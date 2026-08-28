#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, Gtk, Adw, GLib

import webbrowser
import threading
import queue
import time
import sys
import os

from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager
from setzer.dialogs.go_to_page.go_to_page import GoToPageDialog
from setzer.document.preview.magnifier_geometry import (
    compute_magnifier_params,
    compute_magnifier_placement,
)

# 放大镜坐标诊断开关（排查「内容与按下点恒定偏移」）：SETZER_MAGNIFIER_DEBUG=1
# 时，主视图会画出裁剪中心反算回画布坐标的高亮圆点，并打印各段坐标值。
_MAGNIFIER_DEBUG = os.environ.get('SETZER_MAGNIFIER_DEBUG') == '1'


class PreviewController(object):

    def __init__(self, preview, view):
        self.preview = preview
        self.view = view

        self.zoom_buffer = 1
        self.cursor_default = Gdk.Cursor.new_from_name('default')
        self.cursor_pointer = Gdk.Cursor.new_from_name('pointer')
        # Standard GTK cursor; fall back gracefully on themes that do not
        # provide it.  Links retain the pointer cursor and page gaps/default
        # canvas retain the normal cursor.
        self.cursor_magnifier = Gdk.Cursor.new_from_name('zoom-in') or self.cursor_default
        # 普通光标模式（放大镜关闭）下悬停页面用 text 光标提示可拖动选择。
        self.cursor_text = Gdk.Cursor.new_from_name('text') or self.cursor_default
        # 缓存上次的 cursor / link_target：update_cursor 由
        # 滚动 + 鼠标移动每帧触发，原每次都无条件 set_cursor / set_link_target_string。
        # 鼠标在无链接区域移动时三者恒定，却每帧触发 GtkWidget cursor 属性设置 +
        # Gtk.Label set_text（Pango 重排）+ valign 变化（Overlay 重排）。仅在值
        # 变化时设置，将 60 次/秒降为实际跨越链接边界时（典型 0-2 次/秒）。
        self._current_cursor = None
        self._current_link_target = None
        # URI 链接的悬停 tooltip（仅在指向 uri 链接时设置，离开即清空）。
        # 仅变化时设置，避免每帧重置 GTK 内部 tooltip 状态。
        self._current_tooltip = None

        self.view.content.connect('size_changed', self.on_size_change)
        self.view.content.connect('scrolling_offset_changed', self.on_scrolling_offset_change)
        self.view.content.connect('hover_state_changed', self.on_hover_state_change)
        self.view.content.connect('primary_button_press', self.on_primary_button_press)
        self.view.content.connect('primary_button_release', self.on_primary_button_release)
        self.view.content.connect('zoom_request', self.on_zoom_request)
        self.view.content.connect('pinch_zoom', self.on_pinch_zoom)
        self.preview.page_renderer.connect('magnifier_result_ready', self.on_magnifier_result_ready)
        # A held pointer can outlive a rebuild triggered by a new PDF, a
        # rotation/zoom layout rebuild, or recolouring.  Cancel immediately
        # instead of waiting for the next motion event to notice stale state.
        self.preview.connect('pdf_changed', self.on_magnifier_context_changed)
        self.preview.connect('layout_changed', self.on_magnifier_context_changed)
        self.preview.connect('recolor_pdf_changed', self.on_magnifier_context_changed)

        self._pinch_baseline_zoom = None

        # ---- 放大镜（按住左键放大）状态 ----
        # 触发条件：无修饰键左键按下且光标下无链接（链接优先，不破坏既有
        # 跳转工作流）。跟手靠 hover_state_changed + scrolling_offset_changed
        # （按住期间滚轮滚动也要跟着动）。节流：最短入队间隔 + 最小位移，
        # 避免每像素一次渲染。缩放（Ctrl+滚轮 / 捏合）与 layout 重建即取消。
        self._magnifier_active = False
        self._magnifier_layout_ref = None
        self._magnifier_pending_request_id = 0
        self._magnifier_last_enqueue_time = 0.0
        self._magnifier_last_enqueue_pos = None
        # 诊断（SETZER_MAGNIFIER_DEBUG=1）：最近一次入队的裁剪中心反算回
        # 画布坐标，presenter.draw 据此在主视图上画高亮圆点。
        self._magnifier_debug_pos = None
        self._MAGNIFIER_DIAMETER = self.view.magnifier.diameter
        self._MAGNIFIER_MIN_INTERVAL_S = 1.0 / 60.0
        self._MAGNIFIER_MIN_MOVE_PX = 3.0

        # 键盘导航：PgUp/PgDn 翻页、Home/End 首末页、方向键微调、Ctrl+G 跳页。
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.view.add_controller(key_controller)

    def on_size_change(self, *arguments):
        self.preview.zoom_manager.update_dynamic_zoom_levels()
        self.update_cursor()

    def on_scrolling_offset_change(self, *arguments):
        self.preview.update_position()
        self.update_cursor()
        # 放大镜激活期间滚轮滚动：光标视口坐标不变但文档位置变了，
        # 需要重新换算、重渲染并重新定位浮窗。
        if self._magnifier_active:
            self._update_magnifier()
        # 滚动时显示每页页码徽章（在 view 的画布 overlay 中摆真正按钮，
        # 每页一个）。徽章画哪一页由 view 根据传入的 visible_pages 决定，
        # controller 只算「哪些页在视口里」、把结果交给 view 去定位。
        layout = self.preview.layout
        poppler_doc = self.preview.poppler_document
        if layout is not None and poppler_doc is not None:
            visible_pages = self._compute_visible_pages(layout, poppler_doc)
            self.view.show_page_indicator(visible_pages, layout)

    def _compute_visible_pages(self, layout, poppler_doc):
        '''计算当前视口内可见的页（1-based 页码列表）。

        用 layout.get_page_by_offset 把视口顶 / 底各自映射到页号。
        per-page 实现：bisect 在 page_y_starts 上定位,所以即便页面高度
        不一致,「视口底落在哪页」也是准确的。'''
        n_pages = poppler_doc.get_n_pages()
        if n_pages == 0:
            return []
        content = self.view.content
        offset_y = content.scrolling_offset_y
        viewport_h = content.height
        first_page = max(0, layout.get_page_by_offset(offset_y) - 1)
        last_offset = offset_y + viewport_h
        last_page = min(layout.get_page_by_offset(last_offset) - 1, n_pages - 1)
        if last_page < first_page:
            last_page = first_page
        return [p + 1 for p in range(first_page, last_page + 1)]

    def on_zoom_request(self, content, amount):
        # Ctrl+滚轮缩放会重建 layout：放大镜立即取消，避免在旧 layout 上
        # 继续换算坐标。
        if self._magnifier_active:
            self._end_magnifier()
        self.preview.update_position()

        layout = self.preview.layout
        manager = self.preview.zoom_manager

        prev_zoom_level = manager.get_zoom_level()
        zoom_level = self._compute_zoom_level(prev_zoom_level, amount)

        factor = zoom_level / manager.zoom_level
        x = factor * self.view.content.scrolling_offset_x + (factor - 1) * self.view.content.cursor_x
        # per-page：用 layout 找当前页（0-based）。
        prev_pages = max(0, layout.get_page_by_offset(self.view.content.scrolling_offset_y) - 1)
        y = (1 - factor) * prev_pages * layout.page_gap + factor * self.view.content.scrolling_offset_y + (factor - 1) * self.view.content.cursor_y
        # Ctrl+滚轮是手动缩放：脱离任何 fit 模式，保留用户设定的绝对级别。
        manager.zoom_mode = 'manual'
        manager.set_zoom_level(zoom_level)
        self.preview.scroll_to_position(x, y)

    def on_pinch_zoom(self, content, data):
        phase, scale, cx, cy = data

        if phase == 'begin':
            # 捏合缩放优先于放大镜（两指手势与按住拖看互斥）。
            if self._magnifier_active:
                self._end_magnifier()
            if self.preview.layout is None:
                self._pinch_baseline_zoom = None
                return
            self._pinch_baseline_zoom = self.preview.zoom_manager.get_zoom_level()
            self.preview.zoom_manager.zoom_mode = 'manual'
            return

        if phase == 'end':
            self._pinch_baseline_zoom = None
            return

        # phase == 'update'
        if self._pinch_baseline_zoom is None or self.preview.layout is None:
            return
        if scale <= 0:
            return

        target_zoom = self._pinch_baseline_zoom * scale
        if target_zoom < 0.25:
            target_zoom = 0.25
        elif target_zoom > 4.0:
            target_zoom = 4.0

        manager = self.preview.zoom_manager
        prev_zoom = manager.get_zoom_level()
        if prev_zoom is None or prev_zoom == 0:
            prev_zoom = self._pinch_baseline_zoom

        factor = target_zoom / prev_zoom

        x = factor * self.view.content.scrolling_offset_x + (factor - 1) * cx
        layout = self.preview.layout
        # per-page：用 layout 找当前页（0-based）。
        prev_pages = max(0, layout.get_page_by_offset(self.view.content.scrolling_offset_y) - 1)
        y = (1 - factor) * prev_pages * layout.page_gap + factor * self.view.content.scrolling_offset_y + (factor - 1) * cy

        manager.set_zoom_level(target_zoom)
        self.preview.scroll_to_position(x, y)
        self.preview.update_position()

    def _compute_zoom_level(self, prev_zoom_level, amount):
        '''根据缩放量计算目标缩放级别。

        在停靠点（fit-to-width / fit-to-text-width / fit-to-height）附近使用
        zoom_buffer 平滑过渡，避免在停靠点处抖动；跨越停靠点时吸附到停靠点。
        zoom_buffer 作为实例状态在调用间保持，实现「累积缩放」手感。

        参数:
            prev_zoom_level: 当前缩放级别
            amount: 缩放量（正值放大，负值缩小）
        返回:
            目标缩放级别（夹在 [0.25, 4]）
        '''
        manager = self.preview.zoom_manager
        gap = 1.25
        # 停靠点由 update_dynamic_zoom_levels 缓存为 tuple，避免每次缩放都
        # 重建 3 元素列表。tuple 的 `in` 也略快于 list。
        stopping_points = manager._stopping_points

        if prev_zoom_level in stopping_points:
            if amount <= 0:
                self.zoom_buffer *= (1 - amount)
                adj_amount = max(1, self.zoom_buffer / gap)
                zoom_level = min(max(prev_zoom_level * adj_amount, 0.25), 4)
            else:
                self.zoom_buffer *= (1 - amount)
                adj_amount = min(1, self.zoom_buffer * gap)
                zoom_level = min(max(prev_zoom_level * adj_amount, 0.25), 4)
        else:
            zoom_level = min(max(prev_zoom_level * (1 - amount), 0.25), 4)
            if amount <= 0:
                for level in stopping_points:
                    if prev_zoom_level < level and zoom_level >= level:
                        zoom_level = level
                        self.zoom_buffer = 1 / gap
            if amount > 0:
                for level in stopping_points:
                    if prev_zoom_level > level and zoom_level <= level:
                        zoom_level = level
                        self.zoom_buffer = 1 * gap
        return zoom_level

    def on_hover_state_change(self, *arguments):
        self.update_cursor()
        if self._magnifier_active:
            self._update_magnifier()
        self._update_active_text_selection()

    def _update_active_text_selection(self):
        '''拖动选择中：把光标当前位置（视口坐标 + 滚动偏移 = 画布坐标）
        作为 head 更新选择并重绘。由 hover motion 与滚轮滚动共同调用
        （按住拖动途中滚轮滚动，选择也要跟住）。'''
        if not self.preview.text_selection_dragging:
            return
        content = self.view.content
        if content.cursor_x is None or content.cursor_y is None:
            return
        self.preview.update_text_selection(
            content.scrolling_offset_x + content.cursor_x,
            content.scrolling_offset_y + content.cursor_y)
        self.view.content.queue_draw()

    def on_magnifier_context_changed(self, *arguments):
        '''Cancel a held lens as soon as its PDF pixels or layout may change.'''
        self._cancel_magnifier('preview-context-changed')
        # PDF / 布局变化后选择的页码、几何、旋转可能已失效，清除。
        self.preview.clear_text_selection()

    def on_magnifier_setting_changed(self):
        '''工具栏放大镜开关切换（preview.use_magnifier 已更新后调用）。

        关闭：取消激活中的放大镜（若在按住时被关闭，浮窗立即消失）。
        两种方向都重置光标缓存并立即刷新悬停光标——_set_hover_feedback
        有按值缓存，直接调 update_cursor 会因缓存值"未变"而跳过设置，
        光标停留旧状态（如 zoom-in）直到鼠标移动。'''
        if not self.preview.use_magnifier:
            self._cancel_magnifier('magnifier-disabled')
        self._current_cursor = None
        self.update_cursor()

    def _set_hover_feedback(self, cursor, link_target='', tooltip=''):
        '''Apply hover feedback only when values change, including on leave.'''
        if cursor is not self._current_cursor:
            self._current_cursor = cursor
            self.view.set_cursor(cursor)
        if link_target != self._current_link_target:
            self._current_link_target = link_target
            self.view.set_link_target_string(link_target)
        if tooltip != self._current_tooltip:
            self._current_tooltip = tooltip
            self.view.drawing_area.set_tooltip_text(tooltip)

    def _get_link_at(self, x_offset, y_offset):
        '''返回鼠标位置命中的链接 [rect, target, type]，未命中返回 None。

        坐标换算说明见 update_cursor。两个调用点（悬停高亮、点击命中）
        复用同一套「文档偏移 → 页号 → 页内 y-up 坐标 → 命中测试」逻辑，
        避免重复实现导致漂移。
        '''
        if self.preview.layout == None: return None

        window_width = self.view.content.width
        data = self.preview.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data == None: return None

        page_number, x_offset, y_offset = data
        links = self.preview.links_parser.get_links_for_page(page_number)
        # per-page：用该页 height 把 top-down y 转 y-up（与 link y1/y2 一致）。
        y_offset = self.preview.get_page_height(page_number) - y_offset
        for link in links:
            if x_offset > link[0].x1 and x_offset < link[0].x2 and y_offset > link[0].y1 and y_offset < link[0].y2:
                return link
        return None

    def update_cursor(self):
        if self.preview.layout == None:
            self._set_hover_feedback(self.cursor_default)
            return True

        content = self.view.content
        # Motion controllers report None on leave.  Do not map that sentinel to
        # canvas (0, 0), because an action cursor/tooltip could then get stuck.
        if content.cursor_x is None or content.cursor_y is None:
            self._set_hover_feedback(self.cursor_default)
            return True
        x_offset = content.scrolling_offset_x + content.cursor_x
        y_offset = content.scrolling_offset_y + content.cursor_y

        window_width = content.width
        data = self.preview.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data == None:
            self._set_hover_feedback(self.cursor_default)
            return True

        page_number, x_offset, y_offset = data
        # 放大镜开启 → zoom-in 光标；关闭 → text 光标提示可拖动选择文字
        # （悬停在链接上会在下方覆盖为 pointer）。
        cursor = self.cursor_magnifier if self.preview.use_magnifier else self.cursor_text
        link_target = ''
        tooltip = ''
        # per-page：用该页 height 把 top-down y 转 y-up（与 link y1/y2 一致）。
        y_offset = self.preview.get_page_height(page_number) - y_offset
        links = self.preview.links_parser.get_links_for_page(page_number)
        for link in links:
            if x_offset > link[0].x1 and x_offset < link[0].x2 and y_offset > link[0].y1 and y_offset < link[0].y2:
                cursor = self.cursor_pointer
                if link[2] == 'uri':
                    link_target = link[1]
                    # URI 链接需按住 Ctrl 才打开，悬停时提醒用户，避免误触
                    # 直接打开外部 / 本地链接。
                    tooltip = _('Hold Ctrl and click to open this link')
                elif link[2] == 'goto':
                    link_target = _('Go to page ') + str(link[1].page_num)
                break

        self._set_hover_feedback(cursor, link_target, tooltip)

    def on_primary_button_press(self, content, data):
        if self.preview.layout == None: return True

        x_offset, y_offset, state = data

        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0

        if ctrl:
            # Ctrl+点击：若命中 URI 链接则打开（需用户主动按住 Ctrl，
            # 防止误触）；否则保持原有的反向同步（点击 PDF 跳到源位置）。
            link = self._get_link_at(x_offset, y_offset)
            if link is not None and link[2] == 'uri':
                self.open_link(link)
                return True
            self.preview.init_backward_sync(x_offset, y_offset)
            return True

        if state == 0:
            link = self._get_link_at(x_offset, y_offset)
            if link is not None:
                if link[2] == 'goto':
                    self.preview.scroll_dest_on_screen(link[1])
                    return True
                elif link[2] == 'uri':
                    # 普通点击不再直接打开 URI 链接，避免误触。提示用户
                    # 按住 Ctrl 再点击。
                    self._show_ctrl_hint_toast()
                    return True
            else:
                # 无链接、无修饰键的普通左键按下：放大镜开启时进入放大镜
                # 模式（按住显示、松开消失）；关闭时开始拖动选择文字。
                # 链接优先——上面命中链接时绝不激活。state == 0 已保证无
                # Ctrl/Shift 等修饰键。
                if self.preview.use_magnifier:
                    self._begin_magnifier(x_offset, y_offset)
                else:
                    self.preview.begin_text_selection(x_offset, y_offset)
                    self.view.content.queue_draw()
            return True

        return True

    def on_primary_button_release(self, content, data):
        # 无论何种状态松开都结束放大镜：即使因边界条件未激活也是无害 no-op。
        if self._magnifier_active:
            self._end_magnifier()
        # 结束拖动选择：提取文本写入主选择，高亮保留在屏幕上（与 Evince
        # 一致），Ctrl+C 可复制到剪贴板。
        if self.preview.text_selection is not None:
            self.preview.finish_text_selection()
            self.view.content.queue_draw()

    # ---- 放大镜 ----

    def _begin_magnifier(self, doc_x, doc_y):
        '''在文档坐标 (doc_x, doc_y) 处激活放大镜并立即请求首帧渲染。'''
        layout = self.preview.layout
        if layout == None or self.preview.poppler_document == None:
            return
        window_width = self.view.content.width
        data = layout.get_page_number_and_offsets_by_document_offsets(doc_x, doc_y, window_width)
        if data == None:
            # 按在页间 gap / 页边距 / 画布空白处：无内容可放大，不激活。
            return
        page_number, x_pt, y_pt = data
        self._magnifier_active = True
        # 记住 layout 对象引用：后续更新时校验身份，layout 被重建
        # （缩放 / 重编译 / 旋转）即失效取消，避免用过期几何换算。
        self._magnifier_layout_ref = layout
        self._magnifier_last_enqueue_pos = None
        self._update_magnifier(doc_x=doc_x, doc_y=doc_y, page_data=(page_number, x_pt, y_pt))

    def _cancel_magnifier(self, reason):
        '''End a held lens and invalidate all renderer work belonging to it.'''
        if not self._magnifier_active:
            return False
        self._magnifier_active = False
        self._magnifier_layout_ref = None
        self._magnifier_pending_request_id += 1
        self._magnifier_last_enqueue_pos = None
        self._magnifier_debug_pos = None
        self.preview.page_renderer.invalidate_magnifier_requests()
        self.view.magnifier.dismiss()
        self.view.content.queue_draw()
        return True

    def _end_magnifier(self):
        '''End the lens after its primary-button gesture finishes.'''
        return self._cancel_magnifier('primary-button-release')

    def _update_magnifier(self, doc_x=None, doc_y=None, page_data=None):
        '''跟手主循环入口：换算光标文档坐标 → 节流入队渲染 → 定位浮窗。

        doc_x / doc_y 为 None 时从 content.cursor_* + scrolling_offset 现算
        （motion / 滚动路径）；两者须成对提供——press 路径同时传
        doc_x / doc_y 与 page_data（调用方已做好页面映射），缺一会因局部
        变量未绑定在按下瞬间抛 UnboundLocalError。'''
        content = self.view.content
        layout = self.preview.layout
        if not self._magnifier_active or layout == None:
            return
        if layout is not self._magnifier_layout_ref or self.preview.poppler_document == None:
            self._end_magnifier()
            return

        if doc_x is None or doc_y is None:
            cursor_x = content.cursor_x
            cursor_y = content.cursor_y
            if cursor_x == None or cursor_y == None:
                # 光标离开视口（leave 事件）：隐藏浮窗但保持激活，
                # 重新进入后自动恢复。
                self.view.magnifier.dismiss()
                return
            doc_x = content.scrolling_offset_x + cursor_x
            doc_y = content.scrolling_offset_y + cursor_y

        window_width = content.width
        if page_data is None:
            page_data = layout.get_page_number_and_offsets_by_document_offsets(doc_x, doc_y, window_width)
        if page_data == None:
            # 光标移到页间 gap / 边距：无页面内容，隐藏浮窗。
            self.view.magnifier.dismiss()
            return

        page_number, x_pt, y_pt = page_data
        params = compute_magnifier_params(self._MAGNIFIER_DIAMETER, layout.hidpi_factor, layout.scale_factor)

        now = time.monotonic()
        pos = (doc_x, doc_y)
        last_pos = self._magnifier_last_enqueue_pos
        moved = last_pos is None or abs(pos[0] - last_pos[0]) >= self._MAGNIFIER_MIN_MOVE_PX or abs(pos[1] - last_pos[1]) >= self._MAGNIFIER_MIN_MOVE_PX
        if now - self._magnifier_last_enqueue_time >= self._MAGNIFIER_MIN_INTERVAL_S and moved:
            colors = self._magnifier_colors()
            request_id = self.preview.page_renderer.request_magnifier_render(
                page_number, x_pt, y_pt,
                self._MAGNIFIER_DIAMETER,
                layout.hidpi_factor, params['density'], self.preview.rotation, colors)
            if request_id is not None:
                self._magnifier_pending_request_id = request_id
                self._magnifier_last_enqueue_time = now
                self._magnifier_last_enqueue_pos = pos
                if _MAGNIFIER_DEBUG:
                    # 反算回画布坐标：该点若与光标尖不重合，偏差在输入/
                    # 映射段（渲染之前）；重合而镜内内容仍偏，则在渲染/
                    # 浮窗显示段。presenter 在页面绘制之后画双标记：
                    # 红点=反算点（映射+几何），蓝点=原始输入 doc 坐标。
                    back = self.preview.original_to_canvas(page_number, x_pt, y_pt)
                    self._magnifier_debug_pos = {
                        'back': back,
                        'input': (doc_x, doc_y),
                    }
                    print('[magnifier] cursor_doc=({:.1f},{:.1f}) page={} pt=({:.2f},{:.2f}) density={:.1f} back_to_canvas={}'.format(
                        doc_x, doc_y, page_number, x_pt, y_pt, params['density'], back),
                        file=sys.stderr)
            if request_id is not None:
                self._magnifier_pending_request_id = request_id
                self._magnifier_last_enqueue_time = now
                self._magnifier_last_enqueue_pos = pos

        viewport_w = max(content.width, 1)
        viewport_h = max(content.height, 1)
        place_x, place_y = compute_magnifier_placement(
            doc_x, doc_y, self._MAGNIFIER_DIAMETER,
            content.scrolling_offset_x, content.scrolling_offset_y,
            viewport_w, viewport_h)
        self.view.magnifier.present_at(place_x, place_y)
        if _MAGNIFIER_DEBUG:
            alloc = self.view.magnifier.get_allocation()
            print('[magnifier] place=({:.1f},{:.1f}) alloc=x{}+{} y{}+{} w{} h{} viewscale={} layouthidpi={} zoom={}'.format(
                place_x, place_y,
                alloc.x, self.view.magnifier.get_margin_start(),
                alloc.y, self.view.magnifier.get_margin_top(),
                alloc.width, alloc.height,
                self.view.get_scale_factor(), layout.hidpi_factor,
                self.preview.zoom_manager.get_zoom_level()),
                file=sys.stderr)

    def _magnifier_colors(self):
        '''放大镜渲染的反色主题色，与 update_rendered_pages 的取色逻辑一致。'''
        if self.preview.recolor_pdf:
            return (ColorManager.get_ui_color('view_fg_color'), ColorManager.get_ui_color('view_bg_color'))
        return None

    def on_magnifier_result_ready(self, renderer):
        '''渲染线程通知有新结果：排空队列，只采用匹配当前 pending id 的
        surface（旧请求的结果直接丢弃）。'''
        wanted = self._magnifier_pending_request_id
        found = None
        q = self.preview.page_renderer.magnified_pages_queue
        try:
            while True:
                item = q.get_nowait()
                if item['request_id'] == wanted and self._magnifier_active:
                    found = item
        except queue.Empty:
            pass
        if found is not None:
            if _MAGNIFIER_DEBUG:
                print('[magnifier] adopt id={} surface={}x{}'.format(
                    found['request_id'], found['surface'].get_width(),
                    found['surface'].get_height()), file=sys.stderr)
            self.view.magnifier.set_magnified_surface(found['surface'])

    def _show_ctrl_hint_toast(self):
        '''普通点击 URI 链接时，提醒用户需按住 Ctrl 才打开。'''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Hold Ctrl and click to open this link'))
            toast.set_timeout(3)
            main_window.toast_overlay.add_toast(toast)
        return False

    def open_link(self, link):
        if link is None: return
        if link[2] == 'goto':
            self.preview.scroll_dest_on_screen(link[1])
            return
        if link[2] == 'uri':
            url = link[1]
            # 安全：拦截可能执行脚本的 scheme（javascript: / vbscript:
            # / data:），并要求用户确认后再打开本地文件（file://），
            # 防止恶意 PDF 在用户不知情下运行脚本或访问本机文件。
            scheme = ''
            try:
                scheme = (GLib.uri_parse_scheme(url) or '').lower()
            except Exception:
                scheme = ''
            if scheme in ('javascript', 'vbscript', 'data'):
                self._show_blocked_link_toast()
                return
            if scheme == 'file':
                self._confirm_open_file(url)
                return
            # 其它（http(s) / mailto / ftp 等）在后台线程打开，避免
            # 阻塞 GTK 主线程；失败时在主线程弹 toast。
            def _open_url():
                try:
                    webbrowser.open_new_tab(url)
                except Exception:
                    GLib.idle_add(self._show_url_error_toast)
            threading.Thread(target=_open_url, daemon=True).start()

    def _show_url_error_toast(self):
        '''在主线程显示「无法打开链接」toast。

        webbrowser.open_new_tab 在后台线程中调用；失败时通过 GLib.idle_add
        切回主线程显示 toast（GTK 控件非线程安全，不能从后台线程操作）。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Could not open link'))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)
        return False

    def _show_blocked_link_toast(self):
        '''在主线程显示「链接被拦截」toast（危险 scheme）。'''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Link blocked: this link type is unsafe'))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)
        return False

    def _confirm_open_file(self, uri):
        '''打开本地文件（file://）前先征得用户同意。'''
        dialog = Adw.AlertDialog(
            heading=_('Open local file from this PDF?'),
            body=_('This link points to a file on your computer:\n\n{uri}').format(uri=uri))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('open', _('Open'))
        dialog.set_response_appearance('open', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        # 把 uri 作为 user_data 传给回调。
        dialog.choose(main_window, None, self._on_open_file_response, uri)

    def _on_open_file_response(self, dialog, result, uri):
        try:
            dialog.choose_finish(result)
        except Exception:
            return
        if dialog.get_response() == 'open':
            def _open_url():
                try:
                    webbrowser.open_new_tab(uri)
                except Exception:
                    GLib.idle_add(self._show_url_error_toast)
            threading.Thread(target=_open_url, daemon=True).start()

    # ── 键盘导航 ──────────────────────────────────────────────

    def on_key_pressed(self, controller, keyval, keycode, state):
        '''支持标准 PDF 阅读器键盘快捷键：
        PgDn / Space — 下翻一页视口
        PgUp         — 上翻一页视口
        Home         — 跳到首页
        End          — 跳到末页
        ↑ / ↓        — 微调滚动（50px）
        Ctrl+G       — 跳转到指定页面
        '''
        if keyval == Gdk.KEY_Escape:
            if self._magnifier_active:
                self._cancel_magnifier('escape')
                return True
            if self.preview.text_selection is not None:
                self.preview.clear_text_selection()
                return True
        if self.preview.layout == None or self.preview.poppler_document == None:
            return False

        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0

        # Ctrl+C — 把选中文本复制到剪贴板（松开鼠标时已写入主选择）。
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C) and self.preview.text_selection_text:
            Gdk.Display.get_default().get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(self.preview.text_selection_text))
            return True

        # Ctrl+G — 跳转到页面
        if ctrl and keyval in (Gdk.KEY_g, Gdk.KEY_G):
            self._show_goto_page_dialog()
            return True

        # 以下快捷键不含 Ctrl
        if ctrl:
            return False

        content = self.view.content
        cur_y = content.scrolling_offset_y
        viewport_h = content.height
        layout = self.preview.layout
        # 快捷键 PageUp/PageDown/Home/End 是按视口比例滚动（0.9 * 视口高），
        # 不依赖单页尺寸；per-page 不影响。
        total_h = layout.canvas_height
        max_y = max(total_h - viewport_h, 0)

        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_KP_Space):
            new_y = min(cur_y + viewport_h * 0.9, max_y)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            new_y = max(cur_y - viewport_h * 0.9, 0)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self.preview.scroll_to_position(content.scrolling_offset_x, 0)
            return True

        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self.preview.scroll_to_position(content.scrolling_offset_x, max_y)
            return True

        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            new_y = min(cur_y + 50, max_y)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            new_y = max(cur_y - 50, 0)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        return False

    def _show_goto_page_dialog(self):
        '''弹出"跳转到页面"对话框。'''
        n_pages = self.preview.poppler_document.get_n_pages()
        if n_pages < 2:
            return
        main_window = ServiceLocator.get_main_window()
        dialog = GoToPageDialog(main_window)
        dialog.run(n_pages, self._goto_page)

    def _goto_page(self, page_number):
        '''跳转到指定页面（1-based）。'''
        layout = self.preview.layout
        if layout == None:
            return
        content = self.view.content
        # per-page：第 N 页顶部在 page_y_starts[N-1]（已含 vertical_padding）。
        y = layout.get_page_top(page_number - 1)
        if y is None:
            return
        self.preview.scroll_to_position(content.scrolling_offset_x, y)



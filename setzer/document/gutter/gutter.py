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
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo, GtkSource

import math
import cairo

from setzer.helpers.timer import timer
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager
from setzer.app.font_manager import FontManager


class Gutter(object):

    def __init__(self, document, document_view):
        self.document = document
        self.document_view = document_view
        self.source_buffer = document.source_buffer
        self.source_view = document_view.source_view
        self.adjustment = self.document_view.scrolled_window.get_vadjustment()
        self.settings = ServiceLocator.get_settings()

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_halign(Gtk.Align.START)
        self.document_view.overlay.add_overlay(self.drawing_area)
        self.drawing_area.set_draw_func(self.draw)

        self.line_numbers_visible = self.settings.get_value('preferences', 'show_line_numbers')
        self.line_numbers_width = None

        self.code_folding_visible = self.document.is_latex_document() and self.settings.get_value('preferences', 'enable_code_folding')
        self.code_folding_width = None

        self.bookmarks_width = None
        self._bookmark_icon_nodes = dict()

        self.highlight_current_line = self.settings.get_value('preferences', 'highlight_current_line')

        self.char_width = FontManager.get_char_width(self.source_view)
        self.line_height = FontManager.get_line_height(self.source_view)
        self.total_width = None
        self.cursor_x, self.cursor_y = None, None
        self.hovered_folding_region = None
        self._folding_icon_nodes = dict()
        self._newline_icon_nodes = dict()

        # 配色缓存：_get_scheme_colors 原实现每帧（draw）对每个可见行都重新
        # 取 style-scheme 并解析一堆 hex 字符串构造 RGBA，纯属浪费。缓存后在
        # notify::style-scheme 时失效（见 on_scheme_changed）。
        self._scheme_colors_cache = None

        # 字体度量缓存：char_width / line_height 仅在字体实际变化时重算。
        # 原实现每次 update_size 都重建 Pango.Layout 并遍历显示行，而
        # update_size 在每次文本/光标/滚动变化时都被调用——是打字期间
        # 的主要无谓开销之一。
        #
        # 仅比对 font_string 不足以检测字体实际变化：__init__ 在 source_view
        # realize 前就调用了 get_line_height/get_char_width，此时 textview.monospace
        # CSS 尚未应用到 widget 的 pango context，拿到的度量要么为零、要么基于
        # 系统默认字体（如 MiWithJBMonoNL 10pt），而非 CSS 指定的字体。font_string
        # 在此过程中并不变化，缓存永不失效，self.line_height 长期为陈旧值。
        #
        # self.char_width / self.line_height 仅用于字符宽度估算、gutter 总宽
        # 计算等，不参与"行内竖直定位"——竖直定位一律使用 draw() 主循环传入
        # 的真实行高（source_view.get_line_yrange().height，含行距）。
        # 之所以要在 update_size + draw 两处都做 font_description 比对，
        # 是因为 self.line_height 缓存的是 realize 前的字体度量，若首帧 draw
        # 早于任何信号触发 update_size，比对能及时用实际字体重算，避免
        # gutter 总宽/字符宽用陈旧值。
        self._last_font_string = FontManager.font_string
        self._last_actual_font_str = None

        self.layout = Pango.Layout(self.source_view.get_pango_context())
        self.layout.set_alignment(Pango.Alignment.RIGHT)
        # 当前行加粗用的独立 Layout。原先只有这一个共享 Layout，循环里对当前行
        # set_markup('<b>')、对其它行 set_text()，两者复用同一 Layout 在 GTK cairo
        # 重绘时偶发加粗属性泄漏，表现为“光标明明在一行，却有多行号被加粗”。
        # 拆成两个 Layout 后，加粗与普通文本互不干扰，不再串扰。
        self.layout_current = Pango.Layout(self.source_view.get_pango_context())
        self.layout_current.set_alignment(Pango.Alignment.RIGHT)

        # idle 去抖 id：5 路信号（文档变化/光标移动/滚动/折叠状态）共用一次
        # idle 刷新，避免单次按键触发 on_document_change + on_cursor_change
        # 两路各跑一遍 update_hovered_folding_region + update_size + queue_draw。
        self._refresh_idle_id = None

        self.update_size()

        self.settings.connect('settings_changed', self.on_settings_changed)
        # 保存 settings 信号连接的回调引用，shutdown 时据此断开。
        # settings 是进程级单例，若不断开，单例会持续持有 gutter 回调引用，
        # 进而通过 gutter 持有 document，导致文档关闭后无法被 GC，且后续
        # 设置变更会调到已失效的 on_settings_changed。
        self._settings_callback = self.on_settings_changed
        self.document.connect('changed', self.on_document_change)
        self.document.connect('cursor_position_changed', self.on_cursor_change)
        self.document.code_folding.connect('folding_state_changed', self.on_folding_state_changed)
        self.document.bookmarks.connect('bookmarks_changed', self.on_bookmarks_changed)
        self.document.connect('build_diagnostics_changed', self.on_build_diagnostics_changed)
        self.document_view.scrolled_window.get_vadjustment().connect('changed', self.on_adjustment_changed)
        self.document_view.scrolled_window.get_vadjustment().connect('value-changed', self.on_adjustment_value_changed)
        self.source_buffer.connect('notify::style-scheme', self.on_scheme_changed)

        # 注意：gutter 不再挂自己的 EventControllerScroll。编辑器滚动完全由
        # document_controller 的滚动控制器（挂在 scrolled_window 上，CAPTURE
        # 阶段）与 Gtk.ScrolledWindow 原生 kinetic 惯性驱动。gutter 此前额外在
        # overlay 的 drawing_area 上挂第二套滚动控制器，其 on_scroll 里
        # set_kinetic_scrolling(False)+set_value 会打断编辑器的原生惯性，造成
        # 滚动抖动/不跟手。现在改为只跟随 vadjustment 的 value-changed 重绘，
        # 行号栏始终与编辑器同步，滚动交给单一、流畅的原生路径。

        event_controller = Gtk.GestureClick()
        event_controller.connect('pressed', self.on_button_press)
        event_controller.set_button(1)
        self.drawing_area.add_controller(event_controller)

        event_controller = Gtk.EventControllerMotion()
        event_controller.connect('enter', self.on_enter)
        event_controller.connect('motion', self.on_hover)
        event_controller.connect('leave', self.on_leave)
        self.drawing_area.add_controller(event_controller)

        # 悬停诊断行（gutter 色条或整行红/琥珀背景）时显示错误/警告详情。
        # gutter 是覆盖在 source_view 之上的 DrawingArea，只覆盖左侧窄条，
        # 因此两个 widget 都要接 query-tooltip 才能覆盖「标红处」的全部范围。
        self.drawing_area.set_has_tooltip(True)
        self.drawing_area.connect('query-tooltip', self.on_query_tooltip)
        self.source_view.set_has_tooltip(True)
        self.source_view.connect('query-tooltip', self.on_query_tooltip)

        # 离屏缓存：把整个 gutter（背景+行号+折叠+书签+诊断色条）绘到
        # cairo.ImageSurface，覆盖 [surf_top_doc_y, surf_top_doc_y + surf_h]，
        # surf_h = 视口高 + 2*MARGIN 行缓冲。滚动时优先平移旧 surface（命中）或
        # 增量补绘露出带（patch），避免每帧逐行 Pango 重绘。MARGIN 缓冲带保证
        # 小幅滚动不跨带、不重建；跨带时基于「带顶行」基准平移，向上/向下滚动
        # 均正确覆盖，绝不空白（修复旧版 delta==0 顶部空白 bug）。
        self._gutter_cache = None  # (first_line, height, surface_top_doc_y, surface)
        self._GUTTER_MARGIN = 15

    def on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        '''鼠标悬停诊断行时返回该行的错误/警告描述文本。'''
        # gutter 上的 (x, y) 落在左侧窄条，x 对定位行无用：用 source_view
        # 把 (0, y) 映射到该行文本，y 与文本区共享同一竖直坐标系。
        if widget is self.drawing_area:
            found, text_iter = self.source_view.get_iter_at_location(0, y)
        else:
            found, text_iter = self.source_view.get_iter_at_location(x, y)
        if not found:
            return False

        line = text_iter.get_line() + 1
        bd = self.document.build_diagnostics
        error_msgs = bd.error_messages.get(line)
        warning_msgs = bd.warning_messages.get(line)
        if error_msgs is None and warning_msgs is None:
            return False

        # 同行既有错误又有警告时，两类内容一并列出（错误在前、警告在后），
        # 即便编辑器/gutter 因「错误优先」只显红色，气泡也不丢失警告信息。
        parts = []
        if error_msgs:
            parts.append('Error:\n' + '\n'.join(error_msgs))
        if warning_msgs:
            parts.append('Warning:\n' + '\n'.join(warning_msgs))
        tooltip.set_text('\n\n'.join(parts))
        return True

    def shutdown(self):
        """文档关闭时由 Document.shutdown 调用。断开 settings 单例信号连接、
        取消挂起的 idle 回调和减速动画 timeout。

        settings 是进程级单例，不断开会导致单例持续持有 gutter 回调引用，
        进而通过 gutter 持有 document，文档对象无法被 GC 回收，且后续设置
        变更会调到已失效的 on_settings_changed（访问已销毁的 drawing_area）。
        """
        try:
            self.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.document.bookmarks.disconnect('bookmarks_changed', self.on_bookmarks_changed)
        except (TypeError, KeyError, AttributeError):
            pass

        if self._refresh_idle_id is not None:
            GLib.source_remove(self._refresh_idle_id)
            self._refresh_idle_id = None

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        # 行号显隐/当前行高亮/折叠 改变 gutter 静态内容，离屏缓存需失效。
        if item in ('show_line_numbers', 'highlight_current_line', 'enable_code_folding'):
            self._gutter_cache = None

        if item == 'show_line_numbers':
            self.line_numbers_visible = self.settings.get_value('preferences', 'show_line_numbers')
            self.update_hovered_folding_region()
            self.update_size()
            self.drawing_area.queue_draw()

        if item == 'highlight_current_line':
            self.highlight_current_line = self.settings.get_value('preferences', 'highlight_current_line')
            self.drawing_area.queue_draw()

        if item == 'enable_code_folding':
            self.code_folding_visible = self.document.is_latex_document() and self.settings.get_value('preferences', 'enable_code_folding')
            self.update_hovered_folding_region()
            self.update_size()
            self.drawing_area.queue_draw()

    def on_document_change(self, document):
        self._schedule_refresh()

    def on_scheme_changed(self, buffer, pspec):
        self._scheme_colors_cache = None
        self._gutter_cache = None
        self.drawing_area.queue_draw()

    def on_cursor_change(self, document):
        self._schedule_refresh()

    def on_adjustment_value_changed(self, adjustment):
        '''滚动时必须立即重绘 gutter，否则行号/高亮会滞后 source_view 一帧。

        滚动信号频率高且不需要去抖：每次 value-changed 都直接 queue_draw，
        与 source_view 的滚动完全同步。其余信号（文档变化、光标移动等）
        仍走 _schedule_refresh() 的 idle 去抖路径。
        '''
        self.update_hovered_folding_region()
        self.drawing_area.queue_draw()

    def on_adjustment_changed(self, adjustment):
        self._schedule_refresh()

    def on_folding_state_changed(self, code_folding):
        self._schedule_refresh()

    def on_bookmarks_changed(self, bookmarks):
        self._schedule_refresh()

    def on_build_diagnostics_changed(self, document):
        self._schedule_refresh()

    def _schedule_refresh(self):
        '''5 路信号共用一次 idle 刷新。单次按键至少触发 on_document_change +
        on_cursor_change 两路，去抖后只跑一遍 update + queue_draw。'''
        if self._refresh_idle_id is None:
            self._refresh_idle_id = GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self):
        self._refresh_idle_id = None
        # 文档/光标/折叠/书签/诊断变化都使离屏缓存内容过期，必须重建。
        self._gutter_cache = None
        self.update_hovered_folding_region()
        self.update_size()
        self.drawing_area.queue_draw()
        return False

    def on_button_press(self, event_controller, n_press, x, y):
        cursor_area = self.get_cursor_area()
        if cursor_area == 'code_folding' and self.hovered_folding_region != None:
            if self.hovered_folding_region['is_folded']:
                self.document.code_folding.unfold(self.hovered_folding_region)
            else:
                self.document.code_folding.fold(self.hovered_folding_region)
        elif cursor_area == 'bookmarks':
            offset = self.adjustment.get_value()
            line_iter, _ = self.source_view.get_line_at_y(offset + y)
            line = line_iter.get_line()
            self.document.bookmarks.toggle_bookmark(line)
        else:
            offset = self.adjustment.get_value()
            target = self.source_view.get_line_at_y(offset + y).target_iter
            line_number = target.get_line()
            line_start = self.source_buffer.get_iter_at_line(line_number)[1]
            if line_number == self.source_buffer.get_line_count() - 1:
                line_end = self.source_buffer.get_end_iter()
            else:
                line_end = self.source_buffer.get_iter_at_line(line_number + 1)[1]
            self.source_buffer.select_range(line_start, line_end)
        return True

    def on_enter(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_hover(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_leave(self, controller):
        self.set_cursor_position(None, None)

    def set_cursor_position(self, x, y):
        if x != self.cursor_x or y != self.cursor_y:
            self.cursor_x, self.cursor_y = x, y
            self.drawing_area.queue_draw()
        if self.cursor_x != None and self.cursor_x > self.total_width + 1:
            self.drawing_area.set_cursor_from_name('text')
        else:
            self.drawing_area.set_cursor_from_name('default')
        self.update_hovered_folding_region()

    def update_hovered_folding_region(self):
        self.hovered_folding_region = None
        if self.get_cursor_area() == 'code_folding':
            line = self.source_view.get_line_at_y(self.cursor_y + self.adjustment.get_value()).target_iter.get_line()
            self.hovered_folding_region = self.document.code_folding.get_region_by_line(line)

    def _refresh_font_metrics_if_changed(self):
        # 缓存失效检查：除 FontManager.font_string（用户字体/缩放设置）外，
        # 还需比对 source_view 实际生效的 font_description。原因是 Gutter.__init__
        # 在 source_view realize 前就首次取了度量，此时 textview.monospace CSS
        # 尚未应用到 widget 的 pango context，拿到的度量要么为零、要么基于系统
        # 默认字体（如 MiWithJBMonoNL 10pt）而非 CSS 指定的字体（如 monospace
        # 11pt），导致 self.line_height 偏离实际行高。
        #
        # font_string 在此过程中并不变化，仅比对 font_string 无法触发重算。
        # get_font_description / to_string 均为 O(1)，不会成为绘制热点。
        #
        # 该检查在 update_size（idle 去抖路径）和 draw（每帧）中都调用：
        # update_size 依赖信号触发，但 source_view realize 后的首帧 draw 可能
        # 早于任何信号到达（cursor/scroll/change 都尚未发生），此时若不做
        # 检查，self.line_height 仍是 realize 前的陈旧值，draw_folding_region
        # 等用到 self.line_height 的地方会错位。
        font_string = FontManager.font_string
        actual_fd = self.source_view.get_pango_context().get_font_description()
        actual_font_str = actual_fd.to_string() if actual_fd is not None else ''
        if (font_string != self._last_font_string
                or actual_font_str != self._last_actual_font_str):
            self._last_font_string = font_string
            self._last_actual_font_str = actual_font_str
            self.char_width = FontManager.get_char_width(self.source_view)
            self.line_height = FontManager.get_line_height(self.source_view)
            # 图标渲染节点是按尺寸缓存的，字体变化导致尺寸变化需失效重算。
            self._folding_icon_nodes.clear()
            self._newline_icon_nodes.clear()
            self._bookmark_icon_nodes.clear()
            # 离屏缓存的 surface 像素基于旧行高，字体变化必须失效重建，
            # 否则行号/图标按旧行高渲染错位一帧。
            self._gutter_cache = None

    def update_size(self, line_count=None):
        self._refresh_font_metrics_if_changed()
        total_width = 0
        line_numbers_width = 0
        if self.line_numbers_visible:
            if line_count is None:
                line_count = self.source_buffer.get_line_count()
            total_width += int(math.log10(line_count) + 3) * self.char_width
            line_numbers_width = total_width
        if self.code_folding_visible:
            total_width += 3 * self.char_width
            self.code_folding_width = 3 * self.char_width
        else:
            self.code_folding_width = 0

        # Bookmarks area: always visible, provides toggle for bookmark icons.
        # Uses ~1.5 chars width (similar to code folding icon size).
        self.bookmarks_width = max(8, round(self.char_width * 1.5))
        total_width += self.bookmarks_width

        if total_width != self.total_width or line_numbers_width != self.line_numbers_width:
            self.total_width = total_width
            self.line_numbers_width = line_numbers_width
            self.layout.set_width(line_numbers_width * Pango.SCALE)
            self.layout_current.set_width(line_numbers_width * Pango.SCALE)
            # drawing_area 额外覆盖文本区 left_margin，使行高亮延伸到文本区内
            # 在 realize 前 get_left_margin() 可能返回 0，使用 document_view 中的硬编码值
            left_margin = self.source_view.get_left_margin() or 12
            self.drawing_area.set_size_request(total_width + left_margin, -1)
            self.document_view.margin.set_size_request(total_width, -1)

    def presize_for_line_count(self, line_count):
        '''在文本真正填入缓冲区之前，用预估的最终行数预先设定 gutter 宽度。

        大文档加载时 set_text() 会同步阻塞并一次性把行数从 0 推到几千，
        update_size 随后在信号驱动下把宽度从初始窄值跳变到最终值（如 1 位
        → 4 位），视觉上表现为“先窄后突然变宽”。读文件时 text 已可读到，
        line_count = text.count('\\n') + 1 是 O(1) 计数，故可在 set_text 之前
        把宽度预先算好并 set_size_request，使文本填入时宽度已是最终值，
        彻底消除跳变。仅改尺寸、不 queue_draw，避免空白内容被过早绘制。
        '''
        self.update_size(line_count=max(line_count, 1))

    #@timer
    def draw(self, drawing_area, ctx, width, height, data=None):
        if self.total_width == 0: return

        # realize 后首帧可能早于任何信号到达 update_size，此处补一次字体
        # 度量检查，确保用于 gutter 总宽/字符宽的 self.line_height、
        # self.char_width 不会用到 realize 前的陈旧值。O(1) 检查，
        # 仅在字体变化时重算。
        self._refresh_font_metrics_if_changed()

        ctx.save()
        ctx.rectangle(0, 0, width, height)
        ctx.clip()

        viewport_h = self.source_view.get_allocated_height()
        if not viewport_h or viewport_h <= 0:
            viewport_h = height if (height and height > 0) else 600
        scroll_top = self.adjustment.get_value()

        # 用离屏缓存绘制静态内容（背景+行号+折叠+书签+诊断）。命中/增量补绘
        # 避免逐行 Pango 重绘；向上/向下滚动均正确覆盖，绝不空白。
        self._draw_cached_gutter(ctx, int(width), int(viewport_h), scroll_top)

        # 叠加层（不缓存，便宜且频繁变化）：悬停折叠区 + 行号关闭时的当前行高亮。
        self.draw_hovered_folding_region(ctx)
        if not self.line_numbers_visible and self.highlight_current_line and not self.source_buffer.get_has_selection():
            self._draw_current_line_highlight_full(ctx, width)

        ctx.restore()

    def _gutter_surface_top(self, line0_doc_y):
        '''surface 顶文档 y：缓冲带首行的文档 y（已在调用处对齐到行边界）。'''
        return line0_doc_y

    def _draw_cached_gutter(self, ctx, width, height, scroll_top):
        '''离屏缓存绘制。核心：scroll_top 不在缓冲带内时平移旧 surface + 补绘
        露出带；向上/向下滚动都基于「带顶行」基准，保证 delta 跨带时不为 0，
        旧内容平移后正确填满整高，露出带行号数字对齐正确，绝不空白。'''
        if width <= 0 or height <= 0:
            return

        cache = self._gutter_cache
        # 计算缓冲带顶所在行（scroll_top 上移 MARGIN 行），作为 surface 顶基准。
        # 用 get_line_at_y 对齐到显示行边界，确保 surface 顶与行号不出现亚像素错位。
        try:
            margin = self._GUTTER_MARGIN * (self.line_height or 18)
            band_top_iter, _ = self.source_view.get_line_at_y(scroll_top - margin)
            band_line = band_top_iter.get_line()
            # 取该逻辑行首显示行的文档 y 作 surface 顶。
            loc0 = self.source_view.get_iter_location(band_top_iter)
            line0_doc_y = loc0.y
        except Exception:
            line0_doc_y = None

        if line0_doc_y is None:
            # buffer 正被并发修改，取不到带顶：直接逐行降级绘制当前可见区，
            # 不依赖缓存，保证不空白。
            self._paint_line_region(ctx, width, None, scroll_top, height, scroll_top)
            return

        new_surf_top = line0_doc_y
        surface_h = height + 2 * margin

        if cache is not None and cache[1] == height:
            surf_top = cache[2]
            surf_h = cache[3].get_height()
            # 命中：可见区完全落在 surface 内 → 直接平移 paint，O(0.01ms)。
            if scroll_top >= surf_top and scroll_top + height <= surf_top + surf_h:
                ctx.set_source_surface(cache[3], 0, surf_top - scroll_top)
                ctx.paint()
                return

            # 跨出缓冲带：增量补绘。new_surf_top 随 scroll_top 单调，跨带时
            # delta = new_surf_top - surf_top 必不为 0（旧版用 first_line 基准，
            # 向上小幅滚动 first_line 不变导致 delta==0、顶部带没补绘 → 空白）。
            delta = new_surf_top - surf_top
            if abs(delta) <= surf_h:
                try:
                    tmp = cairo.ImageSurface(cairo.Format.ARGB32, int(width), int(surf_h))
                    tctx = cairo.Context(tmp)
                    # 1) 平移旧内容到新位置（保留已画且行号正确的内容）。
                    tctx.set_source_surface(cache[3], 0, delta)
                    tctx.paint()
                    # 2) 补绘露出带。无论向上(delta<0)还是向下(delta>0)，旧内容
                    #    平移后填满整高，但露出带（视口顶超出旧 surface 的部分）
                    #    必须重绘以刷新当前行高亮/诊断色条等随光标变化的状态。
                    #    露出带 = [min(surf_top,new_surf_top), max(surf_top,new_surf_top)]
                    #    即高度 abs(delta) 的条带，位于 new_surf_top 侧（向上滚动）
                    #    或 surf_top 侧（向下滚动）。
                    tctx.save()
                    if delta >= 0:
                        # 向下滚动 delta>0：旧内容上移（平移 +delta），顶部旧内容被裁，
                        # 底部 [surf_top+surf_h, new_surf_top+surf_h] 露出，
                        # 相对 tmp 顶 = surf_h - delta，高度 delta。
                        tctx.rectangle(0, int(surf_h - delta), int(width), int(delta))
                    else:
                        # 向上滚动 delta<0：旧内容下移（平移 delta），底部旧内容被裁，
                        # 顶部 [new_surf_top, surf_top] 露出，相对 tmp 顶 = 0，高度 -delta。
                        tctx.rectangle(0, 0, int(width), int(-delta))
                    tctx.clip()
                    # 从露出带首行起，相对 surface 顶 new_surf_top 绘制，
                    # region 高度传 surf_h 使循环扫到带底（只可见区那几行重绘）。
                    self._paint_line_region(tctx, width, band_line, new_surf_top, surf_h, scroll_top - margin)
                    tctx.restore()
                    self._gutter_cache = (band_line, height, new_surf_top, tmp)
                    ctx.set_source_surface(tmp, 0, new_surf_top - scroll_top)
                    ctx.paint()
                    return
                except Exception:
                    # 增量异常：落整块重建兜底。
                    pass

        # 整块重建（缓存为空 / 尺寸变化 / 跨太多带 / 增量异常）。
        try:
            surface = cairo.ImageSurface(cairo.Format.ARGB32, int(width), int(max(surface_h, 1)))
            # 先存缓存，确保即使绘制中途因 buffer 并发修改抛异常，surface 也已
            # 可用，下一帧平移命中而非无限重建。
            self._gutter_cache = (band_line, height, new_surf_top, surface)
            sctx = cairo.Context(surface)
            self._paint_line_region(sctx, width, band_line, new_surf_top, surface_h, scroll_top - margin)
            ctx.set_source_surface(surface, 0, new_surf_top - scroll_top)
            ctx.paint()
        except Exception:
            # 绘制彻底失败：清空缓存，下次重建，避免缓存到半截 surface。
            self._gutter_cache = None

    def _paint_line_region(self, ctx, width, start_line, surface_top, region_height, scroll_top_hint):
        '''在 ctx（离屏 surface 或主 ctx）上，相对 surface_top 绘制从 start_line
        起的行号/折叠/书签/诊断。surface_top 为该 surface 顶对应的文档 y，
        绘制偏移 = loc.y - surface_top。scroll_top_hint 仅用于跳过视口外的折叠判定，
        保证命中/补绘时行号与当前可见区一致（向上滚动也正确）。'''
        self.draw_background_and_border(ctx, width, int(region_height))
        fg, _ = self._get_scheme_colors()
        Gdk.cairo_set_source_rgba(ctx, fg)

        source_view = self.source_view
        current_line = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()
        try:
            total_lines = self.source_buffer.get_end_iter().get_line()
        except Exception:
            return

        if start_line is None:
            line_iter, _ = source_view.get_line_at_y(scroll_top_hint)
        else:
            try:
                line_iter = self.source_buffer.get_iter_at_line(start_line)[1]
            except Exception:
                return

        bottom = surface_top + region_height
        while True:
            try:
                line = line_iter.get_line()
            except Exception:
                break
            if line > total_lines:
                break

            # 跳过被代码折叠隐藏的逻辑行，避免行号堆叠糊成一片。
            try:
                if line_iter.has_tag(self.document.code_folding.tag):
                    if line >= total_lines:
                        break
                    line_iter = self.source_buffer.get_iter_at_line(line + 1)[1]
                    continue
            except Exception:
                break

            is_current = (current_line == line)
            cur = line_iter.copy()
            first = True
            while True:
                try:
                    loc = source_view.get_iter_location(cur)
                except Exception:
                    break
                drawing_offset = loc.y - surface_top
                row_height = loc.height
                if drawing_offset > bottom:
                    break
                if drawing_offset + row_height >= 0:
                    if first:
                        self.draw_line(ctx, line, is_current, drawing_offset, row_height, width, surface_top)
                    elif self.line_numbers_visible:
                        self.draw_newline_symbol(ctx, drawing_offset, row_height)
                first = False
                if not source_view.forward_display_line(cur):
                    break
                if cur.get_line() != line:
                    break

            if line >= total_lines:
                break
            try:
                line_iter = self.source_buffer.get_iter_at_line(line + 1)[1]
            except Exception:
                break

    def draw_background_and_border(self, ctx, width, height):
        fg, bg = self._get_scheme_colors()
        Gdk.cairo_set_source_rgba(ctx, bg)
        ctx.rectangle(0, 0, width, height)
        ctx.fill()

    def _draw_current_line_highlight_full(self, ctx, width):
        """行号关闭时，绘制覆盖 gutter + left_margin 的当前行高亮。"""
        current_line = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()
        line_start_iter = self.source_buffer.get_iter_at_line(current_line)[1]
        yrange = self.source_view.get_line_yrange(line_start_iter)
        slot_top = yrange.y - self.adjustment.get_value()
        cl_bg = self._get_current_line_bg()
        Gdk.cairo_set_source_rgba(ctx, cl_bg)
        ctx.rectangle(0, slot_top, width, yrange.height)
        ctx.fill()

    def _get_scheme_colors(self):
        # 缓存：原实现每帧（draw）每个可见行都重新取 style-scheme 并解析 hex，
        # 纯属浪费。style-scheme 仅在 notify::style-scheme 时变化，届时在
        # on_scheme_changed 将缓存置 None 失效。
        if self._scheme_colors_cache is not None:
            return self._scheme_colors_cache

        scheme = self.source_buffer.get_style_scheme()
        style = scheme.get_style('text') if scheme else None

        def _parse_hex(s):
            if not s:
                return None
            s = s.strip().lstrip('#')
            if len(s) == 6:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0, alpha=1.0)
            elif len(s) == 8:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0,
                                alpha=int(s[6:8], 16)/255.0)
            return None

        fg = _parse_hex(style.props.foreground) if style else None
        bg = _parse_hex(style.props.background) if style else None
        if fg is None:
            fg = ColorManager.get_ui_color('view_fg_color')
        if bg is None:
            bg = ColorManager.get_ui_color('view_bg_color')
        self._scheme_colors_cache = (fg, bg)
        return fg, bg

    def _get_current_line_bg(self):
        scheme = self.source_buffer.get_style_scheme()
        style = scheme.get_style('current-line') if scheme else None

        def _parse_hex(s):
            if not s:
                return None
            s = s.strip().lstrip('#')
            if len(s) == 6:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0, alpha=1.0)
            elif len(s) == 8:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0,
                                alpha=int(s[6:8], 16)/255.0)
            return None

        cl_bg = _parse_hex(style.props.background) if style else None
        if cl_bg is None:
            cl_bg = ColorManager.get_ui_color('line_highlighting_color')
        return cl_bg

    def draw_line(self, ctx, line, is_current, offset, line_height, width, scroll_base=None):
        if self.line_numbers_visible:
            self.draw_line_number(ctx, line, is_current, offset, line_height, width, scroll_base)

        if self.code_folding_visible:
            self.draw_folding_region(ctx, line, is_current, offset, line_height)

        self.draw_bookmark(ctx, line, offset, line_height)

        # 编译诊断色条（错误强制红、警告琥珀色）绘制在最上层、行号左缘，
        # 不跟随强调色。放在最后以避免被 current-line 背景填充覆盖。
        # 提前短路：绝大多数文档无诊断错误，每帧每可见行都白跑一次
        # get_iter_at_line + get_line_yrange 是浪费。先 O(1) 判断该行是否
        # 真有诊断，没有就跳过整段（如同编辑 LaTeX 源码时没有红色边条）。
        diag = self.document.build_diagnostics
        if (line + 1) in diag.error_lines or (line + 1) in diag.warning_lines:
            self.draw_build_diagnostics(ctx, line, scroll_base)

    def draw_build_diagnostics(self, ctx, line, scroll_base=None):
        error_lines = self.document.build_diagnostics.error_lines
        warning_lines = self.document.build_diagnostics.warning_lines
        if (line + 1) in error_lines:
            color = self.document.build_diagnostics.ERROR_COLOR
        elif (line + 1) in warning_lines:
            color = self.document.build_diagnostics.WARNING_COLOR
        else:
            return

        # 用整条逻辑行的 slot 高度（与 current-line 高亮一致），覆盖自动换行的续行。
        found, line_start_iter = self.source_buffer.get_iter_at_line(line)
        yrange = self.source_view.get_line_yrange(line_start_iter)
        base = scroll_base if scroll_base is not None else self.adjustment.get_value()
        slot_top = yrange.y - base
        bar_height = yrange.height
        ctx.rectangle(0, slot_top, 3, bar_height)
        Gdk.cairo_set_source_rgba(ctx, color)
        ctx.fill()

    def draw_line_number(self, ctx, line, is_current, offset, line_height, width, scroll_base=None):
        fg, bg = self._get_scheme_colors()

        if is_current:
            text = '<b>' + str(line + 1) + '</b>'
        else:
            text = str(line + 1)

        # 行高亮（背景色）仅在无文本选区时绘制，与 GtkSourceView 的
        # current-line-highlighting 在选区时自动取消的行为保持一致。
        # 行号加粗（上面的 <b>）不受此影响，光标行始终加粗。
        if is_current and self.highlight_current_line and not self.source_buffer.get_has_selection():
            cl_bg = self._get_current_line_bg()
            Gdk.cairo_set_source_rgba(ctx, cl_bg)
            # 高亮必须覆盖整行 slot（含 pixels_above/below_lines 行距），
            # 与 GtkSourceView 自身的 current-line 高亮一致。
            # 关键：slot 顶部要用 get_line_yrange().y，而非传入的 offset。
            # offset 来自 get_iter_location().y（文本区顶部）；当
            # pixels_above_lines > 0（行距均分到上下使文本居中）时，文本区
            # 顶部比 slot 顶部低 pixels_above_lines 像素。若用 offset 作高亮
            # 顶部，高亮条会比 GtkSourceView 的高亮低 pixels_above_lines 像素，
            # 顶部对不齐、还会溢出到下一行。get_line_yrange().y 是 slot 真正
            # 顶部，用它作顶、.height 作高才能精确覆盖整个 slot。换行（wrapped）
            # 行的 yrange 跨所有视觉续行，高亮随之覆盖整条逻辑行。
            line_start_iter = self.source_buffer.get_iter_at_line(line)[1]
            yrange = self.source_view.get_line_yrange(line_start_iter)
            base = scroll_base if scroll_base is not None else self.adjustment.get_value()
            slot_top = yrange.y - base
            ctx.rectangle(0, slot_top, width, yrange.height)
            ctx.fill()
            Gdk.cairo_set_source_rgba(ctx, fg)

        # 非当前行用普通 Layout + set_text；当前行用独立的 layout_current +
        # set_markup('<b>')。两个 Layout 互不复用，避免加粗属性泄漏到邻近行。
        if is_current:
            self.layout_current.set_markup(text)
            layout = self.layout_current
        else:
            self.layout.set_text(text, -1)
            layout = self.layout

        # 行号颜色：与源码同用 scheme 的 text foreground 色。
        Gdk.cairo_set_source_rgba(ctx, fg)

        # 真正的行内垂直居中：用 layout 自身的 logical_rect 度量来居中，
        # 而非混用 font metrics 的 line_height，避免两套 Pango 计算路径
        # 的亚像素差异导致的偏移。text_height 与 line_height 都来自同一
        # layout 渲染路径，居中结果稳定。
        text_rect = layout.get_extents().logical_rect
        text_height = text_rect.height / Pango.SCALE
        vertical_offset = (line_height - text_height) / 2
        ctx.move_to(0, offset + vertical_offset)

        PangoCairo.show_layout(ctx, layout)

    def _get_folding_icon_node(self, icon_name, size):
        # 把系统图标（symbolic）渲染成 Gsk.RenderNode 并缓存，避免每帧重复
        # lookup + snapshot。size 跟随字符宽度变化，按 (名称, 尺寸) 缓存即可。
        key = (icon_name, size)
        node = self._folding_icon_nodes.get(key)
        if node is not None:
            return node
        theme = Gtk.IconTheme.get_for_display(self.source_view.get_display())
        paintable = theme.lookup_icon(icon_name, None, size, 1,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, size, size)
        node = snapshot.to_node()
        self._folding_icon_nodes[key] = node
        return node

    def _get_newline_icon_node(self, size):
        # 自动换行产生的视觉续行：在续行首位置绘制换行符号
        # (newline-symbolic.svg)，而非行号。IconTheme 在 setzer.in 入口已
        # 把 resources/icons 加入搜索路径，且 SVG 使用 currentColor，故
        # lookup_icon 会按 symbolic 规范自动用主题前景色着色，与折叠图标
        # 行为一致。按 (尺寸, 设备缩放) 缓存成 Gsk.RenderNode。
        # 注：Gsk.RenderNode.draw 在真实 draw 上下文里遵守 cairo 的
        # translate/scale（已用真实渲染截图验证定位正确），无需手工烘焙变换。
        # lookup_icon 的 scale 必须用控件真实设备缩放倍数，否则在 HiDPI/
        # 分数缩放下纹理只按 1x 渲染、被 cairo 放大绘制而发虚。
        scale_factor = self.source_view.get_scale_factor()
        key = (size, scale_factor)
        node = self._newline_icon_nodes.get(key)
        if node is not None:
            return node
        theme = Gtk.IconTheme.get_for_display(self.source_view.get_display())
        paintable = theme.lookup_icon('newline-symbolic', None, size, scale_factor,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
        if paintable is None:
            return None
        # 以设备像素分辨率渲染纹理（size * scale_factor），绘制时 cairo
        # 上下文已含 scale，1:1 绘制即对齐设备像素，保持清晰。
        pixel_size = size * scale_factor
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, pixel_size, pixel_size)
        node = snapshot.to_node()
        self._newline_icon_nodes[key] = node
        return node

    def draw_newline_symbol(self, ctx, offset, line_height):
        # 图标大小参考单行行高（line_height），取其中一部分，确保落在
        # 该行内、竖直居中；横向与数字行号右边缘对齐。
        size = max(6, round(line_height * 0.3))
        node = self._get_newline_icon_node(size)
        if node is None:
            return
        # 数字通过 RIGHT 对齐绘制在 (line_numbers_width - char_width) 宽度的
        # layout 上，故其右边缘实际落在 line_numbers_width - char_width 处。
        # 符号右边缘需减去同样的 char_width，才能与数字右边缘真正对齐。
        # 留出半个字符间距，让符号与数字不那么贴边。
        x = round(self.line_numbers_width - self.char_width - size)
        y = round(offset + (line_height - size) / 3)
        ctx.save()
        ctx.translate(x, y)
        node.draw(ctx)
        ctx.restore()

    def draw_folding_region(self, ctx, line, is_current, offset, line_height):
        folding_region = self.document.code_folding.get_region_by_line(line)
        if folding_region == None: return

        cw = self.char_width
        lnw = self.line_numbers_width
        # 用 draw() 主循环传来的真实行高（含行距，来自 get_line_yrange），
        # 而非 self.line_height（ascent+descent，不含行距）。两者不等时
        # 用后者做居中会把图标算得偏下。
        lh = line_height

        # 用系统自带 symbolic 箭头替换原先手绘的三角形：
        #   is_folded=True  → 区域被折叠、"可展开"，显示右指箭头 pan-end-symbolic
        #   is_folded=False → 区域已展开，显示下指箭头 pan-down-symbolic
        # 颜色随主题（symbolic 默认前景色），与系统其它控件风格一致。
        icon_name = 'pan-end-symbolic' if folding_region['is_folded'] else 'pan-down-symbolic'
        size = max(8, round(cw * 1.5))
        node = self._get_folding_icon_node(icon_name, size)
        if node is not None:
            # 行高方向居中：图标中心对齐到行中心（offset + lh/2）。
            # 用 round 对齐到整数像素，避免半像素 translate 被 cairo 四舍五入
            # 到像素网格，导致图标视觉上偏上/偏下、看起来不居中。
            x = round(lnw + (self.code_folding_width - size) / 2)
            y = round(offset + (lh - size) / 2)
            ctx.save()
            ctx.translate(x, y)
            node.draw(ctx)
            ctx.restore()

    def draw_bookmark(self, ctx, line, offset, line_height):
        """Draw bookmark icon on lines that have bookmarks."""
        if not self.document.bookmarks.has_bookmark(line):
            return

        icon_name = 'bookmark-filled-symbolic'
        size = self.bookmarks_width
        node = self._get_bookmark_icon_node(icon_name, size)
        if node is None:
            # Fallback to outline icon if filled version is not available
            node = self._get_bookmark_icon_node('bookmark-new-symbolic', size)
        if node is not None:
            # Position: right after code_folding area (or line numbers if no folding)
            x_offset = self.line_numbers_width
            if self.code_folding_visible:
                x_offset += self.code_folding_width
            x_offset += (self.bookmarks_width - size) / 2
            y_offset = offset + (line_height - size) / 2
            ctx.save()
            ctx.translate(round(x_offset), round(y_offset))
            node.draw(ctx)
            ctx.restore()

    def _get_bookmark_icon_node(self, icon_name, size):
        """Render a bookmark icon as a cached Gsk.RenderNode."""
        key = (icon_name, size)
        node = self._bookmark_icon_nodes.get(key)
        if node is not None:
            return node
        theme = Gtk.IconTheme.get_for_display(self.source_view.get_display())
        paintable = theme.lookup_icon(icon_name, None, size, 1,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
        if paintable is None:
            return None
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, size, size)
        node = snapshot.to_node()
        self._bookmark_icon_nodes[key] = node
        return node

    def draw_hovered_folding_region(self, ctx):
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('code_folding_hover'))
        if self.hovered_folding_region != None:
            region = self.hovered_folding_region
            yrange_1 = self.source_view.get_line_yrange(self.source_buffer.get_iter_at_line(region['starting_line']).iter)
            yrange_2 = self.source_view.get_line_yrange(self.source_buffer.get_iter_at_line(region['ending_line']).iter)

            ctx.rectangle(self.total_width - 1, yrange_1.y - self.adjustment.get_value(), 3, yrange_2.y - yrange_1.y + yrange_2.height)
            ctx.fill()

    def get_cursor_area(self):
        if self.cursor_x == None: return None
        offset = 0

        if self.line_numbers_visible:
            offset += self.line_numbers_width
        if self.cursor_x <= offset: return 'line_numbers'

        if self.code_folding_visible:
            offset += self.code_folding_width
        if self.cursor_x <= offset: return 'code_folding'

        if self.bookmarks_width:
            offset += self.bookmarks_width
        if self.cursor_x <= offset: return 'bookmarks'

        return None

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
from gi.repository import GObject, Gdk, Gtk
import cairo

import os.path
import math
import time

from setzer.app.color_manager import ColorManager
from setzer.helpers.timer import timer


# synctex 正向同步高亮（编辑器 → PDF 预览）的最大 alpha。
# accent_color 全不透明(1.0)叠加 cairo.Operator.MULTIPLY 在白底 PDF 上
# 等于直接铺一块实色 accent，过于浓重——比行高亮还要浓一个量级。
# 0.30 在 MULTIPLY 模式下可见性已经足够定位（同时仍能透过看到下面的文字），
# 浓度大致与编辑器侧 begin/end_match (0.20) / section highlight (0.20)
# 同一量级，符合「比行高亮浓厚一点点」的总体风格。若想再淡/再浓，
# 改这个常量即可。
_SYNCTEX_HIGHLIGHT_MAX_ALPHA = 0.30


class PreviewPresenter(object):

    def __init__(self, preview, page_renderer, view):
        self.preview = preview
        self.page_renderer = page_renderer
        self.view = view

        self.highlight_duration = 1.5
        self.count = 1
        self._fade_loop_id = None
        self._current_time_factor = 0.0

        self._build_in_progress = False
        self._last_build_result = None

        self.view.drawing_area.set_draw_func(self.draw)

        self.preview.connect('pdf_changed', self.on_pdf_changed)
        self.preview.connect('layout_changed', self.on_layout_changed)
        self.preview.connect('pdf_load_failed', self.on_pdf_load_failed)
        self.preview.connect('external_pdf_state_changed', self.on_external_pdf_state_changed)
        self.view.set_external_pdf_reload_handler(self.on_external_pdf_reload_requested)
        self.page_renderer.connect('rendered_pages_changed', self.on_rendered_pages_changed)

        # 注册页码徽章按钮的点击回调。徽章现在是真正的 Gtk.Button（在
        # view 的画布 overlay 里），点击 → 滚动到该页顶部。
        self.view.set_page_indicator_click_handler(self._on_page_indicator_clicked)

        build_system = self.preview.document.build_system
        build_system.connect('build_state_change', self.on_build_state_change)
        build_system.connect('build_state', self.on_build_state)

        self.show_blank_slate()

    def _on_page_indicator_clicked(self, page_number_1based):
        '''用户点击页码徽章：把该页滚到视口顶部（保持当前 x 偏移）。

        滚动到 page_y_starts[page-1]（per-page 几何，已含 vertical_padding）。
        不调 set_synctex_rectangles / start_fade_loop 等额外动作——点击是
        单纯的位置跳转，不涉及 synctex。'''
        layout = self.preview.layout
        if layout is None:
            return
        page_top = layout.get_page_top(page_number_1based - 1)
        if page_top is None:
            return
        content = self.view.content
        self.preview.scroll_to_position(content.scrolling_offset_x, page_top)

    def on_pdf_changed(self, preview):
        if self.preview.poppler_document != None:
            # 成功加载新 PDF 时清除上次的构建失败提示。
            self.view.hide_pdf_load_failed()
            self.show_pdf()
        else:
            # 回到空白状态（无旧 PDF 可回退）时也清除失败提示。
            self.view.hide_pdf_load_failed()
            self.show_blank_slate()
        # 文档切换 / PDF 重置:徽章失去意义,取消挂起的 hide 定时器
        # 并立即隐藏(下一帧 draw 不再画徽章)。
        self.view.cancel_page_indicator_timer()
        if self.view.is_page_indicator_visible():
            self.view._hide_page_indicator()

    def on_pdf_load_failed(self, preview):
        # 新 PDF 加载失败，回退到旧 PDF：显示错误图标，弹出 toast 告知用户。
        self.view.show_pdf_load_failed()

    def on_external_pdf_state_changed(self, preview, state):
        self.view.set_external_pdf_state(state)

    def on_external_pdf_reload_requested(self):
        self.preview.reload_external_pdf()

    def on_build_state_change(self, build_system, state):
        if state == 'building_in_progress':
            self._build_in_progress = True
            self._last_build_result = None
            if self.view.stack.get_visible_child_name() == 'blank_slate':
                self.view.blank_slate.set_state('building')
        elif state == 'idle':
            self._build_in_progress = False
            # 修复 spinner 卡在 'building' 的 bug：
            # parse_result 内执行顺序为 add_change_code('pdf_updated') → load_pdf
            # → on_pdf_changed → show_blank_slate()，此时 change_build_state('idle')
            # 还没跑，_build_in_progress 仍为 True，show_blank_slate 会把 spinner
            # 设成 'building'；随后 change_build_state('idle') 仅置标志位，没有
            # 通知 blank_slate 重绘，导致 spinner 永远停留。这里在 idle 时若
            # blank_slate 仍可见，按 _last_build_result 重算状态并切换。
            if self.view.stack.get_visible_child_name() == 'blank_slate':
                self.show_blank_slate()

    def on_build_state(self, build_system, message):
        if message in ('error', 'success'):
            self._last_build_result = message

    def on_layout_changed(self, preview):
        if self.preview.layout != None:
            # Size the canvas-sized drawing area to the full PDF canvas; the
            # Gtk.ScrolledWindow derives its adjustment uppers from this and
            # provides the overlay scrollbars/viewport clipping.
            self.view.drawing_area.set_content_width(self.preview.layout.canvas_width)
            self.view.drawing_area.set_content_height(self.preview.layout.canvas_height)
            self.view.content.queue_draw()

    def on_rendered_pages_changed(self, page_renderer):
        self.view.drawing_area.queue_draw()

    def show_blank_slate(self):
        if self._last_build_result == 'error':
            self.view.blank_slate.set_state('build_failed')
        elif self._build_in_progress:
            self.view.blank_slate.set_state('building')
        else:
            self.view.blank_slate.set_state('never_built')
        self.view.stack.set_visible_child_name('blank_slate')

    def show_pdf(self):
        self.view.stack.set_visible_child_name('pdf')
        self.view.drawing_area.queue_draw()

    def start_fade_loop(self):
        # 取消进行中的淡出动画，避免连续 sync 叠加多个 timeout 同时 queue_draw。
        self.cancel_fade_loop()
        self._current_time_factor = 1.0

        def fade_tick():
            elapsed = time.time() - self.preview.visible_synctex_rectangles_time
            remaining = self.highlight_duration + 0.25 - elapsed
            if remaining <= 0:
                # 淡出结束：在 fade loop 中统一清理，避免在 draw 回调里修改状态。
                self.preview.set_synctex_rectangles(list())
                self._fade_loop_id = None
                self._current_time_factor = 0
                self.view.drawing_area.queue_draw()
                return False

            # 将剩余时间映射到 0~1 范围：最后 0.25s 完成淡出
            time_factor = self.ease(min(remaining, 0.25) * 4)
            self._current_time_factor = time_factor
            self.view.drawing_area.queue_draw()
            return True

        self.view.drawing_area.queue_draw()
        self._fade_loop_id = GObject.timeout_add(30, fade_tick)

    def cancel_fade_loop(self):
        '''取消挂起的 synctex 高亮淡出动画。由 preview.shutdown 调用，
        避免文档关闭后回调继续访问已销毁的 drawing_area。'''
        if self._fade_loop_id is not None:
            GObject.source_remove(self._fade_loop_id)
            self._fade_loop_id = None
        self._current_time_factor = 0

    #@timer
    def draw(self, drawing_area, ctx, width, height):
        if self.preview.layout == None:
            self.preview.setup_layout_and_zoom_levels()
            return

        # 一帧内颜色不变（主题切换是低频事件），一次性取色传给各子函数。
        # 原实现每页都调 ColorManager.get_ui_color：5 可见页 × (borders + view_bg)
        # × 60fps ≈ 600 次/秒 Python→C 边界调用 + 字典查找。提升到 draw 入口
        # 后降为每帧 2 次。synctex 颜色仅在有高亮矩形时才取（else None），
        # 保持原「无高亮时零取色」行为。
        border_color = ColorManager.get_ui_color('borders')
        # 画布（"桌面"）背景始终跟随视图背景色，与 PDF 页面颜色解耦。
        # 原实现让画布跟随页面底色（recolor 用 view_bg，否则纯白），导致
        # 深色模式下画布与纸张同色融合，看不到"背景板"层次——白色纸张在
        # 深色窗口里应浮在深色桌面之上，而非与桌面融为一体。
        canvas_bg_color = ColorManager.get_ui_color('view_bg_color')
        # 页面（纸张）底色：recolor 时跟随视图背景（匹配反色后页面的深色背景），
        # 否则纯白（None 让子函数走 set_source_rgba(1,1,1,1)）。
        if self.preview.recolor_pdf:
            page_bg_color = canvas_bg_color
        else:
            page_bg_color = None
        synctex_color = ColorManager.get_ui_color('highlight_tag_preview') if self.preview.visible_synctex_rectangles else None

        self.draw_background(ctx, drawing_area, canvas_bg_color)

        # 缓存 layout 引用到局部变量：draw 每帧调用，原代码在循环体内多次
        # 经 self.preview.layout.xxx 两级属性链查找（每级 __dict__ 哈希）。
        # 提到局部变量后走 LOAD_FAST，对 5+ 可见页 × 多次属性访问累积省可观。
        layout = self.preview.layout
        # 兼容旧 draw 路径：page_height / page_width 仍取单值（旧 draw_page_
        # background_and_outline 用）；新 per-page 循环用 page_heights / page_y_starts。
        page_gap = layout.page_gap
        # ``width``/``height`` are the full canvas size now; the visible
        # viewport size is read from the ScrolledWindow adjustments.
        visible_width = self.view.content.adjustment_x.get_page_size()
        visible_height = self.view.content.adjustment_y.get_page_size()
        margin = layout.get_horizontal_margin(visible_width)
        scrolling_offset_y = self.view.content.scrolling_offset_y
        # per-page：直接用 layout.get_page_by_offset 找首页 0-based。
        first_page = max(0, layout.get_page_by_offset(scrolling_offset_y) - 1)
        # 末页：找"视口底"所在的页（clamp 到 n-1）。
        last_offset = scrolling_offset_y + visible_height
        n_pages = self.preview.poppler_document.get_n_pages()
        last_page = min(layout.get_page_by_offset(last_offset) - 1, n_pages - 1)
        if last_page < first_page:
            last_page = first_page
        # 第一页 transform 起点 = page_y_starts[first_page]。后续每页用
        # ctx.transform(0, page_heights[i] + gap) 推进，而非 page_step。
        first_page_top = layout.get_page_top(first_page)
        if first_page_top is None:
            return
        ctx.transform(cairo.Matrix(1, 0, 0, 1, margin, first_page_top))

        rotation = self.preview.rotation
        page_heights = layout.page_heights
        for page_number in range(first_page, last_page + 1):
            # per-page：取该页的宽 / 高（旋转后）传给 draw_page_background。
            # 旧逻辑用单一 layout.page_width / page_height，假定等高。
            page_w_px, page_h_px = layout.page_width, page_heights[page_number]
            self.draw_page_background_and_outline(ctx, layout, border_color, page_bg_color,
                                                  page_w_px, page_h_px)
            if rotation != 0:
                # Draw the un-rotated texture (and synctex highlight) inside a
                # rotation transform so it appears rotated within the page box.
                ctx.save()
                # 旋转中心用当前页的 displayed 中心（per-page h 已变化）。
                ctx.translate(page_w_px / 2.0, page_h_px / 2.0)
                ctx.rotate(math.radians(rotation))
                ctx.translate(-layout.page_width_original / 2.0, -layout.page_height_original / 2.0)
                self.draw_rendered_page(ctx, page_number, layout)
                self.draw_synctex_rectangles(ctx, page_number, synctex_color)
                ctx.restore()
            else:
                self.draw_rendered_page(ctx, page_number, layout)
                self.draw_synctex_rectangles(ctx, page_number, synctex_color)

            # per-page advance：每页实际高 + gap（而非统一 page_step）。
            ctx.transform(cairo.Matrix(1, 0, 0, 1, 0, page_heights[page_number] + page_gap))

    def draw_background(self, ctx, drawing_area, bg_color):
        # 画布（"桌面"）背景始终跟随视图背景色（view_bg_color），与页面
        # 颜色解耦：浅色模式画布为浅灰、深色模式为深色，纸张（白或反色后
        # 深色）浮于其上，形成"纸在桌面上"的视觉层次。
        ctx.rectangle(0, 0, drawing_area.get_allocated_width(), drawing_area.get_allocated_height())
        ctx.set_source_rgba(bg_color.red, bg_color.green, bg_color.blue, bg_color.alpha)
        ctx.fill()

    #@timer
    def draw_page_background_and_outline(self, ctx, layout, border_color, bg_color,
                                          page_w_px, page_h_px):
        # layout / 颜色由 draw 传入（已缓存为局部变量 / 一帧取色一次），
        # 避免每页 4+ 次 self.preview.layout.xxx 两级属性链查找 + 重复取色。
        # 边距：每页实际尺寸（per-page）由 draw 传入；layout.page_width /
        # page_height 仍保留作为单值兜底，但新路径优先用入参。
        border_width = layout.border_width
        page_width = page_w_px
        page_height = page_h_px
        ctx.set_source_rgba(border_color.red, border_color.green, border_color.blue, border_color.alpha)
        ctx.rectangle(- border_width, - border_width, page_width + 2 * border_width, page_height + 2 * border_width)
        ctx.fill()

        if bg_color is not None:
            ctx.set_source_rgba(bg_color.red, bg_color.green, bg_color.blue, bg_color.alpha)
        else:
            ctx.set_source_rgba(1, 1, 1, 1)
        ctx.rectangle(0, 0, page_width, page_height)
        ctx.fill()

    def draw_rendered_page(self, ctx, page_number, layout):
        # .get() 替代 `in` + `[]` 两次字典查找；layout 由 draw 传入。
        rendered_page_data = self.page_renderer.rendered_pages.get(page_number)
        if rendered_page_data is None: return

        surface = rendered_page_data[0]
        if not isinstance(surface, cairo.ImageSurface): return

        # rendered_page_data[1] / [2] are the un-rotated CSS page dimensions.
        page_width_css = rendered_page_data[1]
        page_height_css = rendered_page_data[2]
        device_w = page_width_css * layout.hidpi_factor
        device_h = page_height_css * layout.hidpi_factor

        # The context is already rotated (if needed), so we draw the un-rotated
        # page texture at its natural CSS size: scale the device-px surface down
        # to CSS px and fill the page rectangle.
        matrix = ctx.get_matrix()
        ctx.scale(1.0 / layout.hidpi_factor, 1.0 / layout.hidpi_factor)
        ctx.set_source_surface(surface, 0, 0)
        ctx.rectangle(0, 0, device_w, device_h)
        ctx.fill()
        ctx.set_matrix(matrix)

    def draw_synctex_rectangles(self, ctx, page_number, synctex_color):
        if self._current_time_factor <= 0:
            return
        try:
            rectangles = self.preview.layout.visible_synctex_rectangles[page_number]
        except KeyError:
            return
        if synctex_color is not None:
            # 不原地修改 synctex_color.alpha：ColorManager 返回的对象可能被缓存，
            # 原代码 color.alpha *= time_factor 会污染缓存，使后续取色（含本帧
            # 其它页与后续帧）拿到不断衰减的 alpha，高亮颜色越来越淡甚至归零。
            # 直接在 set_source_rgba 中计算乘积，零副作用。
            #
            # 降 alpha：accent 全不透明(1.0) + MULTIPLY 等于铺实色块，太浓重。
            # 改为 _SYNCTEX_HIGHLIGHT_MAX_ALPHA 封顶，再乘 time_factor 做淡出。
            ctx.set_source_rgba(synctex_color.red, synctex_color.green, synctex_color.blue,
                                _SYNCTEX_HIGHLIGHT_MAX_ALPHA * self._current_time_factor)
            ctx.set_operator(cairo.Operator.MULTIPLY)
            for rectangle in rectangles:
                ctx.rectangle(rectangle.x, rectangle.y, rectangle.width, rectangle.height)
            ctx.fill()
            ctx.set_operator(cairo.Operator.OVER)

    def ease(self, factor): return (factor - 1)**3 + 1

    # --- Context-menu actions -------------------------------------------------

    def _render_page_surface(self, page_number, scale=None):
        '''Render a single page to a cairo surface at the given scale.

        Honors the current preview rotation. Used by "Copy Image" and
        "Save Image As".
        '''
        doc = self.preview.poppler_document
        if doc is None: return (None, 0, 0)
        page = doc.get_page(page_number)
        pw, ph = page.get_size()
        rotation = self.preview.rotation
        if rotation in (90, 270):
            sw, sh = ph, pw
        else:
            sw, sh = pw, ph
        if scale is None:
            layout_scale = self.preview.layout.scale_factor if self.preview.layout is not None else 1.0
            scale = max(layout_scale, 2.0)
        surf_w = max(1, int(round(sw * scale)))
        surf_h = max(1, int(round(sh * scale)))
        surface = cairo.ImageSurface(cairo.Format.ARGB32, surf_w, surf_h)
        ctx = cairo.Context(surface)
        if rotation == 0:
            ctx.scale(scale, scale)
            page.render(ctx)
        else:
            ctx.translate(surf_w / 2.0, surf_h / 2.0)
            ctx.rotate(math.radians(rotation))
            ctx.translate(-pw * scale / 2.0, -ph * scale / 2.0)
            ctx.scale(scale, scale)
            page.render(ctx)
        return (surface, surf_w, surf_h)

    def copy_page_text(self, page_number):
        doc = self.preview.poppler_document
        if doc is None: return
        text = doc.get_page(page_number).get_text()
        if text:
            # GTK4 移除了 Gdk.Clipboard.set_text()，改用 set_content + ContentProvider。
            # 与下方 copy_page_image 的 new_for_pixbuf 同范式。
            Gdk.Display.get_default().get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))

    def copy_page_image(self, page_number):
        surface, w, h = self._render_page_surface(page_number)
        if surface is None: return
        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)
        if pixbuf is None: return
        Gdk.Display.get_default().get_clipboard().set_content(Gdk.ContentProvider.new_for_pixbuf(pixbuf))

    def save_page_image(self, page_number):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Save Image As'))
        dialog.set_initial_name('page-{:03d}.png'.format(page_number + 1))
        file_filter = Gtk.FileFilter()
        file_filter.set_name('PNG')
        file_filter.add_mime_type('image/png')
        dialog.set_default_filter(file_filter)
        window = self.view.get_root()
        dialog.save(window, None, self._on_save_image_response, page_number)

    def _on_save_image_response(self, dialog, result, page_number):
        try:
            file = dialog.save_finish(result)
        except Exception:
            return
        if file is None: return
        path = file.get_path()
        if path is None: return
        surface, w, h = self._render_page_surface(page_number)
        if surface is None: return
        try:
            surface.write_to_png(path)
        except Exception:
            pass

    def print_pdf(self):
        doc = self.preview.poppler_document
        if doc is None: return
        window = self.view.get_root()
        operation = Gtk.PrintOperation()
        operation.set_n_pages(doc.get_n_pages())
        operation.connect('draw-page', self._print_draw_page)
        operation.run(Gtk.PrintOperationAction.PRINT_DIALOG, window)

    def _print_draw_page(self, operation, context, page_num):
        doc = self.preview.poppler_document
        page = doc.get_page(page_num)
        cr = context.get_cairo_context()
        w, h = page.get_size()
        pw = context.get_width()
        ph = context.get_height()
        scale = min(pw / w, ph / h)
        cr.scale(scale, scale)
        page.render(cr)



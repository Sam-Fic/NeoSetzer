#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
        self.page_renderer.connect('rendered_pages_changed', self.on_rendered_pages_changed)

        build_system = self.preview.document.build_system
        build_system.connect('build_state_change', self.on_build_state_change)
        build_system.connect('build_state', self.on_build_state)

        self.show_blank_slate()

    def on_pdf_changed(self, preview):
        if self.preview.poppler_document != None:
            # 成功加载新 PDF 时清除上次的构建失败提示。
            self.view.hide_pdf_load_failed()
            self.show_pdf()
        else:
            # 回到空白状态（无旧 PDF 可回退）时也清除失败提示。
            self.view.hide_pdf_load_failed()
            self.show_blank_slate()

    def on_pdf_load_failed(self, preview):
        # 新 PDF 加载失败，回退到旧 PDF：显示错误图标，弹出 toast 告知用户。
        self.view.show_pdf_load_failed()

    def on_build_state_change(self, build_system, state):
        if state == 'building_in_progress':
            self._build_in_progress = True
            self._last_build_result = None
            if self.view.stack.get_visible_child_name() == 'blank_slate':
                self.view.blank_slate.set_state('building')
        elif state == 'idle':
            self._build_in_progress = False

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
        page_height = layout.page_height
        page_gap = layout.page_gap
        # vertical_padding：第一页顶部在 canvas 中的 y 偏移。first_page /
        # last_page 按页面局部坐标（去掉 padding）计算，transform 时再把
        # padding 加回去，使页面绘制在 vertical_padding 处而非 canvas 顶。
        vertical_padding = layout.vertical_padding
        # ``width``/``height`` are the full canvas size now; the visible
        # viewport size is read from the ScrolledWindow adjustments.
        visible_width = self.view.content.adjustment_x.get_page_size()
        visible_height = self.view.content.adjustment_y.get_page_size()
        margin = layout.get_horizontal_margin(visible_width)
        scrolling_offset_x = self.view.content.scrolling_offset_x
        scrolling_offset_y = self.view.content.scrolling_offset_y
        # 视口顶部在"页面局部坐标系"中的 y（减去 padding）。滚到最顶时
        # scrolling_offset_y=0 → local_y=-padding（负值），max(0) clamp 到 0
        # 即第 0 页，确保缓冲区显示空白而非漏画第一页。
        local_y = max(scrolling_offset_y - vertical_padding, 0)
        page_step = page_height + page_gap
        first_page = int(local_y // page_step)
        # +1 像素确保底部部分可见的最后一页也被渲染：visible_height 若恰好是
        # page_step 的整数倍，整除会漏掉刚好露出一行的下一页；+1 让商越过
        # 整数边界把该页纳入 range。min 限制不超过文档实际页数。
        last_page = min(int((local_y + visible_height + 1) // page_step), self.preview.poppler_document.get_n_pages() - 1)
        # The ScrolledWindow already translates the context by
        # ``(-scrolling_offset_x, -scrolling_offset_y)``, so pages are drawn at
        # their absolute canvas coordinates. transform 到第一页左上角：
        # vertical_padding（canvas 顶缓冲）+ first_page * page_step。
        ctx.transform(cairo.Matrix(1, 0, 0, 1, margin, vertical_padding + first_page * page_step))

        rotation = self.preview.rotation
        for page_number in range(first_page, last_page + 1):
            self.draw_page_background_and_outline(ctx, layout, border_color, page_bg_color)
            if rotation != 0:
                # Draw the un-rotated texture (and synctex highlight) inside a
                # rotation transform so it appears rotated within the page box.
                ctx.save()
                ctx.translate(layout.page_width / 2.0, layout.page_height / 2.0)
                ctx.rotate(math.radians(rotation))
                ctx.translate(-layout.page_width_original / 2.0, -layout.page_height_original / 2.0)
                self.draw_rendered_page(ctx, page_number, layout)
                self.draw_synctex_rectangles(ctx, page_number, synctex_color)
                ctx.restore()
            else:
                self.draw_rendered_page(ctx, page_number, layout)
                self.draw_synctex_rectangles(ctx, page_number, synctex_color)

            ctx.transform(cairo.Matrix(1, 0, 0, 1, 0, page_step))

    def draw_background(self, ctx, drawing_area, bg_color):
        # 画布（"桌面"）背景始终跟随视图背景色（view_bg_color），与页面
        # 颜色解耦：浅色模式画布为浅灰、深色模式为深色，纸张（白或反色后
        # 深色）浮于其上，形成"纸在桌面上"的视觉层次。
        ctx.rectangle(0, 0, drawing_area.get_allocated_width(), drawing_area.get_allocated_height())
        ctx.set_source_rgba(bg_color.red, bg_color.green, bg_color.blue, bg_color.alpha)
        ctx.fill()

    #@timer
    def draw_page_background_and_outline(self, ctx, layout, border_color, bg_color):
        # layout / 颜色由 draw 传入（已缓存为局部变量 / 一帧取色一次），
        # 避免每页 4+ 次 self.preview.layout.xxx 两级属性链查找 + 重复取色。
        border_width = layout.border_width
        page_width = layout.page_width
        page_height = layout.page_height
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
            ctx.set_source_rgba(synctex_color.red, synctex_color.green, synctex_color.blue, synctex_color.alpha * self._current_time_factor)
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



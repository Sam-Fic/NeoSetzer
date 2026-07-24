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
from gi.repository import GObject, Gdk
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
        # 跟踪 synctex 高亮淡出动画的 timeout id。原实现每次 start_fade_loop
        # 都新建一个 timeout，连续 forward/backward sync 会叠加多个 15ms
        # timeout 同时 queue_draw，浪费 CPU；文档关闭时挂起的淡出也会继续
        # 访问已销毁的 drawing_area。改为跟踪 id，新淡出取消旧的，并由
        # preview.shutdown 取消挂起的。
        self._fade_loop_id = None

        self.view.drawing_area.set_draw_func(self.draw)

        self.preview.connect('pdf_changed', self.on_pdf_changed)
        self.preview.connect('layout_changed', self.on_layout_changed)
        self.page_renderer.connect('rendered_pages_changed', self.on_rendered_pages_changed)

        self.show_blank_slate()

    def on_pdf_changed(self, preview):
        if self.preview.poppler_document != None:
            self.show_pdf()
        else:
            self.show_blank_slate()

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
        self.view.stack.set_visible_child_name('blank_slate')

    def show_pdf(self):
        self.view.stack.set_visible_child_name('pdf')
        self.view.drawing_area.queue_draw()

    def start_fade_loop(self):
        # 取消进行中的淡出动画，避免连续 sync 叠加多个 timeout 同时 queue_draw。
        self.cancel_fade_loop()

        def draw():
            timer = (self.highlight_duration + 0.25 - time.time() + self.preview.visible_synctex_rectangles_time)
            if timer <= 0.4:
                self.view.drawing_area.queue_draw()
            if timer >= 0:
                return True
            self._fade_loop_id = None
            return False
        self.view.drawing_area.queue_draw()
        self._fade_loop_id = GObject.timeout_add(15, draw)

    def cancel_fade_loop(self):
        '''取消挂起的 synctex 高亮淡出动画。由 preview.shutdown 调用，
        避免文档关闭后回调继续访问已销毁的 drawing_area。'''
        if self._fade_loop_id is not None:
            GObject.source_remove(self._fade_loop_id)
            self._fade_loop_id = None

    #@timer
    def draw(self, drawing_area, ctx, width, height):
        if self.preview.layout == None:
            self.preview.setup_layout_and_zoom_levels()
            return

        self.draw_background(ctx, drawing_area)

        # 缓存 layout 引用到局部变量：draw 每帧调用，原代码在循环体内多次
        # 经 self.preview.layout.xxx 两级属性链查找（每级 __dict__ 哈希）。
        # 提到局部变量后走 LOAD_FAST，对 5+ 可见页 × 多次属性访问累积省可观。
        layout = self.preview.layout
        page_height = layout.page_height
        page_gap = layout.page_gap
        # ``width``/``height`` are the full canvas size now; the visible
        # viewport size is read from the ScrolledWindow adjustments.
        visible_width = self.view.content.adjustment_x.get_page_size()
        visible_height = self.view.content.adjustment_y.get_page_size()
        margin = layout.get_horizontal_margin(visible_width)
        scrolling_offset_x = self.view.content.scrolling_offset_x
        scrolling_offset_y = self.view.content.scrolling_offset_y
        first_page = int(scrolling_offset_y // (page_height + page_gap))
        last_page = min(int((scrolling_offset_y + visible_height + 1) // (page_height + page_gap)), self.preview.poppler_document.get_n_pages() - 1)
        # The ScrolledWindow already translates the context by
        # ``(-scrolling_offset_x, -scrolling_offset_y)``, so pages are drawn at
        # their absolute canvas coordinates.
        ctx.transform(cairo.Matrix(1, 0, 0, 1, margin, first_page * (page_height + page_gap)))

        page_step = page_height + page_gap
        for page_number in range(first_page, last_page + 1):
            self.draw_page_background_and_outline(ctx, layout)
            self.draw_rendered_page(ctx, page_number, layout)
            self.draw_synctex_rectangles(ctx, page_number)

            ctx.transform(cairo.Matrix(1, 0, 0, 1, 0, page_step))

    def draw_background(self, ctx, drawing_area):
        # 画布底色跟随 PDF 页面底色（recolor 用 view_bg，否则纯白），
        # 让页面与画布无缝融合，消除页面边缘的“描边框”观感。
        if self.preview.recolor_pdf:
            bg = ColorManager.get_ui_color('view_bg_color')
        else:
            bg = Gdk.RGBA(1, 1, 1, 1)
        ctx.rectangle(0, 0, drawing_area.get_allocated_width(), drawing_area.get_allocated_height())
        Gdk.cairo_set_source_rgba(ctx, bg)
        ctx.fill()

    #@timer
    def draw_page_background_and_outline(self, ctx, layout):
        # layout 由 draw 传入（已缓存为局部变量），避免每页 4+ 次
        # self.preview.layout.xxx 两级属性链查找。
        border_width = layout.border_width
        page_width = layout.page_width
        page_height = layout.page_height
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('borders'))
        ctx.rectangle(- border_width, - border_width, page_width + 2 * border_width, page_height + 2 * border_width)
        ctx.fill()

        if self.preview.recolor_pdf:
            Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('view_bg_color'))
        else:
            ctx.set_source_rgba(1, 1, 1, 1)
        ctx.rectangle(0, 0, page_width, page_height)
        ctx.fill()

    def draw_rendered_page(self, ctx, page_number, layout):
        # .get() 替代 `in` + `[]` 两次字典查找；layout 由 draw 传入。
        rendered_page_data = self.page_renderer.rendered_pages.get(page_number)
        if rendered_page_data is None: return

        surface = rendered_page_data[0]
        page_width = rendered_page_data[1] * layout.hidpi_factor

        if not isinstance(surface, cairo.ImageSurface): return

        matrix = ctx.get_matrix()
        layout_page_width = layout.page_width
        factor = layout_page_width / page_width
        ctx.scale(factor, factor)

        ctx.set_source_surface(surface, 0, 0)
        ctx.rectangle(0, 0, layout_page_width / factor, layout.page_height / factor)
        ctx.fill()

        ctx.set_matrix(matrix)

    def draw_synctex_rectangles(self, ctx, page_number):
        try:
            rectangles = self.preview.layout.visible_synctex_rectangles[page_number]
        except KeyError: pass
        else:
            time_factor = self.ease(min(self.highlight_duration + 0.25 - (time.time() - self.preview.visible_synctex_rectangles_time), 0.25) * 4)
            if time_factor < 0:
                self.preview.set_synctex_rectangles(list())
            else:
                color = ColorManager.get_ui_color('highlight_tag_preview')
                color.alpha *= time_factor
                Gdk.cairo_set_source_rgba(ctx, color)
                ctx.set_operator(cairo.Operator.MULTIPLY)
                for rectangle in rectangles:
                    ctx.rectangle(rectangle['x'], rectangle['y'], rectangle['width'], rectangle['height'])
                ctx.fill()
                ctx.set_operator(cairo.Operator.OVER)

    def ease(self, factor): return (factor - 1)**3 + 1



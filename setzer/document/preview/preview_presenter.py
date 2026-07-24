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
        self._fade_loop_id = None

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

        def draw():
            timer = (self.highlight_duration + 0.25 - time.time() + self.preview.visible_synctex_rectangles_time)
            if timer <= 0.4:
                self.view.drawing_area.queue_draw()
            if timer >= 0:
                return True
            self._fade_loop_id = None
            return False
        self.view.drawing_area.queue_draw()
        # 15ms（~67fps）远超人眼对淡出动画的感知阈值，30ms（~33fps）足够，
        # 重绘次数减半（最后 0.4s 约 27 次 → 13 次），减轻与滚动 queue_draw
        # 叠加的双重全画布重绘。
        self._fade_loop_id = GObject.timeout_add(30, draw)

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

        # 一帧内颜色不变（主题切换是低频事件），一次性取色传给各子函数。
        # 原实现每页都调 ColorManager.get_ui_color：5 可见页 × (borders + view_bg)
        # × 60fps ≈ 600 次/秒 Python→C 边界调用 + 字典查找。提升到 draw 入口
        # 后降为每帧 2 次。synctex 颜色仅在有高亮矩形时才取（else None），
        # 保持原「无高亮时零取色」行为。
        border_color = ColorManager.get_ui_color('borders')
        if self.preview.recolor_pdf:
            bg_color = ColorManager.get_ui_color('view_bg_color')
        else:
            bg_color = None  # 非反色用纯白，None 让子函数走 set_source_rgba(1,1,1,1)
        synctex_color = ColorManager.get_ui_color('highlight_tag_preview') if self.preview.visible_synctex_rectangles else None

        self.draw_background(ctx, drawing_area, bg_color)

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
            self.draw_page_background_and_outline(ctx, layout, border_color, bg_color)
            self.draw_rendered_page(ctx, page_number, layout)
            self.draw_synctex_rectangles(ctx, page_number, synctex_color)

            ctx.transform(cairo.Matrix(1, 0, 0, 1, 0, page_step))

    def draw_background(self, ctx, drawing_area, bg_color):
        # 画布底色跟随 PDF 页面底色（recolor 用 view_bg，否则纯白），
        # 让页面与画布无缝融合，消除页面边缘的“描边框”观感。
        ctx.rectangle(0, 0, drawing_area.get_allocated_width(), drawing_area.get_allocated_height())
        if bg_color is not None:
            ctx.set_source_rgba(bg_color.red, bg_color.green, bg_color.blue, bg_color.alpha)
        else:
            ctx.set_source_rgba(1, 1, 1, 1)
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

    def draw_synctex_rectangles(self, ctx, page_number, synctex_color):
        try:
            rectangles = self.preview.layout.visible_synctex_rectangles[page_number]
        except KeyError:
            return
        time_factor = self.ease(min(self.highlight_duration + 0.25 - (time.time() - self.preview.visible_synctex_rectangles_time), 0.25) * 4)
        if time_factor < 0:
            self.preview.set_synctex_rectangles(list())
        elif synctex_color is not None:
            # 不原地修改 synctex_color.alpha：ColorManager 返回的对象可能被缓存，
            # 原代码 color.alpha *= time_factor 会污染缓存，使后续取色（含本帧
            # 其它页与后续帧）拿到不断衰减的 alpha，高亮颜色越来越淡甚至归零。
            # 直接在 set_source_rgba 中计算乘积，零副作用。
            ctx.set_source_rgba(synctex_color.red, synctex_color.green, synctex_color.blue, synctex_color.alpha * time_factor)
            ctx.set_operator(cairo.Operator.MULTIPLY)
            for rectangle in rectangles:
                ctx.rectangle(rectangle.x, rectangle.y, rectangle.width, rectangle.height)
            ctx.fill()
            ctx.set_operator(cairo.Operator.OVER)

    def ease(self, factor): return (factor - 1)**3 + 1



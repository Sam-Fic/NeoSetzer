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

import math
from collections import namedtuple

from setzer.helpers.observable import Observable


# Synctex 高亮矩形（经 scale_factor 缩放后的绘制坐标）。原用 5-key dict，
# 每矩形 dict 创建 + 5 次键值设置开销高于 namedtuple；draw_synctex_rectangles
# 中属性访问（.x）也快于 dict 查找（['x']）。namedtuple 不可变，避免误改。
SynctexRect = namedtuple('SynctexRect', ['page', 'x', 'y', 'width', 'height'])


class PreviewLayouter(Observable):

    def __init__(self, preview, view):
        Observable.__init__(self)
        self.preview = preview
        self.view = view

    def create_layout(self):
        if self.preview.zoom_manager.get_zoom_level() != None and self.preview.poppler_document != None:
            window_width = self.view.get_allocated_width()

            layout = PreviewLayout(self.view.get_scale_factor())
            layout.scale_factor = self.preview.zoom_manager.get_zoom_level() * layout.hidpi_factor

            # Displayed (canvas) page dimensions honor the preview rotation:
            # for 90°/270° the page box is swapped. Rendered textures always use
            # the un-rotated page dimensions, so we keep both around.
            pw = self.preview.page_width
            ph = self.preview.page_height
            rotation = self.preview.rotation
            if rotation in (90, 270):
                disp_pw, disp_ph = ph, pw
            else:
                disp_pw, disp_ph = pw, ph
            layout.page_width = layout.scale_factor * disp_pw
            layout.page_height = layout.scale_factor * disp_ph
            layout.page_width_original = layout.scale_factor * pw
            layout.page_height_original = layout.scale_factor * ph
            layout.rotation = rotation

            layout.page_gap = layout.hidpi_factor * 10
            layout.border_width = 0
            layout.canvas_width = layout.page_width + 2 * layout.get_horizontal_margin(window_width)
            layout.canvas_height = self.preview.poppler_document.get_n_pages() * (layout.page_height + layout.page_gap) - layout.page_gap
            self.update_synctex_rectangles(layout)
            return layout
        else:
            return None

    def update_synctex_rectangles(self, layout):
        layout.visible_synctex_rectangles = dict()
        sf = layout.scale_factor
        # SyncTeX v 是包围框底部距页面顶部的距离 (即包围框的下边缘 y 坐标)。
        # 要得到包围框上边缘 (cairo y)，需减去高度：top = v - height。
        for rectangle in self.preview.visible_synctex_rectangles:
            new_rectangle = SynctexRect(
                rectangle['page'],
                rectangle['h'] * sf,
                (rectangle['v'] - rectangle['height']) * sf,
                rectangle['width'] * sf,
                rectangle['height'] * sf,
            )
            layout.visible_synctex_rectangles.setdefault(rectangle['page'] - 1, []).append(new_rectangle)


class PreviewLayout(object):

    def __init__(self, hidpi_factor):
        self.hidpi_factor = hidpi_factor
        self.page_width = None
        self.page_height = None
        self.page_width_original = None
        self.page_height_original = None
        self.page_gap = None
        self.border_width = None
        self.canvas_width = None
        self.canvas_height = None
        self.scale_factor = None
        self.rotation = None
        self.visible_synctex_rectangles = dict()

    def get_horizontal_margin(self, window_width):
        return int(max((window_width - self.page_width) / 2, 0))

    def get_page_number_and_offsets_by_document_offsets(self, x, y, window_width):
        # 此方法在每次滚动/悬停时经 update_cursor 调用。原代码 3 次调用
        # get_horizontal_margin（各做 int(max(...))），3 次计算
        # page_height + page_gap。缓存到局部变量后各只算一次。
        page_height_plus_gap = self.page_height + self.page_gap
        if y % page_height_plus_gap > self.page_height: return None
        h_margin = self.get_horizontal_margin(window_width)
        if x < h_margin or x > (h_margin + self.page_width): return None

        page_number = int(y // page_height_plus_gap)
        y_offset = y % page_height_plus_gap
        x_offset = x - h_margin

        rotation = self.rotation
        if rotation != 0:
            # x_offset / y_offset are now in displayed box-local coords (CSS,
            # y-down). Invert the rotation transform used when drawing the page
            # to recover the original (un-rotated) page coordinates.
            disp_w = self.page_width
            disp_h = self.page_height
            orig_w = self.page_width_original
            orig_h = self.page_height_original
            cx = disp_w / 2.0
            cy = disp_h / 2.0
            ocx = orig_w / 2.0
            ocy = orig_h / 2.0
            theta = math.radians(rotation)
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            dx = x_offset - cx
            dy = y_offset - cy
            ox_css = ocx + dx * cos_t + dy * sin_t
            oy_css = ocy - dx * sin_t + dy * cos_t
            x_offset = ox_css / self.scale_factor
            y_offset = oy_css / self.scale_factor
        else:
            x_offset = x_offset / self.scale_factor
            y_offset = y_offset / self.scale_factor

        return (page_number, x_offset, y_offset)

    def get_page_by_offset(self, offset):
        return int(1 + offset // (self.page_height + self.page_gap))



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
            layout.page_width = layout.scale_factor * self.preview.page_width
            layout.page_height = layout.scale_factor * self.preview.page_height
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
        self.page_gap = None
        self.border_width = None
        self.canvas_width = None
        self.canvas_height = None
        self.scale_factor = None
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
        y_offset = y % page_height_plus_gap / self.scale_factor
        x_offset = (x - h_margin) / self.scale_factor

        return (page_number, x_offset, y_offset)

    def get_page_by_offset(self, offset):
        return int(1 + offset // (self.page_height + self.page_gap))



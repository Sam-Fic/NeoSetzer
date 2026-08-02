#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from bisect import bisect_right
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
            #
            # 历史：layout.page_width / page_height 是单一值（取自首页），假设
            # 所有页等高。任何破坏等高的情形（\enlargethispage、A3 横排插图、
            # paper size 变化）都会算错页号与滚动位置。改 per-page：
            # page_heights / page_y_starts 是关键缓存，bisect 替换整除。
            pw = self.preview.page_width
            ph = self.preview.page_height
            rotation = self.preview.rotation
            if rotation in (90, 270):
                disp_pw, disp_ph = ph, pw
            else:
                disp_pw, disp_ph = pw, ph
            # 兼容旧调用：page_width / page_height 仍设为首页值（旧代码 +
            # 渐变路径共用）。新代码应优先用 layout.page_widths[i] /
            # page_heights[i] / get_page_top / get_page_height 等 per-page API。
            layout.page_width = layout.scale_factor * disp_pw
            layout.page_height = layout.scale_factor * disp_ph
            layout.page_width_original = layout.scale_factor * pw
            layout.page_height_original = layout.scale_factor * ph
            layout.rotation = rotation

            layout.page_gap = layout.hidpi_factor * 10
            # 描边：draw_page_background_and_outline 先填一个略大的
            # border_color 矩形、再用页面底色覆盖中心，留下边缘 border_width
            # 那一圈作描边。值=0 时外/内矩形同尺寸描边被完全覆盖（不可见）。
            # 画布已有 view_bg 背景板后，描边强化"纸浮在桌面上"
            # 的层次感；此前曾因画布与页面同色（纯白）显得突兀而置 0。
            layout.border_width = 3
            # 画布顶/底的缓冲高度：第一页不直接从 canvas y=0 开始、最后一页
            # 也不直接到 canvas_height 结束，而是各留一段 vertical_padding
            # 的"桌面"空白。滚到顶/底时纸张不再贴窗口边缘，与左右水平 margin
            # 形成四周呼吸空间。值取 page_gap 的 3 倍，与页间距视觉协调。
            layout.vertical_padding = layout.page_gap * 1

            # ---- per-page 几何：从 poppler 读每页 size，按 rotation 换算
            # 显示尺寸，乘 scale 得 canvas 像素高，再累加得 page_y_starts。
            poppler_doc = self.preview.poppler_document
            n_pages = poppler_doc.get_n_pages()
            page_heights = []
            page_y_starts = []
            cumulative = layout.vertical_padding
            for i in range(n_pages):
                size = poppler_doc.get_page(i).get_size()
                if rotation in (90, 270):
                    page_disp_ph = size.width
                else:
                    page_disp_ph = size.height
                ph_px = layout.scale_factor * page_disp_ph
                page_heights.append(ph_px)
                page_y_starts.append(cumulative)
                cumulative += ph_px + layout.page_gap
            # canvas 高度 = 最后一页底 + 底部 vertical_padding（与原公式
            # "n * (h + gap) - gap + 2 * padding" 等价当 per-page 相同时）。
            if n_pages > 0:
                layout.canvas_height = page_y_starts[-1] + page_heights[-1] + layout.vertical_padding
            else:
                layout.canvas_height = 2 * layout.vertical_padding
            layout.page_heights = page_heights
            layout.page_y_starts = page_y_starts

            layout.canvas_width = layout.page_width + 2 * layout.get_horizontal_margin(window_width)
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
        # 画布顶/底缓冲（见 create_layout）。None 表示尚未 layout。
        self.vertical_padding = None
        self.canvas_width = None
        self.canvas_height = None
        self.scale_factor = None
        self.rotation = None
        self.visible_synctex_rectangles = dict()
        # per-page 几何（create_layout 填充）。
        # page_heights[i] 是第 i 页的 canvas 像素高（含 rotation 后的
        # 显示尺寸）；page_y_starts[i] 是第 i 页顶部的 canvas y 坐标。
        # 二者长度 = n_pages，索引 0-based。
        self.page_heights = None
        self.page_y_starts = None

    def get_horizontal_margin(self, window_width):
        return int(max((window_width - self.page_width) / 2, 0))

    def get_page_count(self):
        '''总页数。无 layout 时返回 0。'''
        if self.page_y_starts is None:
            return 0
        return len(self.page_y_starts)

    def get_page_height(self, page):
        '''第 page 页的 canvas 像素高（0-based）。无 layout 或越界返回 None。'''
        if self.page_heights is None or page < 0 or page >= len(self.page_heights):
            return None
        return self.page_heights[page]

    def get_page_top(self, page):
        '''第 page 页顶部的 canvas y 坐标（0-based）。无 layout 或越界返回 None。'''
        if self.page_y_starts is None or page < 0 or page >= len(self.page_y_starts):
            return None
        return self.page_y_starts[page]

    def get_page_by_offset(self, offset):
        '''1-based page number at the given canvas y offset。被
        preview_page_renderer.compute_visible_pages 用于确定当前页。

        per-page 实现：用 bisect_right 在 page_y_starts 上定位。offset
        落在顶部 vertical_padding 区时（滚到最顶）返回第 1 页而非 0 /
        负数；落在最后一页之后 clamp 到最后一页。gap 区段内算前一页
        （与原 // 行为一致：floor）。'''
        n = self.get_page_count()
        if n == 0:
            return 1
        # bisect_right 返回「offset 应插入的位置」；减 1 即为所在页 0-based。
        # offset < page_y_starts[0] 时 bisect_right 返回 0 → -1；clamp 到 0
        # 然后转 1-based。offset >= page_y_starts[-1] 时返回 n → n-1。
        idx = bisect_right(self.page_y_starts, offset) - 1
        if idx < 0:
            return 1
        if idx >= n:
            return n
        return idx + 1

    def get_page_number_and_offsets_by_document_offsets(self, x, y, window_width):
        # 此方法在每次滚动/悬停时经 update_cursor 调用。per-page 实现：
        # 用 bisect 在 page_y_starts 定位所在页，再以该页 height 判 gap。
        # vertical_padding：第一页顶部之前是画布缓冲区，点击该区域（y <
        # vertical_padding）应判为"不在任何页面上"，与页面间 gap 的处理一致。
        n = self.get_page_count()
        if n == 0:
            return None
        if y < self.vertical_padding:
            return None
        # bisect_right 把 y 视作「应在哪个累积起点之后」。page_y_starts[i]
        # 已含 vertical_padding，故直接对 y 搜。y < vertical_padding
        # 已在前面早返挡掉。
        idx = bisect_right(self.page_y_starts, y) - 1
        if idx < 0:
            idx = 0
        if idx >= n:
            idx = n - 1
        page_h = self.page_heights[idx]
        # gap 判定：y 在「page_y_starts[idx] + page_h」之后（落在页间 gap）
        # 视为不在任何页面上。
        if y - self.page_y_starts[idx] > page_h:
            return None
        h_margin = self.get_horizontal_margin(window_width)
        if x < h_margin or x > (h_margin + self.page_width):
            return None

        page_number = idx  # 0-based，保留原 API 语义
        y_offset = y - self.page_y_starts[idx]
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

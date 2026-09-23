#!/usr/bin/env python3
# coding: utf-8

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

'''Regression tests for PDF internal link (table of contents) jumps.

Poppler.Dest.page_num is 1-based: poppler-glib adds 1 both in
poppler_document_find_dest() and in the actions returned by
poppler_page_get_link_mapping(). The layout model is 0-based, so mixing the
two frames made every TOC click land one page too far down, while links to
the last page did nothing at all (page lookup out of range).
'''


import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from setzer.document.preview.preview_layouter import PreviewLayouter


PREVIEW_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'document' / 'preview'
    / 'preview.py'
)

PAGE_W = 612.0
PAGE_H = 792.0
N_PAGES = 14
# 对应 preview_layouter.create_layout 的页间距 / 顶底缓冲（hidpi = 1）
PAGE_GAP = 10


def _preview_method(name):
    '''取出 Preview 类中某个方法并 exec 成独立函数（无 gi 依赖）。'''
    tree = ast.parse(PREVIEW_SOURCE.read_text(encoding='utf-8'))
    preview_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Preview'
    )
    method = next(
        node for node in preview_class.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {}
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(PREVIEW_SOURCE), 'exec'),
         namespace)
    return namespace[name]


class _Content:
    def __init__(self):
        self.scrolling_offset_x = 0
        self.width = 600
        self.calls = []

    def scroll_to_position(self, position):
        self.calls.append(tuple(position))


class _View:
    def __init__(self, content):
        self.content = content

    def get_allocated_width(self):
        return 800

    def get_scale_factor(self):
        return 1


class _Document:
    def __init__(self, sizes):
        self._sizes = sizes

    def get_n_pages(self):
        return len(self._sizes)

    def get_page(self, index):
        return SimpleNamespace(get_size=lambda: self._sizes[index])


class _ZoomManager:
    def get_zoom_level(self):
        return 1.0


class _PreviewModel:
    '''Preview 中 scroll_dest_on_screen 依赖的最小接口子集。'''

    def __init__(self, layout, rotation, content, sizes):
        self.layout = layout
        self.rotation = rotation
        self.view = _View(content)
        self._sizes = sizes
        self.original_to_canvas_calls = []

    def get_page_height(self, page):
        # 与 Preview.get_page_height 一致：0-based，越界回退首页值
        if page < 0 or page >= len(self._sizes):
            return self._sizes[0].height
        return self._sizes[page].height

    def original_to_canvas(self, page_number, x_pt, y_pt):
        self.original_to_canvas_calls.append((page_number, x_pt, y_pt))
        page_top = self.layout.get_page_top(page_number)
        if page_top is None:
            return None
        return (x_pt, page_top + y_pt)


def _dest(page_num, left=72.0, top=626.6):
    return SimpleNamespace(page_num=page_num, left=left, top=top,
                           right=0.0, bottom=0.0)


def _build(rotation=0, sizes=None):
    sizes = sizes if sizes is not None else _uniform_sizes()
    content = _Content()
    preview = SimpleNamespace(
        zoom_manager=_ZoomManager(),
        poppler_document=_Document(sizes),
        page_width=sizes[0].width,
        page_height=sizes[0].height,
        rotation=rotation,
        visible_synctex_rectangles={},
    )
    layout = PreviewLayouter(preview, _View(content)).create_layout()
    return _PreviewModel(layout, rotation, content, sizes), layout, content


def _uniform_sizes():
    return [SimpleNamespace(width=PAGE_W, height=PAGE_H) for _ in range(N_PAGES)]


class TestScrollDestOnScreen(unittest.TestCase):

    def setUp(self):
        self.scroll_dest = _preview_method('scroll_dest_on_screen')

    def test_dest_lands_on_target_page(self):
        '''1-based page_num 必须换算成 0-based 页码。'''
        model, layout, content = _build()
        self.scroll_dest(model, _dest(4))
        self.assertEqual(len(content.calls), 1)
        _, y = content.calls[0]
        page_top = layout.get_page_top(3)
        page_height = layout.get_page_height(3)
        self.assertAlmostEqual(y, page_top + (page_height - 626.6), places=6)
        self.assertLessEqual(y, page_top + page_height)

    def test_dest_does_not_land_on_following_page(self):
        '''未换算页码时曾整页偏下（多减一个页高）。'''
        model, layout, content = _build()
        self.scroll_dest(model, _dest(4))
        _, y = content.calls[0]
        next_page_y = layout.get_page_top(4) + (layout.get_page_height(4) - 626.6)
        self.assertNotAlmostEqual(y, next_page_y, places=3)

    def test_in_page_offset_ignores_page_gap(self):
        '''y = 页顶 + (页高 - dest.top)，不得再额外减页间距。'''
        model, layout, content = _build()
        self.scroll_dest(model, _dest(1, top=700.0))
        _, y = content.calls[0]
        page_top = layout.get_page_top(0)
        self.assertAlmostEqual(y, page_top + (layout.get_page_height(0) - 700.0), places=6)
        self.assertNotAlmostEqual(y, page_top + (layout.get_page_height(0) - 700.0 - PAGE_GAP),
                                  places=3)

    def test_non_uniform_pages_use_target_page_geometry(self):
        '''\\enlargethispage / 横排插图等破坏等高假设时，落点必须用目标页
        自己的 page_y_starts / page_height。'''
        sizes = _uniform_sizes()
        sizes[3] = SimpleNamespace(width=PAGE_W, height=PAGE_H + 200)
        sizes[4] = SimpleNamespace(width=PAGE_W, height=PAGE_H - 150)
        model, layout, content = _build(sizes=sizes)
        self.scroll_dest(model, _dest(5, top=700.0))
        _, y = content.calls[0]
        self.assertAlmostEqual(
            y, layout.get_page_top(4) + (layout.get_page_height(4) - 700.0), places=6)
        uniform_top = layout.vertical_padding + 4 * (layout.page_height + layout.page_gap)
        self.assertNotAlmostEqual(
            y, uniform_top + (layout.page_height - 700.0), places=3)

    def test_last_page_dest_still_scrolls(self):
        '''末页目标在旧代码里因越界直接 return，点击无任何反应。'''
        model, layout, content = _build()
        self.scroll_dest(model, _dest(N_PAGES, top=720.0))
        self.assertEqual(len(content.calls), 1)
        _, y = content.calls[0]
        last_top = layout.get_page_top(N_PAGES - 1)
        self.assertAlmostEqual(y, last_top + (layout.get_page_height(N_PAGES - 1) - 720.0), places=6)

    def test_rotated_view_uses_zero_based_page(self):
        '''rotation != 0 分支同样要把页码转成 0-based，并保持 y-up → y-down。'''
        model, layout, content = _build(rotation=90)
        self.scroll_dest(model, _dest(4))
        self.assertTrue(model.original_to_canvas_calls)
        # 两次换算（dest 左上 / 右下）都必须用 0-based 页码
        for page_number, _, _ in model.original_to_canvas_calls:
            self.assertEqual(page_number, 3)
        page_number, x_pt, y_pt = model.original_to_canvas_calls[0]
        self.assertAlmostEqual(x_pt, 72.0, places=6)
        # dest.top 是 y-up，进入旋转换算前要转成 top-down
        self.assertAlmostEqual(y_pt, PAGE_H - 626.6, places=6)
        self.assertEqual(len(content.calls), 1)

    def test_out_of_range_dest_does_not_scroll(self):
        model, layout, content = _build()
        self.scroll_dest(model, _dest(N_PAGES + 5))
        self.assertEqual(content.calls, [])

    def test_none_dest_and_missing_layout_do_not_scroll(self):
        model, layout, content = _build()
        self.scroll_dest(model, None)
        model.layout = None
        self.scroll_dest(model, _dest(1))
        self.assertEqual(content.calls, [])


if __name__ == '__main__':
    unittest.main()

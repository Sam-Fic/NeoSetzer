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

'''选区高亮墨迹精修单测（preview._tighten_selection_boxes 等）。

字形框是行高盒（ascent+descent）：选中内容无降部字母时底边比实际
墨迹低 ~2pt，高亮条看起来偏下。精修用页面栅格的逐列墨迹轮廓把选中
字形框按行收缩到实际墨迹范围（只缩不扩）。

方法体经 AST 提取 exec（与 test_backward_sync_match 同模式），注入
合成栅格与字形框，不依赖 GTK/poppler 真实渲染。
'''

import ast
import math
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import cairo

SRC = os.path.join(REPO, 'setzer', 'document', 'preview', 'preview.py')
_METHODS = ('_tighten_selection_boxes', '_get_line_ink_profiles',
            '_get_selection_raster')


def _load_methods():
    tree = ast.parse(open(SRC, encoding='utf-8').read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'Preview')
    ns = {'cairo': cairo, 'math': math,
          '_TEXT_INK_SCALE': 2.0, '_TEXT_INK_THRESHOLD': 200,
          '_TEXT_INK_RASTER_MAX_PAGES': 4}
    for name in _METHODS:
        fn = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, 'exec'), ns)
    return ns


_NS = _load_methods()


def _glyph(x1, y1, x2, y2):
    return SimpleNamespace(x1=x1, y1=y1, x2=x2, y2=y2)


def _raster(width=200, height=100):
    '''白底 ARGB32 合成栅格（scale=2 的 points→像素空间）。'''
    stride = width * 4
    data = bytearray(b'\xff' * (stride * height))
    return data, stride, width, height


def _paint(data, stride, px1, py1, px2, py2):
    for py in range(py1, py2):
        for px in range(px1, px2):
            off = py * stride + px * 4
            data[off:off + 4] = b'\x00\x00\x00\xff'


class FakePreview:

    def __init__(self, raster):
        self._text_ink_raster_cache = {}
        self._text_ink_profile_cache = {}
        self._raster = raster
        # 只绑精修与轮廓方法；_get_selection_raster 用下面的 fake 覆盖。
        for name in ('_tighten_selection_boxes', '_get_line_ink_profiles'):
            setattr(self, name, _NS[name].__get__(self))

    def _get_selection_raster(self, page):
        return self._raster


class TightenSelectionBoxesTest(unittest.TestCase):

    def setUp(self):
        # 字形行 y 10..20pt（像素 20..40）；墨迹画在像素行 22..32
        # （points 11..16）：上下各留 1pt/4pt 空白，模拟无降部字母。
        self.data, self.stride, self.width, self.height = _raster()
        _paint(self.data, self.stride, 20, 22, 80, 32)
        self.glyphs = [_glyph(10, 10, 20, 20), _glyph(20, 10, 30, 20),
                       _glyph(30, 10, 40, 20)]
        self.fake = FakePreview((self.data, self.stride, self.width, self.height))

    def test_shrinks_to_ink_without_descender(self):
        boxes = [(10, 10, 20, 20), (20, 10, 30, 20)]
        out = self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 16.0), (20, 11.0, 30, 16.0)])

    def test_descender_keeps_full_bottom(self):
        # 第二个字形列范围有降到盒底的墨迹（降部字母）：整行保持底边。
        _paint(self.data, self.stride, 40, 32, 60, 40)
        boxes = [(10, 10, 20, 20), (20, 10, 30, 20)]
        out = self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 20), (20, 11.0, 30, 20)])

    def test_selection_without_descender_glyph_shrinks(self):
        # 同一行有降部墨迹，但选中的字形列范围没有：仍收缩。
        _paint(self.data, self.stride, 60, 32, 80, 40)
        boxes = [(10, 10, 20, 20), (20, 10, 30, 20)]
        out = self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 16.0), (20, 11.0, 30, 16.0)])

    def test_space_glyph_gets_line_band(self):
        # 空格列无墨迹：与同行字母保持同一高亮带（不单独凸出）。
        boxes = [(10, 10, 20, 20), (20, 10, 30, 20), (30, 10, 40, 20)]
        # 只画首尾字形的墨迹，中间（空格）留白。
        data, stride, width, height = _raster()
        _paint(data, stride, 20, 22, 40, 32)
        _paint(data, stride, 60, 22, 80, 32)
        fake = FakePreview((data, stride, width, height))
        out = fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 16.0), (20, 11.0, 30, 16.0),
                               (30, 11.0, 40, 16.0)])

    def test_no_ink_keeps_original_boxes(self):
        data, stride, width, height = _raster()  # 全白
        fake = FakePreview((data, stride, width, height))
        boxes = [(10, 10, 20, 20)]
        self.assertEqual(fake._tighten_selection_boxes(0, self.glyphs, boxes),
                         boxes)

    def test_raster_unavailable_keeps_original_boxes(self):
        fake = FakePreview(None)
        boxes = [(10, 10, 20, 20)]
        self.assertEqual(fake._tighten_selection_boxes(0, self.glyphs, boxes),
                         boxes)

    def test_ink_outside_box_does_not_expand(self):
        # 只缩不扩：盒外墨迹（相邻行/大括号溢出）不并入高亮。
        _paint(self.data, self.stride, 20, 40, 80, 48)  # 盒底之下
        _paint(self.data, self.stride, 20, 12, 80, 20)  # 盒顶之上
        boxes = [(10, 10, 20, 20)]
        out = self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 16.0)])

    def test_multi_line_groups_tighten_independently(self):
        # 第二字形行 y 30..40pt（像素 60..80），墨迹充满整盒（含降部）。
        glyphs = self.glyphs + [_glyph(10, 30, 20, 40)]
        _paint(self.data, self.stride, 20, 60, 40, 80)
        boxes = [(10, 10, 20, 20), (10, 30, 20, 40)]
        out = self.fake._tighten_selection_boxes(0, glyphs, boxes)
        self.assertEqual(out, [(10, 11.0, 20, 16.0), (10, 30, 20, 40)])

    def test_empty_boxes_passthrough(self):
        self.assertEqual(self.fake._tighten_selection_boxes(0, self.glyphs, []),
                         [])

    def test_profile_cached_across_calls(self):
        boxes = [(10, 10, 20, 20)]
        with mock.patch.object(self.fake, '_get_selection_raster',
                               wraps=self.fake._get_selection_raster) as ras:
            self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
            self.fake._tighten_selection_boxes(0, self.glyphs, boxes)
        self.assertEqual(ras.call_count, 1)


class SelectionRasterTest(unittest.TestCase):
    '''_get_selection_raster 真实方法体：渲染失败兜底与缓存淘汰。'''

    def _fake(self, page_side_effect):
        fake = SimpleNamespace(
            _text_ink_raster_cache={},
            poppler_document=SimpleNamespace(
                get_page=mock.Mock(side_effect=page_side_effect)))
        fake._get_selection_raster = _NS['_get_selection_raster'].__get__(fake)
        return fake

    def _page(self):
        page = mock.Mock()
        page.get_size.return_value = (10.0, 10.0)
        return page

    def test_render_failure_caches_none(self):
        fake = self._fake(RuntimeError('boom'))
        self.assertIsNone(fake._get_selection_raster(0))
        self.assertIsNone(fake._get_selection_raster(0))
        self.assertEqual(fake.poppler_document.get_page.call_count, 1)

    def test_raster_cache_evicts_oldest_beyond_limit(self):
        fake = self._fake(lambda page: self._page())
        for page in range(5):
            self.assertIsNotNone(fake._get_selection_raster(page))
        self.assertEqual(len(fake._text_ink_raster_cache), 4)
        self.assertNotIn(0, fake._text_ink_raster_cache)
        self.assertIn(4, fake._text_ink_raster_cache)

    def test_cached_page_not_rerendered(self):
        fake = self._fake(lambda page: self._page())
        first = fake._get_selection_raster(0)
        second = fake._get_selection_raster(0)
        self.assertIs(first, second)
        self.assertEqual(fake.poppler_document.get_page.call_count, 1)


if __name__ == '__main__':
    unittest.main()
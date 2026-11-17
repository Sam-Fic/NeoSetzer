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

'''分数缩放高亮叠层单测（preview_presenter 的 device 空间同映射路径）。

系统分数缩放（如 1.664X）下高亮条与文字错位、且随页码漂移的修复：
高亮不再画矢量矩形，而是栅格化进与页面纹理同尺寸的 device surface，
再用与 draw_rendered_page 逐字相同的 int(css)/surface scale 绘制；
draw_rendered_page 本身也改用 int(css) 精确映射消除 int 截断拉伸 δ。

方法体经 AST 提取 exec（与 test_preview_resize_drawing 同模式），
注入 cairo 与 ColorManager 桩，不依赖 GTK 真实渲染。
'''

import ast
import os
import sys
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import cairo

SRC = os.path.join(REPO, 'setzer', 'document', 'preview', 'preview_presenter.py')
_METHODS = ('draw_rendered_page', 'draw_text_selection_rectangles',
            '_get_selection_overlay', 'on_layout_changed', 'on_pdf_changed')


class _Color:
    red, green, blue, alpha = 0.0, 0.0, 1.0, 1.0


class _ColorManager:
    @staticmethod
    def get_ui_color(name):
        return _Color()


def _load_methods():
    tree = ast.parse(open(SRC, encoding='utf-8').read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == 'PreviewPresenter')
    ns = {'cairo': cairo, 'ColorManager': _ColorManager}
    for name in _METHODS:
        fn = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, 'exec'), ns)
    return ns


_NS = _load_methods()

# 分数 css 尺寸：int 截断 δ 显著（δ≈0.00125），放大旧映射误差便于断言。
CSS_W, CSS_H = 612.99, 792.99
SCALE = 2.0
HIDPI = 2
SW, SH = int(CSS_W) * HIDPI, int(CSS_H) * HIDPI  # 1224 × 1584，与真实渲染器一致
RECT = (50.0, 350.0, 150.0, 356.0)  # points（页高≈396pt），页底附近（δ 误差最大处）


def _layout():
    return SimpleNamespace(page_width_original=CSS_W, page_height_original=CSS_H,
                           scale_factor=SCALE, hidpi_factor=HIDPI)


def _texture_with_marker():
    '''复刻 render_page_loop：白底 + 密度 scale×hidpi，RECT 处画黑标记。'''
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SW, SH)
    ctx = cairo.Context(surf)
    ctx.set_source_rgba(1, 1, 1, 1)
    ctx.paint()
    ctx.scale(SCALE * HIDPI, SCALE * HIDPI)
    ctx.set_source_rgba(0, 0, 0, 1)
    ctx.rectangle(RECT[0], RECT[1], RECT[2] - RECT[0], RECT[3] - RECT[1])
    ctx.fill()
    return surf


def _presenter(textures):
    pres = SimpleNamespace(preview=SimpleNamespace(layout=_layout(),
                                                   text_selection_regions={0: [RECT]}),
                           page_renderer=SimpleNamespace(rendered_pages=textures),
                           _selection_overlay_cache={})
    for name in _METHODS:
        setattr(pres, name, _NS[name].__get__(pres))
    return pres


def _target():
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(CSS_W) + 4, int(CSS_H) + 4)
    ctx = cairo.Context(surf)
    ctx.set_source_rgba(1, 1, 1, 1)
    ctx.paint()
    return surf, ctx


def _rows(surf, kind, y0, y1):
    '''在 x∈[RECT.x1×scale, RECT.x2×scale] 窗口内扫描首/末命中行。'''
    data = surf.get_data()
    stride = surf.get_stride()
    px0, px1 = int(RECT[0] * SCALE), int(RECT[2] * SCALE)
    top = bot = None
    for py in range(y0, y1):
        row = data[py * stride: py * stride + surf.get_width() * 4]
        hit = False
        for px in range(px0, px1):
            b, g, r, a = row[px * 4: px * 4 + 4]
            if kind == 'ink' and r < 200 and g < 200 and b < 200:
                hit = True
                break
            if kind == 'hl' and (r < 250 or g < 250) and b > 200:
                hit = True
                break
        if hit:
            if top is None:
                top = py
            bot = py + 1
    return top, bot


class SelectionOverlayMappingTest(unittest.TestCase):

    def test_highlight_aligns_with_texture_marker_at_fractional_css(self):
        '''核心契约：高亮与纹理文字逐像素同坐标（含 int 截断 δ 消除）。'''
        tex = _texture_with_marker()
        pres = _presenter({0: [tex, int(CSS_W), int(CSS_H), 1, None]})
        surf, ctx = _target()
        pres.draw_rendered_page(ctx, 0, pres.preview.layout)
        ink_t, ink_b = _rows(surf, 'ink', int(RECT[1] * SCALE) - 6,
                             int(RECT[3] * SCALE) + 6)
        surf2, ctx2 = _target()
        pres.draw_text_selection_rectangles(ctx2, 0, _Color())
        hl_t, hl_b = _rows(surf2, 'hl', int(RECT[1] * SCALE) - 6,
                           int(RECT[3] * SCALE) + 6)
        exact = int(RECT[1] * SCALE)  # 700，净映射 = points×scale 精确
        self.assertEqual(ink_t, exact)
        self.assertEqual(hl_t, exact)
        self.assertEqual(ink_t, hl_t)
        self.assertEqual(ink_b, hl_b)

    def test_overlay_same_size_as_texture(self):
        tex = _texture_with_marker()
        pres = _presenter({0: [tex, int(CSS_W), int(CSS_H), 1, None]})
        overlay = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        self.assertEqual((overlay.get_width(), overlay.get_height()), (SW, SH))

    def test_draw_restores_matrix(self):
        tex = _texture_with_marker()
        pres = _presenter({0: [tex, int(CSS_W), int(CSS_H), 1, None]})
        for method, args in (('draw_rendered_page', (0, pres.preview.layout)),
                             ('draw_text_selection_rectangles', (0, _Color()))):
            surf, ctx = _target()
            ctx.translate(17.5, 23.25)
            before = tuple(ctx.get_matrix())
            getattr(pres, method)(ctx, *args)
            self.assertEqual(tuple(ctx.get_matrix()), before)

    def test_fallback_vector_when_texture_missing(self):
        pres = _presenter({})
        surf, ctx = _target()
        pres.draw_text_selection_rectangles(ctx, 0, _Color())
        hl_t, _ = _rows(surf, 'hl', int(RECT[1] * SCALE) - 6,
                        int(RECT[3] * SCALE) + 6)
        self.assertEqual(hl_t, int(RECT[1] * SCALE))
        self.assertEqual(pres._selection_overlay_cache, {})

    def test_no_color_or_no_regions_draws_nothing(self):
        tex = _texture_with_marker()
        pres = _presenter({0: [tex, int(CSS_W), int(CSS_H), 1, None]})
        surf, ctx = _target()
        pres.draw_text_selection_rectangles(ctx, 0, None)
        pres.preview.text_selection_regions = {}
        pres.draw_text_selection_rectangles(ctx, 0, _Color())
        hl_t, _ = _rows(surf, 'hl', 0, surf.get_height())
        self.assertIsNone(hl_t)


class SelectionOverlayCacheTest(unittest.TestCase):

    def test_same_key_reuses_surface(self):
        pres = _presenter({})
        a = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        b = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        self.assertIs(a, b)

    def test_changed_rects_reuses_surface_but_repaints(self):
        pres = _presenter({})
        a = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        moved = (RECT[0], RECT[1] + 20, RECT[2], RECT[3] + 20)
        b = pres._get_selection_overlay(0, [moved], pres.preview.layout, SW, SH)
        self.assertIs(a, b)  # 尺寸不变复用同一 surface，仅重绘
        data = b.get_data()
        stride = b.get_stride()
        old_row = int(RECT[1] * SCALE * HIDPI)
        new_row = int((RECT[1] + 20) * SCALE * HIDPI)
        px = int(RECT[0] * SCALE * HIDPI) + 4
        self.assertEqual(bytes(data[old_row * stride + px * 4: old_row * stride + px * 4 + 3]),
                         b'\x00\x00\x00')  # CLEAR 后旧位置透明
        self.assertNotEqual(bytes(data[new_row * stride + px * 4: new_row * stride + px * 4 + 3]),
                            b'\x00\x00\x00')

    def test_changed_size_allocates_new_surface(self):
        pres = _presenter({})
        a = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        b = pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW // 4, SH // 4)
        self.assertIsNot(a, b)
        self.assertEqual((b.get_width(), b.get_height()), (SW // 4, SH // 4))

    def test_layout_changed_clears_cache(self):
        pres = _presenter({})
        pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        pres.preview.layout = None
        pres.view = SimpleNamespace(
            drawing_area=SimpleNamespace(set_content_width=lambda v: None,
                                         set_content_height=lambda v: None),
            content=SimpleNamespace(queue_draw=lambda: None))
        pres.on_layout_changed(pres.preview)
        self.assertEqual(pres._selection_overlay_cache, {})

    def test_pdf_changed_clears_cache(self):
        pres = _presenter({})
        pres._get_selection_overlay(0, [RECT], pres.preview.layout, SW, SH)
        pres.preview.poppler_document = None
        pres.show_blank_slate = lambda: None
        pres.view = SimpleNamespace(hide_pdf_load_failed=lambda: None,
                                    cancel_page_indicator_timer=lambda: None,
                                    is_page_indicator_visible=lambda: False)
        pres.on_pdf_changed(pres.preview)
        self.assertEqual(pres._selection_overlay_cache, {})


if __name__ == '__main__':
    unittest.main()
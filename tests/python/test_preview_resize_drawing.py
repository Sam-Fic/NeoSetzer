#!/usr/bin/env python3
# coding: utf-8
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pixel-level checks for scaling cached PDF pages during layout animations."""

import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

try:
    import cairo
except ImportError:
    cairo = None


def load_draw_method():
    path = Path(__file__).resolve().parents[2] / 'setzer/document/preview/preview_presenter.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PreviewPresenter')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'draw_rendered_page')
    namespace = {'cairo': cairo}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['draw_rendered_page']


@unittest.skipIf(cairo is None, 'pycairo required for pixel-level drawing tests')
class TestPreviewResizeDrawing(unittest.TestCase):
    def test_cached_surface_fills_current_page_at_all_scales_and_rotations(self):
        draw = load_draw_method()
        for hidpi in (1, 2):
            for draft_divisor in (1, 4):
                for width in (60, 100, 140):
                    for rotation in (0, 90, 180, 270):
                        with self.subTest(hidpi=hidpi, draft=draft_divisor,
                                          width=width, rotation=rotation):
                            old_w, old_h = 100, 200
                            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                                         old_w * hidpi // draft_divisor,
                                                         old_h * hidpi // draft_divisor)
                            source_ctx = cairo.Context(surface)
                            source_ctx.set_source_rgb(0, 0, 0)
                            source_ctx.paint()
                            presenter = SimpleNamespace(page_renderer=SimpleNamespace(
                                rendered_pages={0: [surface, old_w, old_h, 1, None]}))
                            height = width * 2
                            layout = SimpleNamespace(page_width_original=width,
                                                     page_height_original=height,
                                                     hidpi_factor=hidpi)
                            out_w, out_h = (height, width) if rotation in (90, 270) else (width, height)
                            target = cairo.ImageSurface(cairo.FORMAT_ARGB32, out_w + 20, out_h + 20)
                            ctx = cairo.Context(target)
                            ctx.set_source_rgb(1, 1, 1)
                            ctx.paint()
                            ctx.translate(10 + out_w / 2, 10 + out_h / 2)
                            ctx.rotate(math.radians(rotation))
                            ctx.translate(-width / 2, -height / 2)
                            matrix = tuple(ctx.get_matrix())
                            draw(presenter, ctx, 0, layout)
                            self.assertEqual(tuple(ctx.get_matrix()), matrix)
                            target.flush()
                            pixels = target.get_data()
                            stride = target.get_stride()
                            # Both corners must contain ink, not a white strip from
                            # painting at the old cache size. Outside stays white.
                            for x, y in ((14, 14), (out_w + 5, out_h + 5)):
                                offset = y * stride + x * 4
                                self.assertEqual(bytes(pixels[offset:offset + 3]), b'\x00' * 3)
                            self.assertEqual(bytes(pixels[:3]), b'\xff' * 3)


if __name__ == '__main__':
    unittest.main()

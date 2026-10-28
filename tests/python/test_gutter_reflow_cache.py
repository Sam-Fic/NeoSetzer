#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''gutter 离屏缓存在编辑器重排期间的正确性（无界面 AST 测试）。

侧栏开合动画每帧改变编辑器列宽，开启自动换行时 GtkTextView 会把整篇文档的
显示行失效重排，正文行的文档 y 全部改变。gutter 是覆盖在 source_view 上的
DrawingArea，自身尺寸不变，若不显式处理就会拿旧 surface 画在新位置上：
表现即「行号慢正文一拍」乃至「行号消失一小段时间」。'''

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
GUTTER = 'setzer/document/gutter/gutter.py'
GUTTER_WIDTH = 60
VIEWPORT_HEIGHT = 600
LINE_HEIGHT = 20
MARGIN_LINES = 15


class FakeCairo:
    '''cairo 替身：只记录 surface/context 的创建，供断言命中与重建路径。'''

    def __init__(self):
        self.Format = SimpleNamespace(ARGB32='ARGB32')
        self.surfaces = []
        self.contexts = []
        outer = self

        class ImageSurface:
            def __init__(self, fmt, w, h):
                self.size = (w, h)
                outer.surfaces.append(self)

        class Context:
            def __init__(self, surface):
                self.surface = surface
                outer.contexts.append(self)

            def save(self):
                pass

            def restore(self):
                pass

            def scale(self, x, y):
                pass

            def rectangle(self, *args):
                pass

            def clip(self):
                pass

            def set_source_surface(self, *args):
                pass

            def paint(self):
                pass

        self.ImageSurface = ImageSurface
        self.Context = Context


class GutterHarness:
    '''按 _draw_cached_gutter 实际读取的属性构造最小 gutter 实例。'''

    def __init__(self, editor_width=800, upper=10000, scroll_top=5000,
                 band_top_y=None):
        self.cairo = FakeCairo()
        self.gutter = self._load()
        self.editor_width = editor_width
        self.upper = upper
        self.scroll_top = scroll_top
        # band 顶文档 y：默认对齐到 scroll_top 上移一个 margin（重排后的正常值）。
        self.band_top_y = (band_top_y if band_top_y is not None
                           else scroll_top - MARGIN_LINES * LINE_HEIGHT)

        text_iter = SimpleNamespace(get_line=lambda: 250)
        self.gutter.source_view = SimpleNamespace(
            get_scale_factor=lambda: 1,
            get_allocated_width=lambda: self.editor_width,
            get_line_at_y=lambda y: (text_iter, True),
            get_line_yrange=lambda it: SimpleNamespace(y=self.band_top_y,
                                                       height=LINE_HEIGHT),
        )
        self.gutter.adjustment = SimpleNamespace(
            get_value=lambda: self.scroll_top,
            get_upper=lambda: self.upper,
        )
        self.ctx = SimpleNamespace(
            save=Mock(), restore=Mock(), translate=Mock(), scale=Mock(),
            set_source_surface=Mock(), paint=Mock())

    def _load(self):
        source = ROOT / GUTTER
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls_node = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'Gutter')
        names = {'_draw_cached_gutter', 'on_editor_size_changed'}
        methods = [node for node in cls_node.body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {'cairo': self.cairo}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
        cls = type('Gutter', (), {node.name: namespace[node.name] for node in methods})

        gutter = cls()
        gutter.line_height = LINE_HEIGHT
        gutter._GUTTER_MARGIN = MARGIN_LINES
        gutter._gutter_cache = None
        gutter.drawing_area = SimpleNamespace(queue_draw=Mock())
        gutter._paint_line_region = Mock()
        return gutter

    def draw(self):
        self.gutter._draw_cached_gutter(self.ctx, GUTTER_WIDTH,
                                        VIEWPORT_HEIGHT, self.scroll_top)

    def painted_surface(self):
        '''最近一次 paint 到主 ctx 的 surface。'''
        return self.ctx.set_source_surface.call_args[0][0]


class TestGeometryFingerprint(unittest.TestCase):
    def test_first_draw_builds_and_publishes_surface(self):
        h = GutterHarness()
        h.draw()
        self.assertEqual(len(h.cairo.surfaces), 1)
        cache = h.gutter._gutter_cache
        self.assertIs(cache[3], h.cairo.surfaces[0])
        self.assertEqual(cache[6], (800, 10000))
        self.assertIs(h.painted_surface(), cache[3])

    def test_small_scroll_hits_cache_without_rebuilding(self):
        h = GutterHarness()
        h.draw()
        first = h.gutter._gutter_cache[3]
        h.gutter._paint_line_region.reset_mock()

        # 几何未变、可视区仍落在缓冲带内 → 纯平移命中，不新建 surface 不重排。
        h.scroll_top = 5040
        h.band_top_y = 5040 - MARGIN_LINES * LINE_HEIGHT
        h.draw()
        self.assertEqual(len(h.cairo.surfaces), 1)
        self.assertIs(h.painted_surface(), first)
        h.gutter._paint_line_region.assert_not_called()

    def test_editor_width_change_forces_full_rebuild(self):
        h = GutterHarness()
        h.draw()
        stale = h.gutter._gutter_cache[3]
        h.gutter._paint_line_region.reset_mock()

        # 侧栏开合动画的一帧：列宽变了，自动换行把整篇文档重排，
        # 旧 surface 里的行号 y 已失效，必须整块重建而不是平移。
        h.editor_width = 780
        h.draw()
        self.assertEqual(len(h.cairo.surfaces), 2)
        self.assertIsNot(h.gutter._gutter_cache[3], stale)
        self.assertEqual(h.gutter._gutter_cache[6], (780, 10000))
        self.assertIs(h.painted_surface(), h.gutter._gutter_cache[3])
        h.gutter._paint_line_region.assert_called_once()

    def test_document_height_change_forces_full_rebuild(self):
        h = GutterHarness()
        h.draw()
        h.upper = 10400
        h.draw()
        self.assertEqual(len(h.cairo.surfaces), 2)
        self.assertEqual(h.gutter._gutter_cache[6], (800, 10400))


class TestCoverageGuard(unittest.TestCase):
    def test_bogus_band_top_falls_back_to_direct_paint(self):
        # 重排进行中 get_line_yrange 可能返回基于未验证布局的估计值：带顶远远
        # 高于可视区，缓冲带盖不住当前视口。此时必须直绘且不写缓存。
        h = GutterHarness(scroll_top=5000, band_top_y=0)
        h.draw()
        self.assertIsNone(h.gutter._gutter_cache)
        self.assertEqual(len(h.cairo.surfaces), 0)
        h.gutter._paint_line_region.assert_called_once_with(
            h.ctx, GUTTER_WIDTH, None, 5000, VIEWPORT_HEIGHT, 5000)

    def test_band_bottom_above_viewport_falls_back_to_direct_paint(self):
        # 带顶低于 scroll_top（合法方向）但带底 3000+1200=4200 够不到视口底
        # 5000+600=5600：整块 surface 落在可视区之外，照它平移就是一片空白。
        h = GutterHarness(scroll_top=5000, band_top_y=3000)
        h.draw()
        self.assertIsNone(h.gutter._gutter_cache)
        h.gutter._paint_line_region.assert_called_once_with(
            h.ctx, GUTTER_WIDTH, None, 5000, VIEWPORT_HEIGHT, 5000)

    def test_missing_band_top_falls_back_to_direct_paint(self):
        h = GutterHarness()

        def boom(y):
            raise RuntimeError('buffer changed')

        h.gutter.source_view = SimpleNamespace(
            get_scale_factor=lambda: 1,
            get_allocated_width=lambda: 800,
            get_line_at_y=boom,
            get_line_yrange=lambda it: SimpleNamespace(y=0, height=LINE_HEIGHT),
        )
        h.draw()
        self.assertIsNone(h.gutter._gutter_cache)
        h.gutter._paint_line_region.assert_called_once_with(
            h.ctx, GUTTER_WIDTH, None, 5000, VIEWPORT_HEIGHT, 5000)


class TestPublishAfterPaint(unittest.TestCase):
    def test_paint_failure_leaves_no_cache_and_repaints_directly(self):
        h = GutterHarness()
        h.gutter._paint_line_region = Mock(side_effect=RuntimeError('concurrent edit'))
        h.draw()
        # 关键：绘制失败时不能把半张 surface 当成有效缓存（否则后续帧命中它，
        # 行号整段时间空白）；应清缓存并对主 ctx 再直绘一次兜底。
        self.assertIsNone(h.gutter._gutter_cache)
        self.assertEqual(h.gutter._paint_line_region.call_count, 2)
        self.assertEqual(h.gutter._paint_line_region.call_args[0][0], h.ctx)

    def test_success_paints_offscreen_before_publishing(self):
        h = GutterHarness()
        h.draw()
        # 行号先画在离屏 surface 的 ctx 上，成功后才写缓存并 paint 到主 ctx。
        self.assertEqual(h.gutter._paint_line_region.call_args[0][0],
                         h.cairo.contexts[0])
        h.ctx.paint.assert_called_once_with()


class TestGeometrySignalRedraw(unittest.TestCase):
    def test_editor_geometry_change_queues_draw(self):
        h = GutterHarness()
        h.gutter.on_editor_size_changed(780, 600)
        h.gutter.drawing_area.queue_draw.assert_called_once_with()

    def test_geometry_change_does_not_drop_cache(self):
        # 缓存是否可用交给指纹判定；信号处理只负责标脏，不能顺手清缓存
        # （否则动画每帧都退化成整块重建）。
        h = GutterHarness()
        h.draw()
        cached = h.gutter._gutter_cache
        h.gutter.on_editor_size_changed(780, 600)
        self.assertIs(h.gutter._gutter_cache, cached)


if __name__ == '__main__':
    unittest.main()

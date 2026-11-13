#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''行号拖动选区的无界面回归测试。

按住行号纵向拖动时，锚点行与当前指针所在行之间的整行应被选中；
指针靠近视口上下边缘时自动滚动，并把选区延伸到滚入视口的新行。
测试用 AST 提取 Gutter 的相关方法，配假 buffer/adjustment/GLib，
不依赖 GTK 显示环境。
'''

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from tests.python import conftest_stub


ROOT = Path(__file__).resolve().parents[2]
GUTTER = 'setzer/document/gutter/gutter.py'
LINE_HEIGHT = 20
VIEWPORT_HEIGHT = 100
LINE_NUMBERS_WIDTH = 40
BOOKMARKS_WIDTH = 20

METHOD_NAMES = {
    'get_cursor_area',
    'on_line_selection_drag_begin',
    'on_line_selection_drag_update',
    'on_line_selection_drag_end',
    'on_line_selection_drag_cancel',
    '_reset_line_selection_drag',
    '_select_line_range',
    '_update_line_selection',
    '_line_at_widget_y',
    '_line_drag_viewport_height',
    '_clamp_line_drag_y',
    '_update_line_drag_autoscroll',
    '_stop_line_drag_autoscroll',
    '_line_drag_autoscroll_timeout',
}


class FakeIter:

    def __init__(self, line, is_end=False):
        self.line = line
        self.is_end = is_end

    def get_line(self):
        return self.line


class FakeBuffer:

    def __init__(self, line_count=10):
        self.line_count = line_count
        self.selection = None

    def get_line_count(self):
        return self.line_count

    def get_iter_at_line(self, line):
        return (True, FakeIter(line))

    def get_end_iter(self):
        return FakeIter(self.line_count - 1, is_end=True)

    def select_range(self, start, end):
        self.selection = (start.line, end.line, end.is_end)


class FakeAdjustment:

    def __init__(self, value=0, lower=0, upper=1000, page_size=VIEWPORT_HEIGHT):
        self.value = value
        self.lower = lower
        self.upper = upper
        self.page_size = page_size

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def get_lower(self):
        return self.lower

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page_size


class FakeSourceView:

    def __init__(self, buffer, adjustment, height=VIEWPORT_HEIGHT):
        self.buffer = buffer
        self.adjustment = adjustment
        self.height = height

    def get_line_at_y(self, y):
        line = int(max(0, y) // LINE_HEIGHT)
        line = min(line, self.buffer.get_line_count() - 1)
        return SimpleNamespace(target_iter=FakeIter(line))

    def get_allocated_height(self):
        return self.height


class GutterHarness:

    def __init__(self, line_count=10, scroll=0):
        self.glib = conftest_stub.make_glib_stub()
        self.glib.source_remove = self.glib.Source.remove
        self.buffer = FakeBuffer(line_count)
        self.adjustment = FakeAdjustment(value=scroll)
        self.source_view = FakeSourceView(self.buffer, self.adjustment)
        self.gutter = self._load()
        self.gutter.source_buffer = self.buffer
        self.gutter.source_view = self.source_view
        self.gutter.adjustment = self.adjustment
        self.gutter.drawing_area = SimpleNamespace(
            get_allocated_height=lambda: VIEWPORT_HEIGHT)
        self.gutter.line_height = LINE_HEIGHT
        self.gutter.line_numbers_visible = True
        self.gutter.line_numbers_width = LINE_NUMBERS_WIDTH
        self.gutter.code_folding_visible = False
        self.gutter.code_folding_width = 0
        self.gutter.bookmarks_width = BOOKMARKS_WIDTH
        self.gutter.cursor_x = None
        self.gutter._LINE_DRAG_EDGE = 16
        self.gutter._LINE_DRAG_SCROLL_INTERVAL_MS = 50
        self.gutter._line_drag_active = False
        self.gutter._line_drag_anchor_line = None
        self.gutter._line_drag_start_y = None
        self.gutter._line_drag_scroll_direction = 0
        self.gutter._line_drag_scroll_id = None

    def _load(self):
        source = ROOT / GUTTER
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls_node = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'Gutter')
        methods = [node for node in cls_node.body
                   if isinstance(node, ast.FunctionDef) and node.name in METHOD_NAMES]
        namespace = {'GLib': self.glib}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
        return type('Gutter', (), {node.name: namespace[node.name]
                                   for node in methods})()


class TestLineSelection(unittest.TestCase):

    def setUp(self):
        self.h = GutterHarness()

    def test_single_line_ends_at_next_line_start(self):
        self.h.gutter._select_line_range(3, 3)
        self.assertEqual(self.h.buffer.selection, (3, 4, False))

    def test_drag_downwards_selects_all_lines_in_between(self):
        self.h.gutter._select_line_range(2, 5)
        self.assertEqual(self.h.buffer.selection, (2, 6, False))

    def test_drag_upwards_uses_reversed_anchor(self):
        self.h.gutter._select_line_range(5, 2)
        self.assertEqual(self.h.buffer.selection, (2, 6, False))

    def test_last_line_ends_at_buffer_end(self):
        self.h.gutter._select_line_range(8, 9)
        self.assertEqual(self.h.buffer.selection, (8, 9, True))

    def test_out_of_range_lines_are_clamped(self):
        self.h.gutter._select_line_range(12, 15)
        self.assertEqual(self.h.buffer.selection, (9, 9, True))


class TestLineAtY(unittest.TestCase):

    def test_scroll_offset_is_added(self):
        h = GutterHarness(scroll=100)
        self.assertEqual(h.gutter._line_at_widget_y(40), 7)

    def test_result_is_clamped_to_buffer(self):
        h = GutterHarness(line_count=10)
        self.assertEqual(h.gutter._line_at_widget_y(-50), 0)
        self.assertEqual(h.gutter._line_at_widget_y(10000), 9)


class TestDragLifecycle(unittest.TestCase):

    def setUp(self):
        self.h = GutterHarness()

    def test_drag_starts_only_in_line_number_area(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.assertTrue(self.h.gutter._line_drag_active)
        self.assertEqual(self.h.gutter._line_drag_anchor_line, 1)

        # 书签区的按下不应进入行选拖动。
        self.h.buffer.selection = None
        self.h.gutter.on_line_selection_drag_begin(None, 60, 30)
        self.assertFalse(self.h.gutter._line_drag_active)
        self.assertIsNone(self.h.gutter._line_drag_anchor_line)

    def test_drag_update_extends_selection(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 60)
        self.assertEqual(self.h.buffer.selection, (1, 5, False))

    def test_drag_near_bottom_edge_starts_autoscroll(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        self.assertEqual(len(self.h.glib._sources), 1)
        self.assertEqual(self.h.gutter._line_drag_scroll_direction, 1)

    def test_autoscroll_moves_viewport_and_keeps_selecting(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        callback = next(iter(self.h.glib._sources.values()))[1]

        callback()
        self.assertGreater(self.h.adjustment.get_value(), 0)
        # 底部行随滚动下移，选区终点跟着延伸。
        self.assertEqual(self.h.buffer.selection[0], 1)
        self.assertGreater(self.h.buffer.selection[1], 5)

    def test_autoscroll_does_not_pass_maximum(self):
        self.h.adjustment.value = 900
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        callback = next(iter(self.h.glib._sources.values()))[1]
        self.assertTrue(callback())
        self.assertEqual(self.h.adjustment.get_value(), 900)

    def test_drag_end_stops_autoscroll_and_resets_state(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        self.assertEqual(len(self.h.glib._sources), 1)

        self.h.gutter.on_line_selection_drag_end(None, 0, 80)
        self.assertEqual(len(self.h.glib._sources), 0)
        self.assertFalse(self.h.gutter._line_drag_active)
        self.assertIsNone(self.h.gutter._line_drag_anchor_line)
        self.assertEqual(self.h.gutter._line_drag_scroll_direction, 0)

    def test_leaving_edge_stops_autoscroll(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        self.assertEqual(len(self.h.glib._sources), 1)

        self.h.gutter.on_line_selection_drag_update(None, 0, 20)
        self.assertEqual(len(self.h.glib._sources), 0)

    def test_cancel_resets_state(self):
        self.h.gutter.on_line_selection_drag_begin(None, 10, 30)
        self.h.gutter.on_line_selection_drag_update(None, 0, 80)
        self.h.gutter.on_line_selection_drag_cancel(None, None)
        self.assertEqual(len(self.h.glib._sources), 0)
        self.assertFalse(self.h.gutter._line_drag_active)


if __name__ == '__main__':
    unittest.main()

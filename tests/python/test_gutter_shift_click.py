#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''Shift+点击行号扩展选区的无界面回归测试。

按住 Shift 点击两个不同的行号时，应选中这两行之间的整行范围；
未按 Shift 的普通点击行为不变（仍只选中单行）。
测试用 AST 提取 Gutter 的相关方法，配假 buffer/事件控制器，
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
LINE_NUMBERS_WIDTH = 40
BOOKMARKS_WIDTH = 20

METHOD_NAMES = {
    'get_cursor_area',
    'on_button_press',
    'on_line_selection_drag_begin',
    'on_line_selection_drag_update',
    'on_line_selection_drag_end',
    'on_line_selection_drag_cancel',
    '_reset_line_selection_drag',
    '_select_line_range',
    '_select_line_range_to',
    '_shift_pressed',
    '_cursor_line',
    '_line_at_widget_y',
    '_stop_line_drag_autoscroll',
    '_invalidate_range_anchor_if_cursor_moved',
}

SHIFT_MASK = 1 << 0
CTRL_MASK = 1 << 2


class FakeIter:

    def __init__(self, line, is_end=False):
        self.line = line
        self.is_end = is_end

    def get_line(self):
        return self.line


class FakeMark:
    '''Gtk.TextMark 替身。

    GTK4 的 Gtk.TextBuffer.get_insert() 返回的是 TextMark 而不是 TextIter，
    直接对它调 get_line() 会抛 AttributeError。替身刻意不提供 get_line()，
    让「用错 API」在测试里立刻暴露，而不是被 except 兜底成 0 行。
    '''

    def __init__(self, buffer):
        self.buffer = buffer


class FakeBuffer:
    '''最小 GtkTextBuffer 替身：记录选区与光标位置。'''

    def __init__(self, line_count=10):
        self.line_count = line_count
        self.selection = None
        self.cursor_line = 0
        self.insert_mark = FakeMark(self)

    def get_line_count(self):
        return self.line_count

    def get_iter_at_line(self, line):
        return (True, FakeIter(line))

    def get_end_iter(self):
        return FakeIter(self.line_count - 1, is_end=True)

    def get_insert(self):
        return self.insert_mark

    def get_iter_at_mark(self, mark):
        assert mark is self.insert_mark
        return FakeIter(self.cursor_line)

    def select_range(self, start, end):
        self.selection = (start.line, end.line, end.is_end)
        # select_range 会把光标放到选区起点。
        self.cursor_line = start.line


class FakeAdjustment:

    def __init__(self, value=0):
        self.value = value

    def get_value(self):
        return self.value


class FakeSourceView:

    def __init__(self, buffer):
        self.buffer = buffer

    def get_line_at_y(self, y):
        line = int(max(0, y) // LINE_HEIGHT)
        line = min(line, self.buffer.get_line_count() - 1)
        return SimpleNamespace(target_iter=FakeIter(line))


class FakeEventController:
    '''模拟事件控制器：记录当前事件的修饰键状态。'''

    def __init__(self, state=0):
        self.state = state

    def get_current_event_state(self):
        return self.state


class FakeGdk:
    '''Gdk 命名空间替身：只提供 SHIFT 掩码与当前事件查询。'''

    ModifierType = SimpleNamespace(SHIFT_MASK=SHIFT_MASK)
    Event = SimpleNamespace(get_current_event=lambda: None)


class GutterHarness:

    def __init__(self, line_count=10, cursor_line=0):
        self.glib = conftest_stub.make_glib_stub()
        self.glib.source_remove = self.glib.Source.remove
        self.buffer = FakeBuffer(line_count)
        self.buffer.cursor_line = cursor_line
        self.adjustment = FakeAdjustment()
        self.source_view = FakeSourceView(self.buffer)
        self.gutter = self._load()
        self.gutter.source_buffer = self.buffer
        self.gutter.source_view = self.source_view
        self.gutter.adjustment = self.adjustment
        self.gutter.line_numbers_visible = True
        self.gutter.line_numbers_width = LINE_NUMBERS_WIDTH
        self.gutter.code_folding_visible = False
        self.gutter.code_folding_width = 0
        self.gutter.bookmarks_width = BOOKMARKS_WIDTH
        self.gutter.hovered_folding_region = None
        self.gutter._line_drag_active = False
        self.gutter._line_drag_anchor_line = None
        self.gutter._line_drag_start_y = None
        self.gutter._line_drag_scroll_direction = 0
        self.gutter._line_drag_scroll_id = None
        self.gutter._range_anchor_line = None
        self.gutter._range_anchor_expected_cursor_line = None
        self.gutter._shift_selection_pending = False

    def _load(self):
        source = ROOT / GUTTER
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls_node = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == 'Gutter')
        methods = [node for node in cls_node.body
                   if isinstance(node, ast.FunctionDef) and node.name in METHOD_NAMES]
        namespace = {'GLib': self.glib, 'Gdk': FakeGdk}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
        return type('Gutter', (), {node.name: namespace[node.name]
                                   for node in methods})()

    def click(self, line, shift=False):
        '''模拟在行号区点击某一行（y 坐标按行高换算）。

        点击会经 select_range 移动插入标记，真实环境里随即触发文档的
        'cursor_position_changed' → Gutter.on_cursor_change。这里显式复现
        这一步：锚点若被自己的选区动作误判为「过期」，Shift+点击就会退化成
        从文档第一行开始选（真实 App 里出现过的 bug）。
        '''
        controller = FakeEventController(SHIFT_MASK if shift else 0)
        self.gutter.on_button_press(controller, 1, 10, line * LINE_HEIGHT + 2)
        self.gutter._invalidate_range_anchor_if_cursor_moved()
        return controller

    def cursor_moved_to(self, line):
        '''模拟用户（键盘/文本区点击）把光标挪到另一行。'''
        self.buffer.cursor_line = line
        self.gutter._invalidate_range_anchor_if_cursor_moved()


class TestShiftClickRange(unittest.TestCase):

    def setUp(self):
        self.h = GutterHarness()

    def test_plain_click_selects_single_line(self):
        self.h.click(3)
        self.assertEqual(self.h.buffer.selection, (3, 4, False))

    def test_shift_click_after_plain_click_selects_range(self):
        self.h.click(2)
        self.h.click(6, shift=True)
        self.assertEqual(self.h.buffer.selection, (2, 7, False))

    def test_two_shift_clicks_select_the_two_clicked_lines(self):
        # 按住 Shift 依次点击两个行号 → 选中这两行之间的范围。
        self.h.click(4, shift=True)
        self.h.click(8, shift=True)
        self.assertEqual(self.h.buffer.selection, (4, 9, False))

    def test_shift_click_upwards_selects_range(self):
        self.h.click(7)
        self.h.click(3, shift=True)
        self.assertEqual(self.h.buffer.selection, (3, 8, False))

    def test_shift_click_without_anchor_extends_from_cursor(self):
        self.h.buffer.cursor_line = 2
        self.h.click(5, shift=True)
        self.assertEqual(self.h.buffer.selection, (2, 6, False))

    def test_shift_click_on_same_line_selects_that_line(self):
        self.h.click(5)
        self.h.click(5, shift=True)
        self.assertEqual(self.h.buffer.selection, (5, 6, False))

    def test_shift_click_to_last_line_ends_at_buffer_end(self):
        self.h.click(0)
        self.h.click(9, shift=True)
        self.assertEqual(self.h.buffer.selection, (0, 9, True))

    def test_repeated_shift_clicks_extend_from_anchor(self):
        self.h.click(2)
        self.h.click(5, shift=True)
        self.h.click(8, shift=True)
        self.assertEqual(self.h.buffer.selection, (5, 9, False))


class TestAnchorInvalidation(unittest.TestCase):

    def setUp(self):
        self.h = GutterHarness()

    def test_cursor_move_invalidates_stale_anchor(self):
        self.h.click(2)
        self.h.cursor_moved_to(6)
        self.h.click(9, shift=True)
        # 锚点已作废，改用当前光标行 6 作为起点。
        self.assertEqual(self.h.buffer.selection, (6, 9, True))

    def test_own_selection_keeps_anchor(self):
        self.h.click(2)
        self.h.click(6, shift=True)
        # gutter 自身的选区动作把光标放到选区起点，不应作废锚点。
        self.assertEqual(self.h.gutter._range_anchor_line, 6)

    def test_plain_click_resets_anchor(self):
        self.h.click(2)
        self.h.click(8, shift=True)
        self.h.click(4)
        self.h.click(9, shift=True)
        # 普通点击重新设定了锚点（行 4）。
        self.assertEqual(self.h.buffer.selection, (4, 9, True))


class TestShiftStateDetection(unittest.TestCase):

    def setUp(self):
        self.h = GutterHarness()

    def test_shift_pressed_detects_shift_mask(self):
        self.assertTrue(self.h.gutter._shift_pressed(
            FakeEventController(SHIFT_MASK)))

    def test_shift_pressed_false_without_shift(self):
        self.assertFalse(self.h.gutter._shift_pressed(
            FakeEventController(CTRL_MASK)))

    def test_shift_pressed_false_without_controller(self):
        self.assertFalse(self.h.gutter._shift_pressed(None))

    def test_shift_click_does_not_start_line_drag(self):
        controller = FakeEventController(SHIFT_MASK)
        self.h.gutter.on_line_selection_drag_begin(controller, 10, 30)
        self.assertFalse(self.h.gutter._line_drag_active)


class TestShiftClickDragInterplay(unittest.TestCase):
    '''Shift+点击的选区不能被随后到来的拖动手势覆盖。'''

    def setUp(self):
        self.h = GutterHarness()

    def _simulate_active_drag(self):
        '''模拟拖动手势已经激活（drag-begin 读不到修饰键的情形）。'''
        self.h.gutter._line_drag_active = True
        self.h.gutter._line_drag_anchor_line = 6
        self.h.gutter._line_drag_start_y = 6 * LINE_HEIGHT

    def test_drag_update_does_not_override_shift_selection(self):
        self.h.click(2)
        self.h.click(6, shift=True)
        self._simulate_active_drag()
        self.h.gutter.on_line_selection_drag_update(None, 0, 40)
        self.assertEqual(self.h.buffer.selection, (2, 7, False))

    def test_drag_end_does_not_collapse_shift_selection(self):
        self.h.click(2)
        self.h.click(6, shift=True)
        self._simulate_active_drag()
        self.h.gutter.on_line_selection_drag_end(None, 0, 0)
        self.assertEqual(self.h.buffer.selection, (2, 7, False))

    def test_plain_click_clears_pending_flag(self):
        self.h.click(2)
        self.h.click(6, shift=True)
        self.h.click(3)
        self.assertFalse(self.h.gutter._shift_selection_pending)


class TestCursorLineApi(unittest.TestCase):
    '''_cursor_line 必须走 get_iter_at_mark(get_insert())。

    GTK4 的 get_insert() 返回 TextMark，对它直接调 get_line() 会抛
    AttributeError；若被 except 兜底成 0 行，锚点会被误判过期。
    '''

    def setUp(self):
        self.h = GutterHarness()
        self.h.buffer.cursor_line = 4

    def test_cursor_line_reads_insert_mark_position(self):
        self.assertEqual(self.h.gutter._cursor_line(), 4)

    def test_own_selection_does_not_invalidate_anchor(self):
        # gutter 自己的 select_range 移动了插入标记（0-based 行 2），
        # 触发的光标变化不得把刚建立的锚点作废。
        self.h.click(2)
        self.h.click(6, shift=True)
        self.assertEqual(self.h.buffer.selection, (2, 7, False))
        self.assertEqual(self.h.gutter._range_anchor_line, 6)


if __name__ == '__main__':
    unittest.main()

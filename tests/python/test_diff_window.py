#!/usr/bin/env python3
# coding: utf-8
#
# Git 侧栏并排 diff 窗口：行号映射 + 外观/窗口接线的守护。
#
# 纯函数（align_lines / line_numbers）用 ast 抽出来在隔离命名空间 exec，
# 不 import 模块本体——模块顶层 `import gi`，headless CI 下不允许。
# 窗口接线（Adw.Window、自动换行、卡片、标准关闭按钮）用源码断言，
# 与 test_git_pdf_diff_wiring 的写法一致。

import ast
import difflib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIFF_DIALOG = 'setzer/workspace/sidebar/git/diff_dialog.py'
GIT_SECTION = 'setzer/workspace/sidebar/git/git_section.py'


def load_functions(relative_path, names):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    namespace = {'difflib': difflib}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(source), 'exec'), namespace)
            found[node.name] = namespace[node.name]
    missing = set(names) - set(found)
    if missing:
        raise AssertionError('missing functions: {}'.format(sorted(missing)))
    return found


_FUNCTIONS = load_functions(DIFF_DIALOG, ['align_lines', 'line_numbers'])
align_lines = _FUNCTIONS['align_lines']
line_numbers = _FUNCTIONS['line_numbers']


class AlignLinesTest(unittest.TestCase):

    def test_aligns_equal_change_delete_insert(self):
        rows = align_lines('a\nb\nc\nd', 'a\nB\nc\nd\ne')
        kinds = [kind for _, _, kind in rows]
        self.assertEqual(kinds[:2], ['equal', 'change'])
        self.assertIn('insert', kinds)
        # 两栏行数恒等，且同一行在两栏里指同一对齐行。
        self.assertTrue(all(len(row) == 3 for row in rows))
        self.assertEqual(rows[1], ('b', 'B', 'change'))
        self.assertEqual(rows[-1], (None, 'e', 'insert'))

    def test_missing_side_is_none(self):
        rows = align_lines('only-old', '')
        self.assertEqual(rows, [('only-old', None, 'delete')])


class LineNumbersTest(unittest.TestCase):

    def test_real_file_line_numbers_skip_missing_side(self):
        rows = align_lines('a\nb\nc', 'a\nc')
        # 旧侧三行齐全；新侧删掉了一行，缺失处留空但行号不跳号。
        self.assertEqual(line_numbers(rows, 'old'), [1, 2, 3])
        self.assertEqual(line_numbers(rows, 'new'), [1, None, 2])

    def test_insert_side_numbering(self):
        rows = align_lines('a\nc', 'a\nb\nc')
        self.assertEqual(line_numbers(rows, 'old'), [1, None, 2])
        self.assertEqual(line_numbers(rows, 'new'), [1, 2, 3])

    def test_both_sides_have_same_length(self):
        rows = align_lines('a\nb\nc\nd\ne', 'a\nx\ne\nf')
        self.assertEqual(len(line_numbers(rows, 'old')),
                         len(line_numbers(rows, 'new')))


class WindowWiringTest(unittest.TestCase):

    def setUp(self):
        self.source = (ROOT / DIFF_DIALOG).read_text(encoding='utf-8')

    def test_is_standard_window(self):
        '''标准窗口（Adw.Window），不是模态 Adw.Dialog。'''
        self.assertIn('class DiffWindow(Adw.Window):', self.source)
        self.assertNotIn('class DiffWindow(Adw.Dialog)', self.source)
        self.assertNotIn('set_show_end_title_buttons(False)', self.source)
        self.assertIn('Adw.HeaderBar()', self.source)

    def test_follows_line_wrapping_preference(self):
        '''自动换行跟随 preferences/enable_line_wrapping，且实时响应。'''
        self.assertIn("'enable_line_wrapping'", self.source)
        self.assertIn("'settings_changed'", self.source)
        self.assertIn('Gtk.WrapMode.WORD_CHAR', self.source)

    def test_uses_rounded_card(self):
        self.assertIn("add_css_class('diff-card')", self.source)
        self.assertIn('Gtk.Overflow.HIDDEN', self.source)

    def test_draws_gutter_and_monospace_source_view(self):
        '''行号自绘、正文用 GtkSource.View（与主编辑器同源外观）。'''
        self.assertIn('GtkSource.View.new_with_buffer', self.source)
        self.assertIn('set_show_line_numbers(False)', self.source)
        self.assertIn('Gtk.DrawingArea()', self.source)

    def test_line_numbers_compensate_view_top_margin(self):
        '''get_iter_location 是缓冲坐标，行号必须补上 view 的 top-margin，
        否则整列行号会比正文高一截。'''
        start = self.source.index('def _draw_numbers')
        end = self.source.index('\n    def ', start + 1)
        block = self.source[start:end]
        self.assertIn('get_top_margin()', block)
        self.assertIn('location.y - scroll_top + top_margin', block)

    def test_wrapped_rows_show_newline_symbol(self):
        '''自动换行的续行画 newline-symbolic（与主编辑器 gutter 一致）。'''
        self.assertIn("'newline-symbolic'", self.source)
        self.assertIn('forward_display_line', self.source)
        self.assertIn('_draw_newline_symbol', self.source)

    def test_git_section_presents_window(self):
        source = (ROOT / GIT_SECTION).read_text(encoding='utf-8')
        self.assertIn('DiffWindow(', source)
        self.assertNotIn('DiffDialog(', source)


if __name__ == '__main__':
    unittest.main()

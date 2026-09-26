#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''tikz_region_finder 的纯逻辑测试：block 配对形态、嵌套取舍、verbatim 排除。'''

import re
import unittest

from setzer.document.snippet_preview.tikz_region_finder import find_tikzpicture_at


def _blocks_for(text):
    '''按 parser_latex 的语义产出 symbols['blocks'] 形态（无 gi 依赖的替身）。

    每条为 [begin_offset, end_offset, begin_line, end_line, env_name]，
    配对按环境名各自 LIFO——内嵌的 scope / axis 因此不影响外层
    tikzpicture 的配对结果。未闭合的块 end_offset 保持 None 且不进列表。
    '''
    stacks = dict()
    blocks = list()
    for match in re.finditer(
            r'\\(begin|end)\s*\{([A-Za-z0-9@]+\*?)\}', text):
        kind, env = match.group(1), match.group(2)
        if kind == 'begin':
            stacks.setdefault(env, list()).append(
                [match.start(), None, 0, 0])
        else:
            stack = stacks.get(env)
            if stack:
                block = stack.pop()
                block[1] = match.start()
                block.append(env)
                blocks.append(block)
    return sorted(blocks, key=lambda block: block[0])


class FindTikzpictureAtTest(unittest.TestCase):

    def test_inside_figure(self):
        text = (
            '\\begin{tikzpicture}\n'
            '\\draw (0,0) -- (1,1);\n'
            '\\end{tikzpicture}\n'
        )
        region = find_tikzpicture_at(_blocks_for(text), text, 20)
        self.assertIsNotNone(region)
        self.assertEqual(region.start, 0)
        self.assertEqual(region.raw, text.rstrip('\n'))
        self.assertEqual(region.env, 'tikzpicture')

    def test_cursor_on_begin_marker_is_inside(self):
        text = 'x\n\\begin{tikzpicture}\n\\end{tikzpicture}\n'
        blocks = _blocks_for(text)
        self.assertIsNotNone(find_tikzpicture_at(blocks, text, 2))
        # 末尾 } 本身仍在区域内（end 是它的后一位），其后的换行不算。
        self.assertIsNotNone(find_tikzpicture_at(blocks, text, len(text) - 2))
        self.assertIsNone(find_tikzpicture_at(blocks, text, len(text) - 1))

    def test_cursor_before_begin_is_outside(self):
        text = 'x\n\\begin{tikzpicture}\n\\end{tikzpicture}\n'
        self.assertIsNone(find_tikzpicture_at(_blocks_for(text), text, 1))

    def test_cursor_after_close_brace_is_outside(self):
        text = '\\begin{tikzpicture}\n\\end{tikzpicture}\nrest'
        blocks = _blocks_for(text)
        end = text.index('}', text.index('\\end')) + 1
        self.assertIsNotNone(find_tikzpicture_at(blocks, text, end - 1))
        self.assertIsNone(find_tikzpicture_at(blocks, text, end))

    def test_between_two_figures_returns_none(self):
        text = (
            '\\begin{tikzpicture}\n\\draw (0,0);\n\\end{tikzpicture}\n'
            'middle text\n'
            '\\begin{tikzpicture}\n\\draw (1,1);\n\\end{tikzpicture}\n'
        )
        middle = text.index('middle')
        self.assertIsNone(find_tikzpicture_at(_blocks_for(text), text, middle))
        second = find_tikzpicture_at(_blocks_for(text), text, len(text) - 5)
        self.assertIsNotNone(second)
        self.assertTrue(second.raw.startswith('\\begin{tikzpicture}\n\\draw (1,1)'))

    def test_nested_scope_keeps_outer_figure(self):
        text = (
            '\\begin{tikzpicture}\n'
            '\\begin{scope}[shift={(1,0)}]\n'
            '\\draw (0,0) -- (1,1);\n'
            '\\end{scope}\n'
            '\\end{tikzpicture}\n'
        )
        region = find_tikzpicture_at(_blocks_for(text), text, text.index('\\draw'))
        self.assertIsNotNone(region)
        self.assertEqual(region.start, 0)
        self.assertEqual(region.end, len(text) - 1)
        self.assertIn('\\begin{scope}', region.raw)

    def test_nested_tikzpicture_prefers_inner(self):
        text = (
            '\\begin{tikzpicture}\n'
            '\\begin{tikzpicture}\n'
            '\\draw (0,0) -- (1,1);\n'
            '\\end{tikzpicture}\n'
            '\\end{tikzpicture}\n'
        )
        blocks = _blocks_for(text)
        inner = find_tikzpicture_at(blocks, text, text.index('\\draw'))
        self.assertIsNotNone(inner)
        self.assertEqual(inner.start, text.index('\\begin{tikzpicture}', 1))
        outer = find_tikzpicture_at(blocks, text, 0)
        self.assertIsNotNone(outer)
        self.assertEqual(outer.start, 0)
        self.assertEqual(outer.end, len(text) - 1)

    def test_unclosed_figure_returns_none(self):
        text = '\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n'
        self.assertIsNone(find_tikzpicture_at(_blocks_for(text), text, 30))

    def test_optional_argument_is_part_of_raw(self):
        text = (
            '\\begin{tikzpicture}[scale=0.5, every node/.style={draw}]\n'
            '\\draw (0,0) -- (1,1);\n'
            '\\end{tikzpicture}\n'
        )
        region = find_tikzpicture_at(_blocks_for(text), text, 60)
        self.assertIsNotNone(region)
        self.assertTrue(region.raw.startswith(
            '\\begin{tikzpicture}[scale=0.5, every node/.style={draw}]'))

    def test_inside_lstlisting_is_ignored(self):
        text = (
            '\\begin{lstlisting}\n'
            '\\begin{tikzpicture}\n'
            '\\draw (0,0) -- (1,1);\n'
            '\\end{tikzpicture}\n'
            '\\end{lstlisting}\n'
        )
        self.assertIsNone(find_tikzpicture_at(_blocks_for(text), text, 40))

    def test_inside_commented_verbatim_is_ignored(self):
        text = (
            '\\begin{verbatim}\n'
            '\\begin{tikzpicture}\n'
            '\\end{tikzpicture}\n'
            '\\end{verbatim}\n'
            '\\begin{tikzpicture}\n'
            '\\draw (2,2);\n'
            '\\end{tikzpicture}\n'
        )
        blocks = _blocks_for(text)
        self.assertIsNone(find_tikzpicture_at(blocks, text, 30))
        region = find_tikzpicture_at(blocks, text, len(text) - 3)
        self.assertIsNotNone(region)
        self.assertIn('\\draw (2,2)', region.raw)

    def test_other_environments_are_ignored(self):
        text = (
            '\\begin{figure}\n'
            '\\begin{tikzpicture}\n\\draw (0,0);\n\\end{tikzpicture}\n'
            '\\caption{x}\n'
            '\\end{figure}\n'
        )
        blocks = _blocks_for(text)
        self.assertIsNotNone(find_tikzpicture_at(blocks, text, 25))
        # 光标在图环境但不在 tikzpicture 内（caption 处）不返回区域。
        self.assertIsNone(find_tikzpicture_at(blocks, text, text.index('\\caption')))

    def test_empty_and_short_blocks_are_tolerated(self):
        self.assertIsNone(find_tikzpicture_at([], 'abc', 1))
        self.assertIsNone(find_tikzpicture_at([[0, 5]], 'abc', 1))


if __name__ == '__main__':
    unittest.main()

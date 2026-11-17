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

'''source_blocks 整块扩展单测（纯逻辑，gi-free）。

契约：PDF 选区反向映射时，公式/图片/表格的一部分 → 整个白名单环境块；
普通文本不扩（整行语义由调用方实现）。嵌套取最内层；inline math 不扩；
$$ 开/闭配对必须全文件状态机判（局部上扫会把上一公式的闭合 $$ 误认成开）。
'''

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from setzer.ai_fix import source_blocks


def lines(*text):
    return [t + '\n' for t in text]


class PlainLineTest(unittest.TestCase):

    def test_plain_text_is_not_expanded(self):
        src = lines('hello world', 'second line')
        self.assertEqual(source_blocks.expand_to_block(src, 1),
                         (1, 1, 'line', None))

    def test_inline_math_is_not_expanded(self):
        src = lines('Energy $E=mc^2$ is famous.')
        self.assertEqual(source_blocks.expand_to_block(src, 1),
                         (1, 1, 'line', None))

    def test_non_whitelist_environment_is_not_expanded(self):
        src = lines('\\begin{itemize}', '\\item a', '\\end{itemize}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (2, 2, 'line', None))

    def test_empty_source_returns_line(self):
        self.assertEqual(source_blocks.expand_to_block([], 3),
                         (3, 3, 'line', None))

    def test_out_of_range_line_is_clamped(self):
        src = lines('\\begin{equation}', 'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.expand_to_block(src, 99),
                         (1, 3, 'environment', 'equation'))


class EnvironmentTest(unittest.TestCase):

    def test_figure_whole_block(self):
        src = lines('intro',
                    '\\begin{figure}',
                    '\\includegraphics{a.png}',
                    '\\caption{cap}',
                    '\\end{figure}',
                    'outro')
        self.assertEqual(source_blocks.expand_to_block(src, 3),
                         (2, 5, 'environment', 'figure'))
        # 边界行（begin/end 本身）也在块内
        self.assertEqual(source_blocks.expand_to_block(src, 2)[0:2], (2, 5))
        self.assertEqual(source_blocks.expand_to_block(src, 5)[0:2], (2, 5))
        # 块外不扩
        self.assertEqual(source_blocks.expand_to_block(src, 6),
                         (6, 6, 'line', None))

    def test_nested_takes_innermost(self):
        src = lines('\\begin{table}',
                    '\\begin{tabular}{cc}',
                    'a & b \\\\',
                    '\\end{tabular}',
                    '\\end{table}')
        self.assertEqual(source_blocks.expand_to_block(src, 3),
                         (2, 4, 'environment', 'tabular'))
        # table 独有行（caption 位置）落外层
        self.assertEqual(source_blocks.expand_to_block(src, 1),
                         (1, 5, 'environment', 'table'))

    def test_starred_variants(self):
        src = lines('\\begin{figure*}', 'x', '\\end{figure*}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (1, 3, 'environment', 'figure*'))
        src = lines('\\begin{align*}', 'x &= 1 \\\\', '\\end{align*}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (1, 3, 'environment', 'align*'))

    def test_unclosed_environment_is_not_expanded(self):
        src = lines('\\begin{equation}', 'x=1')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (2, 2, 'line', None))

    def test_commented_delimiters_are_ignored(self):
        src = lines('% \\begin{figure}', 'plain text', '% \\end{figure}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (2, 2, 'line', None))

    def test_escaped_percent_does_not_start_comment(self):
        src = lines('\\begin{equation}', 'x = 1 \\% note', '\\end{equation}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (1, 3, 'environment', 'equation'))


class DisplayMathTest(unittest.TestCase):

    def test_bracket_math(self):
        src = lines('text', '\\[', 'E = mc^2', '\\]', 'more')
        self.assertEqual(source_blocks.expand_to_block(src, 3),
                         (2, 4, 'displaymath', None))

    def test_dollar_dollar_pairing(self):
        src = lines('$$', 'a=b', '$$', 'plain', '$$', 'c=d', '$$')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (1, 3, 'displaymath', None))
        # 第二个公式：闭合 $$ 不能被误认成开
        self.assertEqual(source_blocks.expand_to_block(src, 6),
                         (5, 7, 'displaymath', None))
        self.assertEqual(source_blocks.expand_to_block(src, 4),
                         (4, 4, 'line', None))

    def test_same_line_dollar_dollar(self):
        src = lines('before $$x=1$$ after')
        self.assertEqual(source_blocks.expand_to_block(src, 1),
                         (1, 1, 'displaymath', None))

    def test_unclosed_math_is_not_expanded(self):
        src = lines('\\[', 'x=1')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (2, 2, 'line', None))

    def test_math_inside_environment_takes_innermost(self):
        src = lines('\\begin{figure}', '$$x=1$$', '\\end{figure}')
        self.assertEqual(source_blocks.expand_to_block(src, 2),
                         (2, 2, 'displaymath', None))


class ComputeRangesTest(unittest.TestCase):

    def test_ranges_are_one_based_closed(self):
        src = lines('\\begin{align}', 'x &= 1', '\\end{align}')
        self.assertEqual(source_blocks.compute_block_ranges(src),
                         [(1, 3, 'environment', 'align')])

    def test_mismatched_end_takes_nearest_same_name(self):
        # 跨层错配容忍：end{figure} 向下找最近同名 begin。
        src = lines('\\begin{figure}', '\\begin{equation}', 'x',
                    '\\end{figure}')
        ranges = source_blocks.compute_block_ranges(src)
        self.assertIn((1, 4, 'environment', 'figure'), ranges)


if __name__ == '__main__':
    unittest.main()
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


class NearestContentLineTest(unittest.TestCase):
    '''双向跳转落点重定向：块边界行 → 块内最近内容行。'''

    def test_end_equation_redirects_to_content(self):
        # 公式内点击 synctex 常返回 \end{equation}，应落到内容行。
        src = lines('\\begin{equation}', 'E = mc^2', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 3), 2)

    def test_begin_align_redirects_to_first_content(self):
        src = lines('\\begin{align}', 'x &= 1 \\\\', 'y &= 2', '\\end{align}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 2)
        self.assertEqual(source_blocks.nearest_content_line(src, 4), 3)

    def test_tabular_boundary_skips_hline(self):
        # \hline 无 synctex 记录，重定向过去跳转依旧落空。
        src = lines('\\begin{tabular}{|c|c|}', '\\hline', 'alpha & beta \\\\',
                    '\\hline', '\\end{tabular}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 5), 3)

    def test_figure_boundary_skips_centering(self):
        src = lines('\\begin{figure}[h]', '\\centering',
                    '\\includegraphics{a}', '\\caption{cap}', '\\end{figure}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 5), 4)

    def test_skips_blank_and_comment_lines(self):
        src = lines('\\begin{equation}', '', '% note', 'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 4)

    def test_content_line_unchanged(self):
        src = lines('\\begin{equation}', 'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 2)

    def test_line_outside_block_unchanged(self):
        src = lines('text', '\\begin{equation}', 'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 1)

    def test_inline_boundary_content_unchanged(self):
        # 行内混有正文内容不算纯边界行。
        src = lines('\\begin{equation} x=1 \\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 1)

    def test_empty_block_unchanged(self):
        src = lines('\\begin{equation}', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 1)

    def test_out_of_range_unchanged(self):
        src = lines('\\begin{equation}', 'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 99), 99)
        self.assertEqual(source_blocks.nearest_content_line([], 1), 1)

    def test_displaymath_delimiter_redirects(self):
        src = lines('\\[', 'x=1', '\\]')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 2)
        self.assertEqual(source_blocks.nearest_content_line(src, 3), 2)

    def test_label_line_skipped_as_redirect_target(self):
        # \label 行 strip 后剩 label key，但无可见输出、synctex 记录是
        # 周围段落盒：嵌套公式（equation+label+cases）重定向必须跳过它。
        src = lines('\\begin{equation}', '\\label{eq:x}', '|x| =',
                    '\\begin{cases}', 'x, & x \\ge 0', '\\end{cases}',
                    '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 7), 5)

    def test_cursor_on_label_line_redirects(self):
        # 光标停在 \label 行上正向同步：同样重定向到内容行。
        src = lines('\\begin{equation}', '\\label{eq:x}', 'x=1',
                    '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 3)

    def test_nonumber_and_notag_skipped(self):
        src = lines('\\begin{align}', '\\nonumber', 'x=1', '\\notag',
                    '\\end{align}')
        self.assertEqual(source_blocks.nearest_content_line(src, 1), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 5), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 3)

    def test_multiple_no_output_commands_on_one_line(self):
        src = lines('\\begin{equation}', '\\label{eq:x} \\nonumber',
                    'x=1', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 3)

    def test_symbol_command_line_is_content(self):
        # \vdots/\cdots 等符号命令 strip 后为空，但有可见输出与自己的
        # synctex 记录（实测点阵行盒）：可作重定向目标，自身不重定向。
        src = lines('\\begin{align}', 'a_{11} & a_{12}', '\\vdots & \\vdots',
                    '\\end{align}')
        self.assertEqual(source_blocks.nearest_content_line(src, 4), 3)
        self.assertEqual(source_blocks.nearest_content_line(src, 3), 3)

    def test_mixed_content_and_label_unchanged(self):
        src = lines('\\begin{equation}', 'x = 1 \\label{eq:x}',
                    '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 2)

    def test_label_outside_block_unchanged(self):
        src = lines('text', '\\label{sec:x}', 'more text')
        self.assertEqual(source_blocks.nearest_content_line(src, 2), 2)

    def test_tag_line_is_content(self):
        # \tag 产生可见的自定义编号，不是无输出行。
        src = lines('\\begin{equation}', 'x=1 \\tag{3}', '\\end{equation}')
        self.assertEqual(source_blocks.nearest_content_line(src, 3), 2)


class IsNoOutputLineTest(unittest.TestCase):

    def test_no_output_commands(self):
        for s in ('\\label{eq:x}', '\\nonumber', '\\notag',
                  '\\label{a} \\nonumber', '\\index{term}',
                  '\\vspace{1em}', '\\hspace*{2cm}', '\\phantom{x}'):
            self.assertTrue(source_blocks.is_no_output_line(s), s)

    def test_lines_with_visible_output(self):
        for s in ('x \\label{eq:x}', '\\vdots', '\\alpha', '\\tag{3}',
                  'x=1', '\\text{if}', ''):
            self.assertFalse(source_blocks.is_no_output_line(s), s)


if __name__ == '__main__':
    unittest.main()
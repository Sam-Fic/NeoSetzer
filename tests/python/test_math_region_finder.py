#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''math_region_finder 的纯逻辑测试：定界符/环境全覆盖、转义、注释、
verbatim 跳过、嵌套环境、EOF 未闭合丢弃、offset 往返与二分查询。'''

import unittest

from setzer.document.snippet_preview.math_region_finder import (
    MathRegion,
    find_math_regions,
    find_region_at,
    scan_verbatim_spans,
)


class ScanVerbatimSpansTest(unittest.TestCase):

    def test_no_verbatim(self):
        self.assertEqual(scan_verbatim_spans('\\begin{tikzpicture}x\\end{tikzpicture}'),
                         [])

    def test_span_covers_markers_themselves(self):
        text = 'a\\begin{lstlisting}\ncode\n\\end{lstlisting}b'
        start, end = scan_verbatim_spans(text)[0]
        self.assertEqual(text[start:end],
                         '\\begin{lstlisting}\ncode\n\\end{lstlisting}')

    def test_multiple_spans_are_ordered(self):
        text = ('\\begin{verbatim}x\\end{verbatim} mid '
                '\\begin{minted}y\\end{minted}')
        spans = scan_verbatim_spans(text)
        self.assertEqual(len(spans), 2)
        self.assertLess(spans[0][0], spans[1][0])
        self.assertTrue(all(0 <= start < end <= len(text)
                            for start, end in spans))

    def test_starred_variant_is_matched(self):
        text = '\\begin{verbatim*}x\\end{verbatim*}'
        self.assertEqual(len(scan_verbatim_spans(text)), 1)

    def test_unclosed_extends_to_end(self):
        text = 'a\\begin{verbatim}never closed'
        self.assertEqual(scan_verbatim_spans(text),
                         [(text.index('\\begin{verbatim}'), len(text))])

    def test_non_verbatim_environment_is_ignored(self):
        text = '\\begin{equation}x\\end{equation}'
        self.assertEqual(scan_verbatim_spans(text), [])


class FindMathRegionsTest(unittest.TestCase):

    def regions(self, text):
        return find_math_regions(text)

    def raws(self, text):
        return [r.raw for r in self.regions(text)]

    def test_inline_dollar(self):
        self.assertEqual(self.raws('hello $x+y$ world'), ['$x+y$'])

    def test_multiple_inline(self):
        self.assertEqual(self.raws('$a$ and $b$'), ['$a$', '$b$'])

    def test_display_dollar(self):
        self.assertEqual(self.raws('$$\n\\alpha\n$$'), ['$$\n\\alpha\n$$'])

    def test_bracket_display(self):
        self.assertEqual(self.raws('text \\[ E=mc^2 \\] end'), ['\\[ E=mc^2 \\]'])

    def test_paren_inline(self):
        self.assertEqual(self.raws(r'text \( a+b \) end'), [r'\( a+b \)'])

    def test_equation_environment(self):
        text = 'pre\n\\begin{equation}\n  x = y\n\\end{equation}\npost'
        self.assertEqual(
            self.raws(text),
            ['\\begin{equation}\n  x = y\n\\end{equation}'])

    def test_starred_and_variants(self):
        for env in ('align*', 'gather', 'multline*', 'eqnarray*', 'alignat*'):
            with self.subTest(env=env):
                text = '\\begin{' + env + '}x\\end{' + env + '}'
                self.assertEqual(self.raws(text), [text])

    def test_nested_environment_outermost(self):
        text = ('\\begin{equation}\n\\begin{aligned}\na &= b\n'
                '\\end{aligned}\n\\end{equation}')
        regions = self.regions(text)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].raw, text)
        self.assertEqual(regions[0].kind, 'display')

    def test_dollar_inside_environment_does_not_close(self):
        text = '\\begin{gather} $x$ \\end{gather}'
        self.assertEqual(self.raws(text), [text])

    def test_escaped_dollar_not_delimiter(self):
        self.assertEqual(self.raws(r'cost is \$5 and $x$'), ['$x$'])

    def test_double_backslash_then_dollar_is_delimiter(self):
        # \\ 是换行命令，其后 $ 正常开启数学。
        self.assertEqual(self.raws('a\\\\$x$'), ['$x$'])

    def test_escaped_percent_not_comment(self):
        self.assertEqual(self.raws(r'100\% of $x$'), ['$x$'])

    def test_comment_hides_math(self):
        self.assertEqual(self.raws('hello % $x$ world\n$y$'), ['$y$'])

    def test_comment_inside_math_does_not_break_pairing(self):
        # TeX 语义：% 吞掉行尾换行，数学在下一行继续。
        self.assertEqual(self.raws('$a % comment\n+b$'), ['$a % comment\n+b$'])

    def test_verbatim_skipped(self):
        text = r'\begin{verbatim}$x$ \[y\]\end{verbatim}' + '\n$z$'
        self.assertEqual(self.raws(text), ['$z$'])

    def test_unclosed_dollar_dropped(self):
        self.assertEqual(self.regions('math $x without close'), [])

    def test_unclosed_environment_dropped(self):
        self.assertEqual(self.regions('\\begin{equation}\nx = 1'), [])

    def test_unclosed_verbatim_eats_rest(self):
        self.assertEqual(self.regions('\\begin{verbatim}$x$'), [])

    def test_stray_close_ignored(self):
        self.assertEqual(self.raws(r'\end{align} \] \) $x$'), ['$x$'])

    def test_empty_text(self):
        self.assertEqual(self.regions(''), [])

    def test_offset_roundtrip(self):
        text = 'a $x$ b \\[y\\]\n\\begin{align}z\\end{align}'
        for region in self.regions(text):
            self.assertEqual(text[region.start:region.end], region.raw)

    def test_regions_sorted_and_disjoint(self):
        text = '$a$ \\[b\\] $$c$$ \\begin{align}d\\end{align}'
        regions = self.regions(text)
        for prev, cur in zip(regions, regions[1:]):
            self.assertLess(prev.end, cur.start)
        self.assertEqual([r.kind for r in regions],
                         ['inline', 'display', 'display', 'display'])

    def test_line_break_inside_inline_math(self):
        # $ 对可跨行配对（TeX 允许，math 不因换行结束）。
        self.assertEqual(self.raws('$a\nb$'), ['$a\nb$'])


class FindRegionAtTest(unittest.TestCase):

    def setUp(self):
        self.text = 'ab $x+y$ cd'
        self.regions = find_math_regions(self.text)

    def test_inside(self):
        region = find_region_at(self.regions, self.text.index('x'))
        self.assertIsNotNone(region)
        self.assertEqual(region.raw, '$x+y$')

    def test_at_start_offset_is_inside(self):
        region = find_region_at(self.regions, self.text.index('$'))
        self.assertIsNotNone(region)

    def test_end_offset_is_exclusive(self):
        self.assertIsNone(find_region_at(self.regions, self.text.index('$x+y$') + len('$x+y$')))

    def test_outside(self):
        self.assertIsNone(find_region_at(self.regions, 0))
        self.assertIsNone(find_region_at(self.regions, len(self.text) - 1))

    def test_empty_regions(self):
        self.assertIsNone(find_region_at([], 5))


if __name__ == '__main__':
    unittest.main()

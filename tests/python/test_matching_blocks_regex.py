#!/usr/bin/env python3
# coding: utf-8

# 单元测试：setzer.document.update_matching_blocks.begin_end_match
#
# find_cursor_in_begin_end 取代了原 _BEGIN_END_REGEX + %•% sentinel 注入。
# 本测试覆盖：
# - 光标在 \begin{...}/\end{...} 大括号内时正确定位（before/after 切分）
# - 光标在括号外不命中
# - 用户文本含 %•%（旧 sentinel）不影响匹配（标记注入已消除）
# - 行首缩进 / \begin 前有文本时正确定位 backslash_offset
# - 多个 \begin/\end 时取最右一个包含光标的
# - 未闭合大括号不命中
# - 内容含 { [ ( 时不命中（与原正则 [^\{\[\(] 语义一致）

import unittest

from setzer.document.update_matching_blocks.begin_end_match import (
    find_cursor_in_begin_end,
)


class TestBasicMatch(unittest.TestCase):

    def test_match_begin_cursor_at_end_of_content(self):
        # \begin{foo} 光标在 foo 与 } 之间（offset 11，foo 之后）
        # line: \begin{foo}  (positions 0-6: \begin{, 7-9: foo, 10: })
        # cursor offset 10 = between 'o' (pos 9) and '}' (pos 10)
        line = '\\begin{foo}'
        result = find_cursor_in_begin_end(line, 10)
        self.assertIsNotNone(result)
        begin_or_end, before, after, bs_off = result
        self.assertEqual(begin_or_end, 'begin')
        self.assertEqual(before, 'foo')
        self.assertEqual(after, '')
        self.assertEqual(bs_off, 0)

    def test_match_end_cursor_at_end_of_content(self):
        line = '\\end{bar}'
        # cursor offset 8 = between 'r' (pos 7) and '}' (pos 8)
        result = find_cursor_in_begin_end(line, 8)
        self.assertIsNotNone(result)
        begin_or_end, before, after, bs_off = result
        self.assertEqual(begin_or_end, 'end')
        self.assertEqual(before, 'bar')
        self.assertEqual(after, '')

    def test_match_with_text_after_cursor(self):
        # \begin{foo|bar} 光标在 foo 与 bar 之间
        line = '\\begin{foobar}'
        # positions: 0-6 \begin{, 7-12 foobar, 13 }
        # cursor offset 10 = between 'o' (pos 9) and 'b' (pos 10)
        result = find_cursor_in_begin_end(line, 10)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, 'foo')
        self.assertEqual(after, 'bar')

    def test_match_with_text_before_cursor(self):
        # \begin{foo|bar} 光标在 foo 中间
        line = '\\begin{foobar}'
        # cursor offset 9 = between 'o' (pos 8) and 'o' (pos 9)
        result = find_cursor_in_begin_end(line, 9)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, 'fo')
        self.assertEqual(after, 'obar')

    def test_match_cursor_right_after_open_brace(self):
        # \begin{|foo} 光标紧贴 { 之后
        line = '\\begin{foo}'
        # cursor offset 7 = between '{' (pos 6) and 'f' (pos 7)
        result = find_cursor_in_begin_end(line, 7)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, '')
        self.assertEqual(after, 'foo')

    def test_match_cursor_right_before_close_brace(self):
        # \begin{foo|} 光标紧贴 } 之前
        line = '\\begin{foo}'
        # cursor offset 10 = between 'o' (pos 9) and '}' (pos 10)
        result = find_cursor_in_begin_end(line, 10)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, 'foo')
        self.assertEqual(after, '')


class TestNoMatch(unittest.TestCase):

    def test_cursor_outside_braces_no_match(self):
        # 光标在 } 之后
        line = '\\begin{foo}'
        result = find_cursor_in_begin_end(line, 11)
        self.assertIsNone(result)

    def test_cursor_before_begin_no_match(self):
        # 光标在 \begin 之前
        line = '\\begin{foo}'
        result = find_cursor_in_begin_end(line, 0)
        self.assertIsNone(result)

    def test_no_begin_or_end_no_match(self):
        # 普通命令不匹配
        line = '\\textbf{foo}'
        result = find_cursor_in_begin_end(line, 10)
        self.assertIsNone(result)

    def test_plain_text_no_match(self):
        line = 'just some text'
        result = find_cursor_in_begin_end(line, 5)
        self.assertIsNone(result)

    def test_unclosed_brace_no_match(self):
        # 未闭合的 \begin{ 不应匹配（无 closing }）
        line = '\\begin{foo'
        result = find_cursor_in_begin_end(line, 9)
        self.assertIsNone(result)

    def test_forbidden_char_in_content_before_cursor_no_match(self):
        # 内容含 { （在光标之前）→ 不匹配
        line = '\\begin{a{b}'
        # cursor 在 b 与 } 之间 (offset 10)
        result = find_cursor_in_begin_end(line, 10)
        self.assertIsNone(result)

    def test_forbidden_char_in_content_after_cursor_no_match(self):
        # 内容含 [ （在光标之后）→ 不匹配（closing } 必须在 [ 之前，但光标在 [ 之前）
        line = '\\begin{ab[c}'
        # positions: 0-6 \begin{, 7 a, 8 b, 9 [, 10 c, 11 }
        # cursor offset 8 = between 'a' and 'b'; } at 11 is after [ at 9
        # scan from content_start=7: a (ok), b (ok), [ (forbidden, break). last_close = None.
        result = find_cursor_in_begin_end(line, 8)
        self.assertIsNone(result)


class TestLeadingTextAndIndentation(unittest.TestCase):

    def test_leading_whitespace(self):
        # 行首有缩进，\begin 不在 offset 0
        line = '  \\begin{foo}'
        # positions: 0-1 spaces, 2-8 \begin{, 9-11 foo, 12 }
        # cursor offset 12 = between 'o' (pos 11) and '}' (pos 12)
        result = find_cursor_in_begin_end(line, 12)
        self.assertIsNotNone(result)
        begin_or_end, before, after, bs_off = result
        self.assertEqual(begin_or_end, 'begin')
        self.assertEqual(before, 'foo')
        self.assertEqual(after, '')
        # backslash 在 offset 2（不是 0）
        self.assertEqual(bs_off, 2)

    def test_text_before_begin(self):
        # \begin 前有其他文本
        line = 'some text \\begin{foo}'
        # 'some text ' = 10 chars, \begin{ at 10-16, foo at 17-19, } at 20
        # cursor offset 20 = between 'o' (pos 19) and '}' (pos 20)
        result = find_cursor_in_begin_end(line, 20)
        self.assertIsNotNone(result)
        _, before, after, bs_off = result
        self.assertEqual(before, 'foo')
        self.assertEqual(bs_off, 10)


class TestMultipleBeginEnd(unittest.TestCase):

    def test_rightmost_match_wins(self):
        # 同一行有 \begin{a} 和 \end{b}，光标在 \end{b} 内
        line = '\\begin{a}\\end{b}'
        # positions: 0-6 \begin{, 7 a, 8 }, 9-13 \end{, 14 b, 15 }
        # cursor offset 15 = between 'b' (pos 14) and '}' (pos 15)
        result = find_cursor_in_begin_end(line, 15)
        self.assertIsNotNone(result)
        begin_or_end, before, after, bs_off = result
        self.assertEqual(begin_or_end, 'end')
        self.assertEqual(before, 'b')
        # \end 的 \ 在 offset 9
        self.assertEqual(bs_off, 9)

    def test_cursor_in_first_region(self):
        # 同一行有 \begin{a} 和 \end{b}，光标在 \begin{a} 内
        line = '\\begin{a}\\end{b}'
        # cursor offset 8 = between 'a' (pos 7) and '}' (pos 8)
        result = find_cursor_in_begin_end(line, 8)
        self.assertIsNotNone(result)
        begin_or_end, before, after, bs_off = result
        self.assertEqual(begin_or_end, 'begin')
        self.assertEqual(before, 'a')
        self.assertEqual(bs_off, 0)

    def test_cursor_between_regions_no_match(self):
        # 光标在两个 region 之间（} 与 \end 之间）
        line = '\\begin{a}\\end{b}'
        # cursor offset 9 = between '}' (pos 8) and '\' (pos 9)
        result = find_cursor_in_begin_end(line, 9)
        self.assertIsNone(result)


class TestLegacySentinelNotFalsePositive(unittest.TestCase):
    '''验证旧 sentinel %•% 出现在用户文本中不会影响匹配。

    旧实现用 %•% 作为光标标记注入 line 文本；若用户文本含 %•%，正则会
    误把它当作光标标记。新实现 find_cursor_in_begin_end 不修改 line
    文本、不依赖任何 sentinel，所以用户文本中的 %•% 完全无影响。
    '''

    def test_user_text_with_percent_bullet_matches_correctly(self):
        # 用户在 \begin{...} 内打了 %•% 三字符，光标在 } 之前
        # line: \begin{foo%•%bar}  (不含任何 sentinel，就是用户文本)
        # positions: 0-6 \begin{, 7-9 foo, 10-12 %•%, 13-15 bar, 16 }
        line = '\\begin{foo%•%bar}'
        # cursor offset 16 = between 'r' (pos 15) and '}' (pos 16)
        result = find_cursor_in_begin_end(line, 16)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        # before 应包含 foo%•%bar（全部内容），after 为空
        self.assertEqual(before, 'foo%•%bar')
        self.assertEqual(after, '')

    def test_user_text_with_percent_bullet_cursor_in_middle(self):
        # 光标在 %•% 与 bar 之间
        line = '\\begin{foo%•%bar}'
        # cursor offset 13 = between '%' (pos 12) and 'b' (pos 13)
        result = find_cursor_in_begin_end(line, 13)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, 'foo%•%')
        self.assertEqual(after, 'bar')

    def test_user_text_with_private_use_char(self):
        # 用户文本含 \uE000（旧实现曾考虑用作 sentinel）也不影响
        # 注意：Python 源码中 \uE000 是单字符（BMP 私用区）
        line = '\\begin{foo\ue000bar}'
        close_idx = line.index('}')
        result = find_cursor_in_begin_end(line, close_idx)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertIn('\ue000', before)
        self.assertEqual(after, '')


class TestContentWithBraceChars(unittest.TestCase):
    '''原正则 [^\{\[\(] 允许 } 出现在内容中（greedy），closing } 是最后一个 }。

    find_cursor_in_begin_end 复现此语义：扫描到第一个 forbidden char ({ [ ()
    为止，取最后一个 } 作为 closing。
    '''

    def test_content_with_multiple_close_braces(self):
        # \begin{a}b} 光标在 a 与第一个 } 之间
        # 原 regex: .*\\(begin|end)\{([^\{\[\(]*)%•%([^\{\[\(]*)\}
        # greedy [^\{\[\(]* 会吞掉 } b，回溯到 group(3)='a' 之前的 }
        # 实际上 closing } 是最后一个 }，group(2)+group(3) 是 { 与最后 } 之间
        line = '\\begin{a}b}'
        # positions: 0-6 \begin{, 7 a, 8 }, 9 b, 10 }
        # scan from 7: a (ok), } (last_close=8), b (ok), } (last_close=10). last_close=10.
        # cursor offset 8 = between 'a' (pos 7) and '}' (pos 8)
        result = find_cursor_in_begin_end(line, 8)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        # before = line[7:8] = 'a', after = line[8:10] = '}b'
        self.assertEqual(before, 'a')
        self.assertEqual(after, '}b')

    def test_content_with_close_brace_then_forbidden(self):
        # \begin{a}b{c} 光标在 a 与 } 之间
        # scan from 7: a (ok), } (last_close=8), b (ok), { (forbidden, break).
        # last_close=8. cursor offset 8 is within [7, 8].
        line = '\\begin{a}b{c}'
        result = find_cursor_in_begin_end(line, 8)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, 'a')
        self.assertEqual(after, '')


class TestEdgeCases(unittest.TestCase):

    def test_empty_content_cursor_at_start(self):
        # \begin{} 光标紧贴 { 之后（空内容）
        line = '\\begin{}'
        # positions: 0-6 \begin{, 7 }
        # cursor offset 7 = between '{' (pos 6) and '}' (pos 7)
        result = find_cursor_in_begin_end(line, 7)
        self.assertIsNotNone(result)
        _, before, after, _ = result
        self.assertEqual(before, '')
        self.assertEqual(after, '')

    def test_begin_at_end_of_line_unclosed(self):
        # \begin{ 在行尾，无 }，不匹配
        line = 'text \\begin{'
        result = find_cursor_in_begin_end(line, 12)
        self.assertIsNone(result)

    def test_cursor_exactly_at_open_brace(self):
        # 光标在 { 位置（offset = position of {）
        # content_start > cursor_offset → break, no match
        line = '\\begin{foo}'
        # { at position 6, cursor offset 6
        result = find_cursor_in_begin_end(line, 6)
        self.assertIsNone(result)


if __name__ == '__main__':
    unittest.main()

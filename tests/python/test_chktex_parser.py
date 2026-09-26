#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# chktex 输出解析与定位的测试：纯逻辑模块（chktex_parser），全程不
# import gi，headless 环境可跑。

import unittest

from setzer.document.chktex.chktex_parser import (
    FORMAT_STRING,
    build_chktex_args,
    build_line_messages,
    locate_span,
    parse_chktex_output,
)


class ParseChktexOutputTest(unittest.TestCase):

    def test_normal_warning(self):
        issues = parse_chktex_output('3:8:Warning:1:Command terminated with space.\t\\LaTeX\n')
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.line, 3)
        self.assertEqual(issue.column, 8)
        self.assertEqual(issue.kind, 'Warning')
        self.assertEqual(issue.number, 1)
        self.assertEqual(issue.message, 'Command terminated with space.')
        self.assertEqual(issue.data, '\\LaTeX')

    def test_message_with_colons_survives(self):
        # 消息文本含冒号：左侧只取前 4 个字段，其余整体归为消息。
        issues = parse_chktex_output('5:1:Warning:12:Note: use of "..." instead of \\ldots\tfoo\n')
        self.assertEqual(issues[0].message, 'Note: use of "..." instead of \\ldots')

    def test_data_with_colons_and_spaces(self):
        # 违规文本放在行尾 TAB 之后，冒号/空格不受字段切分干扰。
        issues = parse_chktex_output('7:3:Warning:24:Wrong label style\t\\label{fig:intro_2}\n')
        self.assertEqual(issues[0].data, '\\label{fig:intro_2}')

    def test_empty_data(self):
        issues = parse_chktex_output('2:1:Message:44:Some advice\t\n')
        self.assertEqual(issues[0].data, '')
        self.assertEqual(issues[0].kind, 'Message')

    def test_column_zero_becomes_none(self):
        issues = parse_chktex_output('9:0:Warning:8:Suspicious\tfoo\n')
        self.assertIsNone(issues[0].column)

    def test_file_level_line_zero_parsed(self):
        issues = parse_chktex_output('0:0:Warning:13:You should have a \\documentclass\t\n')
        self.assertEqual(issues[0].line, 0)
        self.assertEqual(issues[0].message, 'You should have a \\documentclass')

    def test_multiple_issues_order_preserved(self):
        text = (
            '1:1:Warning:8:Should be lowercase\ta\n'
            '2:5:Error:18:Intended use of \\ldots\tb\n'
            '2:9:Message:1:Note\tc\n'
        )
        issues = parse_chktex_output(text)
        self.assertEqual([issue.line for issue in issues], [1, 2, 2])
        self.assertEqual([issue.kind for issue in issues], ['Warning', 'Error', 'Message'])

    def test_banner_and_noise_skipped(self):
        text = (
            'ChkTeX v1.7.6 (Aurora)\n'
            '\n'
            'No tab here\n'
            'not:numbered:correctly\n'
            'x:y:z:Warning:not-a-number:msg\tdata\n'
            '-1:1:x:Warning:1:Negative line\tdata\n'
        )
        self.assertEqual(parse_chktex_output(text), ())

    def test_crlf_input(self):
        issues = parse_chktex_output('4:2:Warning:1:Cmd terminated\t\\emph\r\n')
        self.assertEqual(issues[0].data, '\\emph')

    def test_unicode_data(self):
        issues = parse_chktex_output('6:10:Warning:1:Message here\t中文文本\n')
        self.assertEqual(issues[0].data, '中文文本')
        self.assertEqual(issues[0].message, 'Message here')

    def test_empty_output(self):
        self.assertEqual(parse_chktex_output(''), ())
        self.assertEqual(parse_chktex_output('\n\n'), ())


class LocateSpanTest(unittest.TestCase):

    def test_data_found_at_column(self):
        # column 为 1-based：第 7 列即字符偏移 6（'\\' 的位置）。
        line = r'Hello \LaTeX is great'
        self.assertEqual(locate_span(line, 7, r'\LaTeX'), (6, 12))

    def test_multibyte_column_mismatch_falls_back(self):
        # chktex 列号在多字节字符前可能按字节计：列号作字符偏移会指偏，
        # 回退「从行首全文找 data」仍能定位到正确区间。
        line = '中文注解 \\emph{强调} 后续'
        self.assertEqual(locate_span(line, 20, '\\emph{强调}'), (5, 14))

    def test_repeated_data_uses_column_hint(self):
        # 第二处 \emph 起于字符偏移 14（即第 15 列）。
        line = r'\emph{a} plus \emph{b}'
        self.assertEqual(locate_span(line, 15, r'\emph'), (14, 19))
        self.assertEqual(locate_span(line, 1, r'\emph'), (0, 5))

    def test_data_not_found_returns_whole_line(self):
        self.assertEqual(locate_span('some line', 3, 'missing'), (0, 9))

    def test_empty_data_returns_whole_line(self):
        self.assertEqual(locate_span('abc', 2, ''), (0, 3))

    def test_column_none_or_zero(self):
        self.assertEqual(locate_span('abcdef', None, 'cde'), (2, 5))
        self.assertEqual(locate_span('abcdef', 0, 'cde'), (2, 5))


class BuildLineMessagesTest(unittest.TestCase):

    def test_grouping_and_format(self):
        from setzer.document.chktex.chktex_parser import ChktexIssue
        issues = (
            ChktexIssue(3, 8, 'Warning', 1, 'Command terminated with space.', 'x'),
            ChktexIssue(3, 1, 'Warning', 24, 'Wrong label style', 'y'),
            ChktexIssue(5, 0, 'Error', 18, 'Intended use of \\ldots', 'z'),
        )
        messages = build_line_messages(issues)
        self.assertEqual(messages, {
            3: ('Warning 1: Command terminated with space.',
                'Warning 24: Wrong label style'),
            5: ('Error 18: Intended use of \\ldots',),
        })

    def test_file_level_line_zero_excluded(self):
        from setzer.document.chktex.chktex_parser import ChktexIssue
        issues = (ChktexIssue(0, None, 'Warning', 13, 'File level', ''),)
        self.assertEqual(build_line_messages(issues), {})


class BuildChktexArgsTest(unittest.TestCase):

    def test_args_shape(self):
        args = build_chktex_args()
        self.assertEqual(args[0], 'chktex')
        self.assertIn('-q', args)
        self.assertIn('-wall', args)
        # -f 的值与 flag 分开传，值里含 TAB（与 FORMAT_STRING 一致）。
        self.assertEqual(args[-2], '-f')
        self.assertEqual(args[-1], FORMAT_STRING)
        self.assertIn('\t', FORMAT_STRING)
        self.assertIn('%d', FORMAT_STRING)


if __name__ == '__main__':
    unittest.main()

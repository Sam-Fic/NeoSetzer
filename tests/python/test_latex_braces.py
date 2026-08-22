#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.parser.latex_braces import scan_balanced_braced_argument


class ScanBalancedBracedArgumentTest(unittest.TestCase):

    def test_issue_389_nested_text_command_is_returned_verbatim(self):
        text = r'\section{The \textit{quick settings} menu}'
        opening = text.index('{')
        self.assertEqual(
            scan_balanced_braced_argument(text, opening),
            (r'The \textit{quick settings} menu', len(text)),
        )

    def test_multiple_and_deeply_nested_arguments_keep_outer_boundary(self):
        text = r'{A \href{https://example.test/{path}}{\textbf{label}} title} tail'
        self.assertEqual(
            scan_balanced_braced_argument(text, 0),
            (r'A \href{https://example.test/{path}}{\textbf{label}} title',
             text.index('} tail') + 1),
        )

    def test_escaped_literal_braces_do_not_change_depth(self):
        text = r'{Literal \{draft\} and \texttt{code}}'
        self.assertEqual(
            scan_balanced_braced_argument(text, 0),
            (r'Literal \{draft\} and \texttt{code}', len(text)),
        )

    def test_multiline_argument_keeps_all_text(self):
        text = '\\section{First line\n\\emph{second line}}'
        opening = text.index('{')
        self.assertEqual(
            scan_balanced_braced_argument(text, opening),
            ('First line\n\\emph{second line}', len(text)),
        )

    def test_invalid_or_unclosed_arguments_are_safe(self):
        self.assertIsNone(scan_balanced_braced_argument('plain', 0))
        self.assertIsNone(scan_balanced_braced_argument('{unfinished', 0))
        self.assertIsNone(scan_balanced_braced_argument('{}', 1))
        self.assertIsNone(scan_balanced_braced_argument('{}', -1))


if __name__ == '__main__':
    unittest.main()

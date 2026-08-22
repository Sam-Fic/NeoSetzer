#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.smart_list import (
    SmartListNewlineKind,
    get_smart_list_newline_action,
)


class SmartListNewlineRuleTest(unittest.TestCase):

    def assert_action(self, line_text, expected_kind, expected_indentation):
        action = get_smart_list_newline_action(line_text, len(line_text))
        self.assertIsNotNone(action)
        self.assertEqual(action.kind, expected_kind)
        self.assertEqual(action.indentation, expected_indentation)

    def test_item_with_text_continues_at_same_space_indentation(self):
        self.assert_action(
            '    \\item First item',
            SmartListNewlineKind.CONTINUE,
            '    ',
        )

    def test_item_with_text_continues_at_same_tab_indentation(self):
        self.assert_action(
            '\t\\item Nested item',
            SmartListNewlineKind.CONTINUE,
            '\t',
        )

    def test_empty_conventional_item_exits_list(self):
        self.assert_action(
            '  \\item ',
            SmartListNewlineKind.EXIT,
            '  ',
        )

    def test_whitespace_only_body_is_left_to_default_editor_handling(self):
        self.assertIsNone(get_smart_list_newline_action('\\item  ', len('\\item  ')))
        self.assertIsNone(get_smart_list_newline_action('\\item \t', len('\\item \t')))

    def test_non_item_and_partial_or_related_commands_are_not_matched(self):
        for line_text in (
                '',
                '\\item',
                '\\itemize',
                '\\item[Label] text',
                'text \\item Item',
                '  text',
        ):
            with self.subTest(line_text=line_text):
                self.assertIsNone(get_smart_list_newline_action(line_text, len(line_text)))

    def test_cursor_must_be_at_end_of_line(self):
        line_text = '\\item First item'
        self.assertIsNone(get_smart_list_newline_action(line_text, 6))
        self.assertIsNone(get_smart_list_newline_action(line_text, len(line_text) + 1))


if __name__ == '__main__':
    unittest.main()

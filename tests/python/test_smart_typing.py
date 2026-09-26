#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.smart_typing import (
    CLOSING_QUOTES,
    OPENING_QUOTES,
    get_smart_quote_insertion,
    is_auto_subscript_group,
    is_subscript_group_char,
    should_open_subscript,
)


class SmartQuoteRuleTest(unittest.TestCase):

    def assert_quote(self, previous_char, expected):
        self.assertEqual(get_smart_quote_insertion(previous_char), expected)

    def test_document_start_opens(self):
        self.assert_quote('', OPENING_QUOTES)

    def test_after_word_closes(self):
        self.assert_quote('d', CLOSING_QUOTES)

    def test_after_chinese_word_closes(self):
        self.assert_quote('说', CLOSING_QUOTES)

    def test_after_digit_closes(self):
        self.assert_quote('7', CLOSING_QUOTES)

    def test_after_closing_punctuation_closes(self):
        for char in '!?.,;:':
            with self.subTest(char=char):
                self.assert_quote(char, CLOSING_QUOTES)

    def test_after_quote_characters_closes(self):
        self.assert_quote("'", CLOSING_QUOTES)
        self.assert_quote('"', CLOSING_QUOTES)

    def test_after_whitespace_opens(self):
        for char in ' \t\n':
            with self.subTest(char=repr(char)):
                self.assert_quote(char, OPENING_QUOTES)

    def test_after_opening_delimiters_opens(self):
        for char in '([{<>$\\&%#_~':
            with self.subTest(char=char):
                self.assert_quote(char, OPENING_QUOTES)


class AutoSubscriptRuleTest(unittest.TestCase):

    def test_wraps_alnum_after_underscore_in_math(self):
        self.assertTrue(should_open_subscript('x_', '1', True))

    def test_wraps_letter_after_caret_in_math(self):
        self.assertTrue(should_open_subscript('y^', 'i', True))

    def test_brace_after_marker_is_left_to_bracket_completion(self):
        self.assertFalse(should_open_subscript('x_', '{', True))

    def test_non_alnum_does_not_open(self):
        for char in ' +-{}#^\\':
            with self.subTest(char=char):
                self.assertFalse(should_open_subscript('x_', char, True))

    def test_outside_math_does_not_open(self):
        self.assertFalse(should_open_subscript('x_', '1', False))

    def test_only_after_a_script_marker(self):
        self.assertFalse(should_open_subscript('xy', '1', True))
        self.assertFalse(should_open_subscript('', '1', True))

    def test_escaped_underscore_is_a_typeset_underscore(self):
        self.assertFalse(should_open_subscript(r'x\_', '1', True))

    def test_line_break_before_marker_is_a_real_subscript(self):
        self.assertTrue(should_open_subscript(r'\\_', '1', True))
        self.assertTrue(should_open_subscript(r'x\\_', '1', True))

    def test_third_level_backslashes_are_escaped_again(self):
        self.assertFalse(should_open_subscript(r'x\\\_', '1', True))


class SubscriptGroupTest(unittest.TestCase):

    def test_alnum_continues_the_group(self):
        for char in 'aZ09':
            with self.subTest(char=char):
                self.assertTrue(is_subscript_group_char(char))

    def test_only_alnum_continues_the_group(self):
        for char in ' +-{}(),;:!?.*^_\t':
            with self.subTest(char=char):
                self.assertFalse(is_subscript_group_char(char))

    def test_empty_char_does_not_continue(self):
        self.assertFalse(is_subscript_group_char(''))

    def test_cursor_at_letters_inside_auto_group(self):
        self.assertTrue(is_auto_subscript_group(r'x_{12'))
        self.assertTrue(is_auto_subscript_group(r'y^{i'))
        self.assertTrue(is_auto_subscript_group(r'x_{'))

    def test_group_must_follow_a_script_marker(self):
        self.assertFalse(is_auto_subscript_group(r'x\{12'))
        self.assertFalse(is_auto_subscript_group(r'z_1{a'))
        self.assertFalse(is_auto_subscript_group('x{12'))
        self.assertFalse(is_auto_subscript_group('x_12'))
        self.assertFalse(is_auto_subscript_group('text'))
        self.assertFalse(is_auto_subscript_group(''))

    def test_group_holds_only_alnum(self):
        self.assertFalse(is_auto_subscript_group(r'x_{1 + 2'))
        self.assertFalse(is_auto_subscript_group(r'x_{a}b'))

    def test_escaped_marker_is_not_a_group(self):
        self.assertFalse(is_auto_subscript_group(r'x\_{1'))
        self.assertFalse(is_auto_subscript_group(r'x\\\_{1'))

    def test_double_backslash_marker_is_a_group(self):
        self.assertTrue(is_auto_subscript_group(r'x\\_{1'))


if __name__ == '__main__':
    unittest.main()

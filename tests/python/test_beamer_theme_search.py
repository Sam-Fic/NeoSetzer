#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.dialogs.document_wizard.beamer_themes import filter_theme_names


THEMES = ('Warsaw', 'Malmoe', 'CambridgeUS', 'default', 'PaloAlto')


class TestBeamerThemeSearch(unittest.TestCase):

    def test_empty_query_keeps_all_themes_in_catalogue_order(self):
        self.assertEqual(filter_theme_names(THEMES, ''), THEMES)
        self.assertEqual(filter_theme_names(THEMES, '   '), THEMES)

    def test_search_is_casefolded_substring_matching(self):
        self.assertEqual(filter_theme_names(THEMES, 'AL'), ('Malmoe', 'PaloAlto'))
        self.assertEqual(filter_theme_names(THEMES, 'cambridge'), ('CambridgeUS',))
        self.assertEqual(filter_theme_names(THEMES, 'DEFAULT'), ('default',))

    def test_no_match_returns_an_empty_tuple(self):
        self.assertEqual(filter_theme_names(THEMES, 'ocean'), ())

    def test_invalid_query_and_catalogue_entries_are_safe(self):
        self.assertEqual(filter_theme_names(('Warsaw', None, 7), None), ('Warsaw',))


if __name__ == '__main__':
    unittest.main()

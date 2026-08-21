#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.document.autocomplete.environment_templates import get_environment_completion_tail


class EnvironmentTemplatesTest(unittest.TestCase):

    def test_enumerate_starts_with_an_item_placeholder(self):
        self.assertEqual(
            get_environment_completion_tail('enumerate'),
            '\n\t\\item •\n\\end{enumerate}',
        )

    def test_enumerate_matching_is_case_insensitive(self):
        self.assertEqual(
            get_environment_completion_tail('Enumerate'),
            '\n\t\\item •\n\\end{Enumerate}',
        )

    def test_other_environments_keep_the_generic_placeholder(self):
        self.assertEqual(
            get_environment_completion_tail('figure'),
            '\n\t•\n\\end{figure}',
        )


if __name__ == '__main__':
    unittest.main()

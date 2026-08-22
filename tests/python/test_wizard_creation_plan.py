#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.dialogs.document_wizard.creation_plan import build_creation_plan


BASE_STATE = {
    'document_class': 'article',
    'title': 'Research notes',
    'languages': {'english': 'English', 'german': 'Deutsch'},
    'font_package': 'lmodern',
    'packages': {'ams': True, 'graphicx': True, 'hyperref': False},
    'article': {'page_format': 'A4', 'is_landscape': False},
    'report': {'page_format': 'A4', 'is_landscape': False},
    'book': {'page_format': 'A4', 'is_landscape': False},
    'letter': {'page_format': 'A4', 'is_landscape': False},
}


class TestCreationPlan(unittest.TestCase):

    def test_normal_document_requires_nonempty_title_and_summarises_settings(self):
        plan = build_creation_plan(BASE_STATE)

        self.assertEqual(plan.mode, 'wizard-settings')
        self.assertTrue(plan.title_required)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.document_class, 'article')
        self.assertEqual(plan.language, 'english')
        self.assertEqual(plan.font_package, 'lmodern')
        self.assertEqual(plan.page_format, 'A4')
        self.assertEqual(plan.packages, ('ams', 'graphicx'))

    def test_blank_title_blocks_standard_document_creation(self):
        state = dict(BASE_STATE, title='   ')
        plan = build_creation_plan(state)

        self.assertTrue(plan.title_required)
        self.assertFalse(plan.ready)

    def test_letter_does_not_require_a_document_title(self):
        state = dict(BASE_STATE, document_class='scrlttr2', title='')
        plan = build_creation_plan(state)

        self.assertFalse(plan.title_required)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.page_format, 'A4')

    def test_source_template_bypasses_wizard_metadata_requirements(self):
        state = dict(BASE_STATE, title='')
        plan = build_creation_plan(state, source_template_name='University report')

        self.assertEqual(plan.mode, 'source-template')
        self.assertEqual(plan.template_name, 'University report')
        self.assertFalse(plan.title_required)
        self.assertTrue(plan.ready)

    def test_malformed_state_is_safe_and_not_ready_for_standard_mode(self):
        plan = build_creation_plan({'document_class': 42, 'languages': []})

        self.assertEqual(plan.document_class, 'article')
        self.assertTrue(plan.title_required)
        self.assertFalse(plan.ready)
        self.assertEqual(plan.packages, ())


if __name__ == '__main__':
    unittest.main()

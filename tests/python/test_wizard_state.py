#!/usr/bin/env python3
# coding: utf-8

"""Regression tests for the versioned, gi-free document-wizard state schema."""

import copy
import unittest

from setzer.dialogs.document_wizard.wizard_state import (
    WIZARD_STATE_VERSION,
    build_default_wizard_state,
    normalise_wizard_state,
)


LANGUAGES = {'english': 'English', 'german': 'Deutsch'}


def make_defaults():
    return build_default_wizard_state('A4', LANGUAGES)


class TestDefaultWizardState(unittest.TestCase):

    def test_default_state_is_complete_and_json_shaped(self):
        state = make_defaults()

        self.assertEqual(state['schema_version'], WIZARD_STATE_VERSION)
        self.assertEqual(state['document_class'], 'article')
        self.assertEqual(state['letter']['option_window_address'], True)
        self.assertEqual(state['beamer']['theme'], 'default')
        self.assertEqual(set(state['packages']), {
            'ams', 'graphicx', 'color', 'xcolor', 'url', 'theorem',
            'textcomp', 'listings', 'hyperref', 'glossaries', 'parskip',
        })

    def test_default_state_does_not_alias_languages_or_class_settings(self):
        state = make_defaults()
        state['languages']['french'] = 'French'
        state['article']['page_format'] = 'US Letter'

        self.assertNotIn('french', LANGUAGES)
        self.assertEqual(state['report']['page_format'], 'A4')


class TestNormaliseWizardState(unittest.TestCase):

    def test_partial_legacy_letter_preset_keeps_valid_values_and_fills_missing(self):
        legacy = {
            'document_class': 'scrlttr2',
            'author': 'Ada Lovelace',
            'letter': {
                'page_format': 'US Letter',
                'font_size': 12,
                'is_landscape': True,
            },
        }

        state = normalise_wizard_state(legacy, make_defaults())

        self.assertEqual(state['schema_version'], WIZARD_STATE_VERSION)
        self.assertEqual(state['document_class'], 'scrlttr2')
        self.assertEqual(state['author'], 'Ada Lovelace')
        self.assertEqual(state['letter']['page_format'], 'US Letter')
        self.assertEqual(state['letter']['font_size'], 12)
        self.assertTrue(state['letter']['is_landscape'])
        self.assertFalse(state['letter']['option_twocolumn'])
        self.assertTrue(state['letter']['option_window_address'])
        self.assertEqual(state['letter']['recipient_address'], '')

    def test_invalid_values_fall_back_without_losing_unrelated_valid_values(self):
        candidate = {
            'document_class': 'shell-command',
            'title': 42,
            'author': 'Valid author',
            'font_package': 'unsupported',
            'languages': {'english': 7},
            'packages': {'ams': 'yes', 'graphicx': False},
            'article': {
                'page_format': '../A4',
                'font_size': 72,
                'option_twocolumn': 1,
                'margin_left': -2,
                'margin_right': 4.25,
            },
            'beamer': {'theme': 'No such theme', 'option_top_align': False},
        }

        state = normalise_wizard_state(candidate, make_defaults())

        self.assertEqual(state['document_class'], 'article')
        self.assertEqual(state['title'], '')
        self.assertEqual(state['author'], 'Valid author')
        self.assertEqual(state['font_package'], 'lmodern')
        self.assertEqual(state['languages'], LANGUAGES)
        self.assertTrue(state['packages']['ams'])
        self.assertFalse(state['packages']['graphicx'])
        self.assertEqual(state['article']['page_format'], 'A4')
        self.assertEqual(state['article']['font_size'], 10)
        self.assertFalse(state['article']['option_twocolumn'])
        self.assertEqual(state['article']['margin_left'], 3.5)
        self.assertEqual(state['article']['margin_right'], 4.25)
        self.assertEqual(state['beamer']['theme'], 'default')
        self.assertFalse(state['beamer']['option_top_align'])

    def test_unknown_fields_are_not_activated_or_persisted(self):
        state = normalise_wizard_state({
            'future_top_level': 'ignored',
            'article': {'future_option': True},
            'packages': {'evilpackage': True},
        }, make_defaults())

        self.assertNotIn('future_top_level', state)
        self.assertNotIn('future_option', state['article'])
        self.assertNotIn('evilpackage', state['packages'])

    def test_normalisation_is_idempotent_and_does_not_mutate_inputs(self):
        defaults = make_defaults()
        candidate = {
            'document_class': 'beamer',
            'beamer': {'theme': 'Warsaw'},
            'letter': {'sender_name': 'Original'},
        }
        original_defaults = copy.deepcopy(defaults)
        original_candidate = copy.deepcopy(candidate)

        first = normalise_wizard_state(candidate, defaults)
        second = normalise_wizard_state(first, defaults)
        first['letter']['sender_name'] = 'Changed result'

        self.assertEqual(second, normalise_wizard_state(second, defaults))
        self.assertEqual(defaults, original_defaults)
        self.assertEqual(candidate, original_candidate)
        self.assertEqual(candidate['letter']['sender_name'], 'Original')

    def test_non_mapping_candidate_returns_fresh_defaults(self):
        defaults = make_defaults()
        state = normalise_wizard_state(['not', 'a', 'preset'], defaults)
        state['letter']['sender_name'] = 'Transient'

        self.assertEqual(defaults['letter']['sender_name'], '')
        self.assertEqual(state['schema_version'], WIZARD_STATE_VERSION)


if __name__ == '__main__':
    unittest.main()

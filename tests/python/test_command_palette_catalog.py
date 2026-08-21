#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.command_palette.catalog import CommandCatalog, CommandDescriptor, normalize, search


class FakeAction:

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.activations = []

    def get_enabled(self):
        return self.enabled

    def activate(self, parameter):
        self.activations.append(parameter)


class FakeActions:

    def __init__(self, actions):
        self.actions = actions


class CommandPaletteCatalogTest(unittest.TestCase):

    def setUp(self):
        self.build = CommandDescriptor('build', 'Build PDF', 'Build', 'build', ('compile', 'latex'))
        self.save_build = CommandDescriptor('save-build', 'Save and Build PDF', 'Build', 'save-and-build', ('compile', 'latex'))
        self.preferences = CommandDescriptor('preferences', 'Preferences', 'Application', 'preferences', ('settings',))
        self.fake_build = FakeAction()
        self.fake_save_build = FakeAction()
        self.fake_preferences = FakeAction(enabled=False)
        self.catalog = CommandCatalog(FakeActions({
            'build': self.fake_build,
            'save-and-build': self.fake_save_build,
            'preferences': self.fake_preferences,
        }), (self.build, self.save_build, self.preferences))

    def test_normalize_ignores_case_accents_and_punctuation(self):
        self.assertEqual(normalize('Préférences — PDF!'), 'preferences pdf')

    def test_search_prefers_exact_title(self):
        self.assertEqual(search((self.build, self.save_build), 'build pdf'), [self.build, self.save_build])

    def test_search_matches_keywords_and_category(self):
        self.assertEqual(search((self.build, self.preferences), 'compile'), [self.build])
        self.assertEqual(search((self.build, self.preferences), 'application'), [self.preferences])

    def test_catalog_hides_disabled_actions(self):
        self.assertEqual(self.catalog.available(), [self.build, self.save_build])
        self.assertNotIn(self.preferences, self.catalog.search(''))

    def test_execute_activates_enabled_action_without_parameter(self):
        self.assertTrue(self.catalog.execute(self.build))
        self.assertEqual(self.fake_build.activations, [None])

    def test_execute_refuses_disabled_action(self):
        self.assertFalse(self.catalog.execute(self.preferences))
        self.assertEqual(self.fake_preferences.activations, [])


if __name__ == '__main__':
    unittest.main()

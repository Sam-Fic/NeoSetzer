#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.command_palette.catalog import (
    COMMANDS,
    CommandCatalog,
    CommandDescriptor,
    RECENT_COMMAND_LIMIT,
    normalize,
    prioritize_recent,
    search,
    update_recent_command_ids,
)


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

    def test_shortcut_settings_key_uses_explicit_or_action_name_mapping(self):
        self.assertEqual(self.build.settings_shortcut_key, 'build')
        self.assertEqual(self.save_build.settings_shortcut_key, 'save_and_build')
        self.assertEqual(self.preferences.settings_shortcut_key, 'preferences')
        find_command = next(command for command in COMMANDS if command.identifier == 'find')
        self.assertEqual(find_command.settings_shortcut_key, 'find')

    def test_recent_command_ids_are_deduplicated_and_bounded(self):
        previous = ['save-build', 'build', 'save-build', 'missing']
        recent = update_recent_command_ids('build', previous)
        self.assertEqual(recent, ['build', 'save-build', 'missing'])
        self.assertEqual(
            update_recent_command_ids('latest', range(20), limit=RECENT_COMMAND_LIMIT),
            ['latest'],
        )
        with self.assertRaises(ValueError):
            update_recent_command_ids('build', (), limit=0)

    def test_empty_query_prioritizes_available_recent_commands(self):
        commands = self.catalog.search('', ('save-build', 'missing', 'build', 'save-build'))
        self.assertEqual(commands, [self.save_build, self.build])
        self.assertEqual(
            prioritize_recent((self.build, self.save_build), ('missing', 'save-build')),
            [self.save_build, self.build],
        )

    def test_search_query_keeps_relevance_order_over_recent_commands(self):
        self.assertEqual(
            self.catalog.search('build', ('save-build',)),
            [self.build, self.save_build],
        )

    def test_global_catalog_includes_searchable_insert_table_command(self):
        table_command = next(command for command in COMMANDS if command.identifier == 'insert-table')
        self.assertEqual(table_command.action_name, 'insert-table-dialog')
        self.assertEqual(table_command.category, 'LaTeX')
        self.assertEqual(search((table_command,), 'booktabs'), [table_command])
        self.assertEqual(search((table_command,), 'tabular'), [table_command])

    def test_execute_refuses_disabled_action(self):
        self.assertFalse(self.catalog.execute(self.preferences))
        self.assertEqual(self.fake_preferences.activations, [])


if __name__ == '__main__':
    unittest.main()

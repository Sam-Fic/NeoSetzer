#!/usr/bin/env python3
# coding: utf-8

import pathlib
import unittest
from unittest.mock import patch

import setzer.dialogs.first_run_tutorial.tutorial_content as tutorial_content
from setzer.dialogs.first_run_tutorial.tutorial_content import (
    DEFAULT_SHORTCUTS,
    get_configured_shortcut,
    get_tutorial_tips,
)


class _Settings:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error

    def get_value(self, section, key):
        if self.error is not None:
            raise self.error
        return self.values[(section, key)]


class FirstRunTutorialContentTest(unittest.TestCase):
    def test_configured_shortcuts_use_current_values(self):
        settings = _Settings({
            ('keyboard_shortcuts', 'save_and_build'): '<Control><Shift>B',
            ('keyboard_shortcuts', 'command_palette'): '<Alt>P',
        })

        self.assertEqual(
            get_configured_shortcut(settings, 'save_and_build'),
            '<Control><Shift>B',
        )
        self.assertEqual(
            get_configured_shortcut(settings, 'command_palette'),
            '<Alt>P',
        )

    def test_missing_or_invalid_shortcuts_fall_back_to_defaults(self):
        cases = (
            _Settings(error=KeyError('missing')),
            _Settings({('keyboard_shortcuts', 'save_and_build'): ''}),
            _Settings({('keyboard_shortcuts', 'save_and_build'): '   '}),
            _Settings({('keyboard_shortcuts', 'save_and_build'): 5}),
        )
        for settings in cases:
            with self.subTest(settings=settings):
                self.assertEqual(
                    get_configured_shortcut(settings, 'save_and_build'),
                    DEFAULT_SHORTCUTS['save_and_build'],
                )

    def test_tips_keep_workflow_order_and_insert_shortcuts(self):
        messages = []

        def translate(message):
            messages.append(message)
            return message

        with patch.object(tutorial_content, '_', translate, create=True):
            tips = get_tutorial_tips('F5', 'Ctrl+.')

        self.assertEqual(len(tips), 4)
        self.assertEqual([tip[0] for tip in tips], [
            'document-new-symbolic',
            'system-run-symbolic',
            'view-list-symbolic',
            'system-search-symbolic',
        ])
        self.assertEqual([tip[1] for tip in tips], [
            'Start with a document',
            'Build and preview your PDF',
            'Navigate with document structure',
            'Find commands quickly',
        ])
        self.assertIn('F5', tips[1][2])
        self.assertIn('Ctrl+.', tips[3][2])
        self.assertIn('Use {shortcut} to save and build. The PDF opens in the '
                      'preview panel, where you can inspect the result.', messages)
        self.assertIn('Use {shortcut} to search and run application commands '
                      'when you do not remember where an action is located.', messages)


class FirstRunTutorialStartupOrderTest(unittest.TestCase):
    def test_tutorial_is_presented_after_workspace_actions_are_ready(self):
        source_path = pathlib.Path(__file__).resolve().parents[2] / 'setzer.in'
        source = source_path.read_text(encoding='utf-8')

        self.assertLess(
            source.index('self.workspace.init_workspace_controller()'),
            source.rindex('maybe_show_first_run_tutorial()'),
        )
        self.assertLess(
            source.index('ServiceLocator.set_shortcuts(self.shortcuts)'),
            source.rindex('maybe_show_first_run_tutorial()'),
        )


if __name__ == '__main__':
    unittest.main()

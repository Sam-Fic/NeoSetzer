#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''Pure content and preference helpers for the first-run tutorial.

Keeping this module free of GTK makes the tutorial's user-facing sequence and
shortcut fallback behaviour testable in headless CI. The dialog owns widget
creation and accelerator rendering.
'''


DEFAULT_SHORTCUTS = {
    'save_and_build': 'F5',
    'command_palette': '<Control>period',
}


def get_configured_shortcut(settings, action_name):
    '''Return a configured shortcut or the documented default.

    Settings files can outlive renamed or removed shortcut entries. A malformed
    value must not prevent the welcome dialog from opening, so only non-empty
    strings are accepted and every other value falls back to the current
    settings default.
    '''
    default = DEFAULT_SHORTCUTS[action_name]
    try:
        value = settings.get_value('keyboard_shortcuts', action_name)
    except (AttributeError, KeyError, TypeError, ValueError):
        return default
    return value if isinstance(value, str) and value.strip() else default


def get_tutorial_tips(save_and_build_shortcut, command_palette_shortcut):
    '''Return the ordered, translated tutorial content.

    The application installs ``_`` before dialogs are created. Unit tests can
    provide an identity translator in this module's globals without importing
    GTK or the application bootstrap.
    '''
    return [
        (
            'document-new-symbolic',
            _('Start with a document'),
            _('Use the welcome screen to create or open a file, choose '
              'a template, or try the example document.'),
        ),
        (
            'system-run-symbolic',
            _('Build and preview your PDF'),
            _('Use {shortcut} to save and build. The PDF opens in the '
              'preview panel, where you can inspect the result.').format(
                          shortcut=save_and_build_shortcut),
        ),
        (
            'view-list-symbolic',
            _('Navigate with document structure'),
            _('Use Document Structure to move between headings in a '
              'long document. Select an entry to jump to its title.'),
        ),
        (
            'system-search-symbolic',
            _('Find commands quickly'),
            _('Use {shortcut} to search and run application commands '
              'when you do not remember where an action is located.').format(
                          shortcut=command_palette_shortcut),
        ),
    ]

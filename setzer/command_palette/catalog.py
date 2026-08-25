#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Command metadata and fuzzy matching for NeoSetzer's command palette.

The command palette deliberately lists an explicit set of user-facing,
parameter-free Gio actions.  This avoids exposing context-menu actions that
need a label, offset, or a variant target, while reusing the application's
existing action enablement and activation behavior.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
import re
import unicodedata
from typing import Callable, Iterable, Sequence


def _(message: str) -> str:
    """Look up a runtime gettext translation with a test-safe fallback."""

    return getattr(builtins, '_', lambda value: value)(message)


RECENT_COMMAND_LIMIT = 8


@dataclass(frozen=True)
class CommandDescriptor:
    """A user-facing command backed by an existing action."""

    identifier: str
    title: str
    category: str
    action_name: str
    keywords: tuple[str, ...] = ()
    shortcut_key: str | None = None

    @property
    def settings_shortcut_key(self) -> str:
        '''Return the keyboard-shortcut settings key for this command.

        Most Gio Action names map directly to the settings convention by
        replacing hyphens with underscores.  Commands whose action describes
        a UI target rather than the user-facing operation can declare a
        dedicated ``shortcut_key``.
        '''

        return self.shortcut_key or self.action_name.replace('-', '_')


@dataclass(frozen=True)
class CommandResultGroup:
    '''A command-palette section with a homogeneous executable state.'''

    identifier: str
    commands: tuple[CommandDescriptor, ...]
    available: bool


# Keep labels in one place so they can be localized together when the dialog is
# added to the translation template.  The English source strings are also useful
# fallbacks before the locale is initialized.
COMMANDS: tuple[CommandDescriptor, ...] = (
    CommandDescriptor('new-latex', _('New LaTeX Document'), _('File'), 'new-latex-document', ('new', 'document', 'tex')),
    CommandDescriptor('new-bibtex', _('New BibTeX Document'), _('File'), 'new-bibtex-document', ('new', 'bibliography', 'bib')),
    CommandDescriptor('open', _('Open Document'), _('File'), 'open-document-dialog', ('file', 'open')),
    CommandDescriptor('save', _('Save'), _('File'), 'save', ('write', 'document')),
    CommandDescriptor('save-as', _('Save As'), _('File'), 'save-as', ('write', 'rename', 'document')),
    CommandDescriptor('save-all', _('Save All'), _('File'), 'save-all', ('write', 'documents')),
    CommandDescriptor('export-pdf', _('Export PDF As'), _('File'), 'export-pdf-as', ('export', 'pdf')),
    CommandDescriptor('export-project-package', _('Export Project Package'), _('File'), 'export-project-package', ('export', 'project', 'zip', 'archive')),
    CommandDescriptor('print', _('Print'), _('File'), 'print', ('printer', 'pdf')),
    CommandDescriptor('close-document', _('Close Document'), _('File'), 'close-active-document', ('close', 'tab'), 'close_document'),
    CommandDescriptor('close-all', _('Close All Documents'), _('File'), 'close-all-documents', ('close', 'tabs'), 'close_all_documents'),
    CommandDescriptor('reopen', _('Reopen Last Closed Document'), _('File'), 'reopen-last-closed-document', ('restore', 'tab'), 'reopen_last_closed_document'),
    CommandDescriptor('build', _('Build PDF'), _('Build'), 'build', ('compile', 'latex', 'pdf')),
    CommandDescriptor('save-build', _('Save and Build PDF'), _('Build'), 'save-and-build', ('compile', 'latex', 'pdf')),
    CommandDescriptor('build-log', _('Show Build Log'), _('Build'), 'show-build-log', ('compile', 'output', 'log'), 'build_log'),
    CommandDescriptor('close-build-log', _('Close Build Log'), _('Build'), 'close-build-log', ('compile', 'output', 'log')),
    CommandDescriptor('forward-sync', _('Forward Sync'), _('Build'), 'forward-sync', ('synctex', 'preview', 'pdf')),
    CommandDescriptor('undo', _('Undo'), _('Edit'), 'undo', ('edit', 'history')),
    CommandDescriptor('redo', _('Redo'), _('Edit'), 'redo', ('edit', 'history')),
    CommandDescriptor('cut', _('Cut'), _('Edit'), 'cut', ('clipboard', 'selection')),
    CommandDescriptor('copy', _('Copy'), _('Edit'), 'copy', ('clipboard', 'selection')),
    CommandDescriptor('paste', _('Paste'), _('Edit'), 'paste', ('clipboard',)),
    CommandDescriptor('delete-selection', _('Delete Selection'), _('Edit'), 'delete-selection', ('remove', 'selection')),
    CommandDescriptor('select-all', _('Select All'), _('Edit'), 'select-all', ('selection', 'text')),
    CommandDescriptor('find', _('Find'), _('Edit'), 'start-search', ('search', 'text'), 'find'),
    CommandDescriptor('replace', _('Find and Replace'), _('Edit'), 'start-search-and-replace', ('search', 'replace', 'text'), 'find_and_replace'),
    CommandDescriptor('project-search-replace', _('Search and Replace in Project'), _('Edit'), 'project-search-and-replace', ('search', 'replace', 'project', 'preview', 'all files')),
    CommandDescriptor('find-next', _('Find Next'), _('Edit'), 'find-next', ('search', 'next')),
    CommandDescriptor('find-previous', _('Find Previous'), _('Edit'), 'find-previous', ('search', 'previous')),
    CommandDescriptor('go-to-line', _('Go to Line'), _('Edit'), 'go-to-line', ('line', 'navigation')),
    CommandDescriptor('duplicate-line', _('Duplicate Line'), _('Edit'), 'duplicate-line', ('copy', 'line')),
    CommandDescriptor('delete-line', _('Delete Line'), _('Edit'), 'delete-line', ('remove', 'line')),
    CommandDescriptor('move-line-up', _('Move Line Up'), _('Edit'), 'move-line-up', ('line', 'move')),
    CommandDescriptor('move-line-down', _('Move Line Down'), _('Edit'), 'move-line-down', ('line', 'move')),
    CommandDescriptor('indent', _('Indent'), _('Edit'), 'indent', ('format', 'line')),
    CommandDescriptor('outdent', _('Outdent'), _('Edit'), 'outdent', ('format', 'line', 'unindent')),
    CommandDescriptor('toggle-comment', _('Toggle Comment'), _('Edit'), 'toggle-comment', ('comment', 'latex')),
    CommandDescriptor('toggle-bookmark', _('Toggle Bookmark'), _('Edit'), 'toggle-bookmark', ('bookmark', 'line')),
    CommandDescriptor('next-bookmark', _('Next Bookmark'), _('Edit'), 'next-bookmark', ('bookmark', 'navigation')),
    CommandDescriptor('previous-bookmark', _('Previous Bookmark'), _('Edit'), 'previous-bookmark', ('bookmark', 'navigation')),
    CommandDescriptor('clear-bookmarks', _('Clear Bookmarks'), _('Edit'), 'clear-bookmarks', ('bookmark', 'remove')),
    CommandDescriptor('wizard', _('Document Wizard'), _('LaTeX'), 'show-document-wizard', ('new', 'template', 'latex')),
    CommandDescriptor('packages', _('Manage Packages'), _('LaTeX'), 'add-remove-packages-dialog', ('package', 'latex')),
    CommandDescriptor('preamble-assistant', _('Preamble Assistant'), _('LaTeX'), 'show-preamble-assistant', ('package', 'preamble', 'suggestions', 'dependencies')),
    CommandDescriptor('include-bibtex', _('Include BibTeX File'), _('LaTeX'), 'include-bibtex-file', ('bibliography', 'bib')),
    CommandDescriptor('manage-bibliography', _('Manage Bibliography'), _('LaTeX'), 'manage-bibliography', ('bibliography', 'bib', 'citation', 'reference')),
    CommandDescriptor('include-latex', _('Include LaTeX File'), _('LaTeX'), 'include-latex-file', ('input', 'include', 'tex')),
    CommandDescriptor('insert-image', _('Insert Image'), _('LaTeX'), 'insert-image-dialog', ('figure', 'graphic', 'image')),
    CommandDescriptor('insert-table', _('Insert Table'), _('LaTeX'), 'insert-table-dialog', ('table', 'tabular', 'booktabs')),
    CommandDescriptor('insert-matrix', _('Insert Matrix'), _('LaTeX'), 'insert-matrix-dialog', ('matrix', 'pmatrix', 'bmatrix', 'math', 'mathtools')),
    CommandDescriptor('fold-all', _('Fold All'), _('View'), 'fold-all', ('collapse', 'sections')),
    CommandDescriptor('unfold-all', _('Unfold All'), _('View'), 'unfold-all', ('expand', 'sections')),
    CommandDescriptor('zoom-in', _('Zoom In'), _('View'), 'zoom-in', ('font', 'increase')),
    CommandDescriptor('zoom-out', _('Zoom Out'), _('View'), 'zoom-out', ('font', 'decrease')),
    CommandDescriptor('reset-zoom', _('Reset Zoom'), _('View'), 'reset-zoom', ('font', 'default')),
    CommandDescriptor('preview-search', _('Search in PDF'), _('Preview'), 'preview-search-pdf', ('find', 'pdf', 'preview')),
    CommandDescriptor('preview-source', _('Show Source from PDF'), _('Preview'), 'preview-show-source', ('synctex', 'pdf', 'source')),
    CommandDescriptor('preview-zoom-in', _('Zoom In PDF'), _('Preview'), 'preview-zoom-in', ('pdf', 'increase')),
    CommandDescriptor('preview-zoom-out', _('Zoom Out PDF'), _('Preview'), 'preview-zoom-out', ('pdf', 'decrease')),
    CommandDescriptor('preferences', _('Preferences'), _('Application'), 'show-preferences-dialog', ('settings', 'options'), 'show_preferences_dialog'),
    CommandDescriptor('document-properties', _('Document Properties'), _('Application'), 'show-document-properties', ('settings', 'document')),
    CommandDescriptor('keyboard-shortcuts', _('Keyboard Shortcuts'), _('Application'), 'show-shortcuts-dialog', ('shortcuts', 'help'), 'show_shortcuts'),
    CommandDescriptor('about', _('About NeoSetzer'), _('Application'), 'show-about-dialog', ('about', 'help'), 'show_about_dialog'),
    CommandDescriptor('fullscreen', _('Toggle Fullscreen'), _('Application'), 'toggle-fullscreen', ('fullscreen', 'window'), 'fullscreen'),
)


def normalize(value: str) -> str:
    """Return a case-insensitive, accent-insensitive search representation."""

    decomposed = unicodedata.normalize('NFKD', value)
    no_accents = ''.join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r'[^\w]+', ' ', no_accents.casefold()).strip()


def _word_score(query_word: str, field: str) -> int:
    if query_word == field:
        return 160
    if field.startswith(query_word):
        return 120
    if any(word.startswith(query_word) for word in field.split()):
        return 90
    if query_word in field:
        return 55
    return 0


def score(command: CommandDescriptor, query: str) -> int | None:
    """Score a command for a query; return ``None`` if it does not match.

    All query words must match at least one command field.  Titles are weighted
    most strongly, categories and aliases provide discoverability, and sorting
    stays deterministic for equal scores.
    """

    query_words = normalize(query).split()
    if not query_words:
        return 0
    fields: Sequence[tuple[str, int]] = (
        (normalize(command.title), 4),
        (normalize(command.category), 2),
        (normalize(command.action_name), 1),
        (normalize(' '.join(command.keywords)), 2),
    )
    total = 0
    for word in query_words:
        best = max((_word_score(word, field) * weight for field, weight in fields), default=0)
        if best == 0:
            return None
        total += best
    title = normalize(command.title)
    normalized_query = normalize(query)
    if title == normalized_query:
        total += 1000
    elif title.startswith(normalized_query):
        total += 400
    return total


def update_recent_command_ids(command_id: str, identifiers: Iterable[str],
                              limit: int = RECENT_COMMAND_LIMIT) -> list[str]:
    '''Return a de-duplicated, bounded MRU list with ``command_id`` first.'''

    if limit < 1:
        raise ValueError('The recent-command limit must be positive')
    remaining = []
    seen = {command_id}
    for identifier in identifiers:
        if isinstance(identifier, str) and identifier not in seen:
            remaining.append(identifier)
            seen.add(identifier)
    return [command_id] + remaining[:limit - 1]


def prioritize_recent(commands: Iterable[CommandDescriptor],
                      recent_identifiers: Iterable[str]) -> list[CommandDescriptor]:
    '''Move still-available recent commands to the front without duplication.'''

    command_list = list(commands)
    commands_by_id = {command.identifier: command for command in command_list}
    recent = []
    seen = set()
    for identifier in recent_identifiers:
        command = commands_by_id.get(identifier)
        if command is not None and identifier not in seen:
            recent.append(command)
            seen.add(identifier)
    return recent + [command for command in command_list if command.identifier not in seen]


def search(commands: Iterable[CommandDescriptor], query: str) -> list[CommandDescriptor]:
    """Return commands ordered by relevance, then by category and title."""

    scored = []
    for command in commands:
        value = score(command, query)
        if value is not None:
            scored.append((value, command))
    return [
        command for _, command in sorted(
            scored,
            key=lambda item: (-item[0], normalize(item[1].category), normalize(item[1].title), item[1].identifier),
        )
    ]


class CommandCatalog:
    """Adapt command descriptors to the workspace's existing Gio actions."""

    def __init__(self, actions, commands: Iterable[CommandDescriptor] = COMMANDS):
        self.actions = actions
        self.commands = tuple(commands)

    def get_action(self, command: CommandDescriptor):
        return self.actions.actions.get(command.action_name)

    def is_available(self, command: CommandDescriptor) -> bool:
        action = self.get_action(command)
        return action is not None and action.get_enabled()

    def available(self) -> list[CommandDescriptor]:
        return [command for command in self.commands if self.is_available(command)]

    def search_groups(self, query: str,
                      recent_identifiers: Iterable[str] = ()) -> tuple[CommandResultGroup, ...]:
        '''Return grouped matches without making unavailable commands executable.'''

        matches = search(self.commands, query)
        available = tuple(command for command in matches if self.is_available(command))
        unavailable = tuple(command for command in matches if not self.is_available(command))
        if not normalize(query):
            available_by_id = {command.identifier: command for command in available}
            actual_recent = []
            seen = set()
            for identifier in recent_identifiers:
                command = available_by_id.get(identifier)
                if command is not None and identifier not in seen:
                    actual_recent.append(command)
                    seen.add(identifier)
            remaining = tuple(command for command in available if command.identifier not in seen)
            groups = []
            if actual_recent:
                groups.append(CommandResultGroup('recent', tuple(actual_recent), True))
            if remaining:
                groups.append(CommandResultGroup('all', remaining, True))
            return tuple(groups)
        groups = []
        if available:
            groups.append(CommandResultGroup('available', available, True))
        if unavailable:
            groups.append(CommandResultGroup('unavailable', unavailable, False))
        return tuple(groups)

    def search(self, query: str,
               recent_identifiers: Iterable[str] = ()) -> list[CommandDescriptor]:
        '''Return only executable commands for legacy callers and direct tests.'''

        return [
            command
            for group in self.search_groups(query, recent_identifiers)
            if group.available
            for command in group.commands
        ]

    def execute(self, command: CommandDescriptor) -> bool:
        """Activate a currently enabled parameter-free action safely."""

        action = self.get_action(command)
        if action is None or not action.get_enabled():
            return False
        action.activate(None)
        return True

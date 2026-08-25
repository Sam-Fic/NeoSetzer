#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Regression tests for Workspace's debounced realtime persistence (#373).

Workspace imports the full GTK application graph, while these behaviours only
need the small scheduling methods.  The test extracts those methods from the
production AST and runs them with the shared GLib timer stub.  This keeps the
assertions tied to the production implementation without requiring a display.
'''

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from tests.python import conftest_stub  # noqa: F401 - installs the GI stub
from gi.repository import GLib


WORKSPACE_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'workspace' / 'workspace.py'
)


def _workspace_members(*names):
    '''Return selected Workspace members compiled from the production source.'''
    tree = ast.parse(WORKSPACE_SOURCE.read_text(encoding='utf-8'))
    workspace = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Workspace'
    )
    selected = [
        node for node in workspace.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError('Workspace methods missing from production source: ' + repr(missing))
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {'GLib': GLib}
    exec(compile(ast.fix_missing_locations(module), str(WORKSPACE_SOURCE), 'exec'), namespace)
    return {name: namespace[name] for name in names}


def _workspace_persistence_delay():
    tree = ast.parse(WORKSPACE_SOURCE.read_text(encoding='utf-8'))
    workspace = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Workspace'
    )
    for node in workspace.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == 'PERSISTENCE_DELAY_MS'
                   for target in node.targets):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                    return node.value.value
    raise AssertionError('Workspace.PERSISTENCE_DELAY_MS is missing or not an integer')


_PERSISTENCE_METHODS = _workspace_members(
    'schedule_persistence',
    '_cancel_scheduled_persistence',
    '_flush_scheduled_persistence',
    'flush_persistence',
    '_on_document_state_changed',
)


class WorkspacePersistenceHarness:
    PERSISTENCE_DELAY_MS = _workspace_persistence_delay()


for _name, _method in _PERSISTENCE_METHODS.items():
    setattr(WorkspacePersistenceHarness, _name, _method)


class TestWorkspaceRealtimePersistence(unittest.TestCase):

    def setUp(self):
        GLib._next_source_id = 0
        GLib._sources = {}
        self.workspace = WorkspacePersistenceHarness()
        self.workspace._persistence_source_id = None
        self.workspace.save_to_disk = Mock(return_value=True)

    def test_document_state_changes_share_one_debounced_save(self):
        self.workspace._on_document_state_changed()
        first_source_id = self.workspace._persistence_source_id
        self.assertEqual(
            GLib._sources[first_source_id][0],
            WorkspacePersistenceHarness.PERSISTENCE_DELAY_MS,
        )

        self.workspace._on_document_state_changed()
        second_source_id = self.workspace._persistence_source_id

        self.assertNotEqual(first_source_id, second_source_id)
        self.assertNotIn(first_source_id, GLib._sources)
        self.assertIn(second_source_id, GLib._sources)
        self.assertEqual(len(GLib._sources), 1)
        self.workspace.save_to_disk.assert_not_called()

    def test_scheduled_callback_saves_once_and_clears_timer(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id
        _, callback, callback_args = GLib._sources[source_id]

        self.assertFalse(callback(*callback_args))
        self.assertIsNone(self.workspace._persistence_source_id)
        self.workspace.save_to_disk.assert_called_once_with()

    def test_final_flush_cancels_pending_timer_and_saves_immediately(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id

        self.assertTrue(self.workspace.flush_persistence())

        self.assertNotIn(source_id, GLib._sources)
        self.assertIsNone(self.workspace._persistence_source_id)
        self.workspace.save_to_disk.assert_called_once_with()

    def test_cancel_tolerates_a_timer_already_removed_by_glib(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id
        del GLib._sources[source_id]

        self.workspace._cancel_scheduled_persistence()

        self.assertIsNone(self.workspace._persistence_source_id)
        self.workspace.save_to_disk.assert_not_called()

    def test_structural_session_changes_keep_scheduling_persistence(self):
        tree = ast.parse(WORKSPACE_SOURCE.read_text(encoding='utf-8'))
        workspace = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'Workspace'
        )
        required_methods = {
            'add_document',
            'remove_document',
            'set_active_document',
            '_update_recently_opened',
            'clear_recently_opened_documents',
            'toggle_pinned_recent_document',
        }
        methods = {
            node.name: node for node in workspace.body
            if isinstance(node, ast.FunctionDef) and node.name in required_methods
        }
        self.assertEqual(set(methods), required_methods)
        for name, method in methods.items():
            calls_schedule = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'self'
                and node.func.attr == 'schedule_persistence'
                for node in ast.walk(method)
            )
            self.assertTrue(calls_schedule, name + ' must schedule a workspace snapshot')


if __name__ == '__main__':
    unittest.main()

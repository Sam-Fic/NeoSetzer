#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Regression tests for tab pinning (document._pinned) persistence.

Workspace imports the full GTK application graph, while the pin behaviours
only need three Workspace methods.  Following the established AST-extraction
pattern (see test_workspace_realtime_persistence), the methods are compiled
from the production source and run against Mock documents/workspaces, so the
assertions stay tied to the production implementation without a display.

提取说明：_collect_open_documents_data 的「未命名文档」分支用到 uuid，与
os 同为调用期在 __globals__ 里解引用的名字；本测试全部走已命名文档分支，
仍预置 os/uuid 以防用例演化时踩到 NameError。
'''

import ast
import os
import uuid
from pathlib import Path
import unittest
from unittest.mock import Mock


WORKSPACE_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'workspace' / 'workspace.py'
)

PIN_METHODS = (
    'toggle_pin_document',
    'is_document_pinned',
    '_collect_open_documents_data',
    '_restore_document_state',
)


def _workspace_members(*names):
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
    namespace = {'GLib': None, 'os': os, 'uuid': uuid}
    exec(compile(ast.fix_missing_locations(module), str(WORKSPACE_SOURCE), 'exec'), namespace)
    return {name: namespace[name] for name in names}


_METHODS = _workspace_members(*PIN_METHODS)


def _make_workspace(documents=()):
    workspace = Mock()
    workspace.open_documents = list(documents)
    return workspace


def _make_document(filename='/tmp/test.tex', pinned=False):
    document = Mock()
    document.get_filename.return_value = filename
    document.get_last_activated.return_value = 1.0
    document.get_displayname.return_value = 'test.tex'
    document.language = 'latex'
    document.source_buffer.get_has_selection.return_value = False
    document.source_buffer.get_property.return_value = 0
    document.view.scrolled_window.get_vadjustment().get_value.return_value = 0.0
    document.code_folding.get_folded_regions.return_value = []
    document._pinned = pinned
    return document


class TestTabPinToggle(unittest.TestCase):

    def setUp(self):
        self.document = _make_document()
        self.workspace = _make_workspace([self.document])

    def test_toggle_pins_and_emits_change_code(self):
        _METHODS['toggle_pin_document'](self.workspace, self.document)

        self.assertTrue(self.document._pinned)
        self.assertTrue(_METHODS['is_document_pinned'](self.workspace, self.document))
        self.workspace.add_change_code.assert_called_once_with(
            'document_pin_state_changed', self.document)
        self.workspace.schedule_persistence.assert_called_once()

    def test_toggle_twice_restores_unpinned_state(self):
        _METHODS['toggle_pin_document'](self.workspace, self.document)
        _METHODS['toggle_pin_document'](self.workspace, self.document)

        self.assertFalse(self.document._pinned)
        self.assertFalse(_METHODS['is_document_pinned'](self.workspace, self.document))
        self.assertEqual(self.workspace.add_change_code.call_count, 2)

    def test_toggle_ignores_unknown_document(self):
        stranger = _make_document(filename='/tmp/other.tex')

        _METHODS['toggle_pin_document'](self.workspace, stranger)
        _METHODS['toggle_pin_document'](self.workspace, None)

        self.assertFalse(stranger._pinned)
        self.workspace.add_change_code.assert_not_called()
        self.workspace.schedule_persistence.assert_not_called()


class TestTabPinPersistence(unittest.TestCase):

    def setUp(self):
        self.document = _make_document()
        self.workspace = _make_workspace([self.document])

    def test_pinned_document_is_saved_with_pinned_flag(self):
        self.document._pinned = True

        open_documents, _ = _METHODS['_collect_open_documents_data'](self.workspace)

        self.assertTrue(open_documents['/tmp/test.tex']['pinned'])

    def test_unpinned_document_omits_pinned_flag(self):
        open_documents, _ = _METHODS['_collect_open_documents_data'](self.workspace)

        self.assertNotIn('pinned', open_documents['/tmp/test.tex'])

    def test_restore_applies_pinned_state_and_notifies_presenter(self):
        item = {'last_activated': 1.0, 'pinned': True}

        _METHODS['_restore_document_state'](self.workspace, self.document, item, None)

        self.assertTrue(self.document._pinned)
        self.workspace.add_change_code.assert_called_once_with(
            'document_pin_state_changed', self.document)

    def test_restore_without_flag_keeps_document_unpinned(self):
        item = {'last_activated': 1.0}

        _METHODS['_restore_document_state'](self.workspace, self.document, item, None)

        self.assertFalse(self.document._pinned)
        # 状态未变时不发 document_pin_state_changed，避免会话恢复期间
        # 对每个文档都空转一次 presenter 同步。
        self.workspace.add_change_code.assert_not_called()

    def test_session_roundtrip_preserves_pin_state(self):
        self.document._pinned = True
        open_documents, untitled = _METHODS['_collect_open_documents_data'](self.workspace)
        item = dict(open_documents['/tmp/test.tex'])

        restored = _make_document()
        workspace = _make_workspace([restored])
        _METHODS['_restore_document_state'](workspace, restored, item, None)

        self.assertTrue(restored._pinned)


if __name__ == '__main__':
    unittest.main()

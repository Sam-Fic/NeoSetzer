#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Regression tests for Workspace's debounced realtime persistence (#373).

Workspace imports the full GTK application graph, while these behaviours only
need scheduling and content persistence methods.  The test extracts them from the
production AST and runs them with the shared GLib timer stub.  This keeps the
assertions tied to the production implementation without requiring a display.

GLib 身份说明：本文件刻意不导入任何 gi。全套件运行时，字母序靠前的测试
（如 test_matrix_generator）会先加载真实 gi；若提取出的方法解析到真实
GLib，真实 timeout_add 登记的 id 与测试重置的假 _sources 对不上，产生
KeyError。因此每个用例在 setUp 用 make_glib_stub() 构建独立桩实例，并
替换进提取代码共享的 globals 字典（exec 出的函数以它为 __globals__，
调用时才解引用 GLib 名字），桩/真机环境下行为完全一致。
'''

import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

from tests.python import conftest_stub


WORKSPACE_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'workspace' / 'workspace.py'
)


def _workspace_members(*names):
    '''Return selected Workspace members compiled from the production source.

    返回 ``(methods, namespace)``：namespace 是 exec 的 globals 字典，其中
    ``'GLib'`` 键在每次 setUp 时被替换为独立的桩实例（函数调用时动态查找）。
    '''
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
    namespace = {'GLib': None}
    exec(compile(ast.fix_missing_locations(module), str(WORKSPACE_SOURCE), 'exec'), namespace)
    return {name: namespace[name] for name in names}, namespace


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


_PERSISTENCE_METHODS, _PERSISTENCE_NAMESPACE = _workspace_members(
    'schedule_persistence',
    '_cancel_scheduled_persistence',
    '_flush_scheduled_persistence',
    'flush_persistence',
    '_on_document_state_changed',
    '_on_document_content_changed',
    '_watch_document_state',
    '_unwatch_document_state',
    '_collect_open_documents_data',
    '_save_untitled_content',
    '_restore_untitled_document',
    'remove_document',
)


class WorkspacePersistenceHarness:
    PERSISTENCE_DELAY_MS = _workspace_persistence_delay()


for _name, _method in _PERSISTENCE_METHODS.items():
    setattr(WorkspacePersistenceHarness, _name, _method)


class SignalStub:
    def __init__(self):
        self.handlers = {}
        self.next_id = 0

    def connect(self, signal, callback, *user_data):
        self.next_id += 1
        self.handlers[self.next_id] = (signal, callback, user_data)
        return self.next_id

    def disconnect(self, handler_id):
        del self.handlers[handler_id]

    def emit(self, signal, *args):
        for name, callback, user_data in list(self.handlers.values()):
            if name == signal:
                callback(self, *args, *user_data)


class DocumentStub:
    def __init__(self, filename=None, text='draft'):
        self.get_filename = Mock(return_value=filename)
        self.get_last_activated = Mock(return_value=1)
        self.get_displayname = Mock(return_value='Untitled')
        self.set_displayname = Mock()
        self.language = 'latex'
        self.source_buffer = SignalStub()
        self.source_buffer.get_start_iter = Mock(return_value=0)
        self.source_buffer.get_end_iter = Mock(return_value=len(text))
        self.source_buffer.get_text = Mock(return_value=text)
        self.source_buffer.get_property = Mock(return_value=0)
        self.source_buffer.get_has_selection = Mock(return_value=False)
        self.source_buffer.set_modified = Mock()
        self.adjustment = SignalStub()
        self.adjustment.get_value = Mock(return_value=0)
        self.view = Mock()
        self.view.scrolled_window.get_vadjustment.return_value = self.adjustment
        self.code_folding = Mock()
        self.code_folding.get_folded_regions.return_value = []


class TestWorkspaceRealtimePersistence(unittest.TestCase):

    def setUp(self):
        # 独立 GLib 桩：登记表、自增 id 均为本测试私有，杜绝环境串扰。
        self.glib = conftest_stub.make_glib_stub()
        # 提取出的方法以 _PERSISTENCE_NAMESPACE 为 __globals__，调用时才
        # 解引用 GLib 名字；换入新桩即改变其行为，无需重新 exec。
        _PERSISTENCE_NAMESPACE['GLib'] = self.glib
        self.workspace = WorkspacePersistenceHarness()
        self.workspace._persistence_source_id = None
        self.workspace.save_to_disk = Mock(return_value=True)
        self.workspace.open_documents = []
        self.workspace._document_state_handlers = {}
        self.workspace._untitled_content_path = Mock(
            side_effect=lambda uid: f'/unused/{uid}.content')
        self.write_content = Mock()
        _PERSISTENCE_NAMESPACE.update(uuid=uuid, atomic_write_text=self.write_content)

    def _add_document(self, document):
        self.workspace.open_documents.append(document)
        self.workspace._watch_document_state(document)

    def test_document_state_changes_share_one_debounced_save(self):
        self.workspace._on_document_state_changed()
        first_source_id = self.workspace._persistence_source_id
        self.assertEqual(
            self.glib._sources[first_source_id][0],
            WorkspacePersistenceHarness.PERSISTENCE_DELAY_MS,
        )

        self.workspace._on_document_state_changed()
        second_source_id = self.workspace._persistence_source_id

        self.assertNotEqual(first_source_id, second_source_id)
        self.assertNotIn(first_source_id, self.glib._sources)
        self.assertIn(second_source_id, self.glib._sources)
        self.assertEqual(len(self.glib._sources), 1)
        self.workspace.save_to_disk.assert_not_called()

    def test_scheduled_callback_saves_once_and_clears_timer(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id
        _, callback, callback_args = self.glib._sources[source_id]

        self.assertFalse(callback(*callback_args))
        self.assertIsNone(self.workspace._persistence_source_id)
        self.workspace.save_to_disk.assert_called_once_with()

    def test_final_flush_cancels_pending_timer_and_saves_immediately(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id

        self.assertTrue(self.workspace.flush_persistence())

        self.assertNotIn(source_id, self.glib._sources)
        self.assertIsNone(self.workspace._persistence_source_id)
        self.workspace.save_to_disk.assert_called_once_with()

    def test_cancel_tolerates_a_timer_already_removed_by_glib(self):
        self.workspace.schedule_persistence()
        source_id = self.workspace._persistence_source_id
        del self.glib._sources[source_id]

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

    def test_repeated_view_snapshots_do_not_read_or_write_saved_content(self):
        documents = [DocumentStub(), DocumentStub()]
        for document in documents:
            self.assertFalse(hasattr(document, '_untitled_content_saved'))
            self._add_document(document)
        self.workspace._collect_open_documents_data()

        for offset in (10, 20):
            document = documents[0]
            document.source_buffer.get_property.return_value = offset
            document.adjustment.get_value.return_value = offset * 2
            document.source_buffer.emit('notify::cursor-position', None)
            document.adjustment.emit('value-changed')
            _, untitled = self.workspace._collect_open_documents_data()
            self.assertEqual(untitled[document._untitled_id]['cursor_offset'], offset)
            self.assertEqual(untitled[document._untitled_id]['scroll_offset'], offset * 2)

        self.assertEqual(self.write_content.call_count, 2)
        for document in documents:
            self.assertIs(document._untitled_content_saved, True)
            document.source_buffer.get_text.assert_called_once()
            document.source_buffer.set_modified.assert_not_called()

    def test_edits_invalidate_only_the_changed_untitled_document(self):
        changed, unchanged = DocumentStub(), DocumentStub()
        for document in (changed, unchanged):
            self._add_document(document)
        self.workspace._collect_open_documents_data()
        self.write_content.reset_mock()
        for document in (changed, unchanged):
            document.source_buffer.get_text.reset_mock()

        changed.source_buffer.get_text.return_value = 'edited'
        changed.source_buffer.emit('changed')
        changed.source_buffer.emit('changed')
        self.assertIs(changed._untitled_content_saved, False)
        self.assertIs(unchanged._untitled_content_saved, True)
        self.assertEqual(len(self.glib._sources), 1)
        self.write_content.assert_not_called()
        changed.source_buffer.get_text.assert_not_called()

        self.workspace._collect_open_documents_data()
        self.write_content.assert_called_once_with(
            f'/unused/{changed._untitled_id}.content', 'edited')
        changed.source_buffer.get_text.assert_called_once()
        unchanged.source_buffer.get_text.assert_not_called()
        self.assertIs(changed._untitled_content_saved, True)
        for document in (changed, unchanged):
            document.source_buffer.set_modified.assert_not_called()

    def test_named_document_edits_do_not_schedule_content_snapshots(self):
        document = DocumentStub(filename='/unused/named.tex')
        self._add_document(document)
        document.source_buffer.emit('changed')
        self.workspace._collect_open_documents_data()

        self.assertIs(document._untitled_content_saved, False)
        self.assertIsNone(self.workspace._persistence_source_id)
        document.source_buffer.get_text.assert_not_called()
        document.source_buffer.set_modified.assert_not_called()
        self.write_content.assert_not_called()

    def test_edits_after_save_as_invalidate_the_previous_recovery_copy(self):
        document = DocumentStub()
        self._add_document(document)
        self.workspace._collect_open_documents_data()
        self.write_content.reset_mock()
        document.get_filename.return_value = '/unused/saved.tex'
        document.source_buffer.get_text.return_value = 'new contents'
        document.source_buffer.emit('changed')
        document.get_filename.return_value = None
        self.workspace._collect_open_documents_data()
        self.write_content.assert_called_once_with(
            f'/unused/{document._untitled_id}.content', 'new contents')

    def test_failed_content_write_is_retried_on_the_next_snapshot(self):
        for edited in (False, True):
            with self.subTest(edited=edited):
                document = DocumentStub()
                self.workspace.open_documents = []
                self._add_document(document)
                self.write_content.reset_mock(side_effect=True)
                if edited:
                    self.workspace._collect_open_documents_data()
                    document.source_buffer.emit('changed')
                document.source_buffer.get_text.reset_mock()
                self.write_content.reset_mock()
                self.write_content.side_effect = [OSError('disk full'), None]

                with patch('builtins.print'):
                    _, untitled = self.workspace._collect_open_documents_data()
                self.assertIsNot(getattr(document, '_untitled_content_saved', None), True)
                self.assertIn(document._untitled_id, untitled)
                self.workspace._collect_open_documents_data()
                self.assertIs(document._untitled_content_saved, True)
                self.workspace._collect_open_documents_data()

                self.assertEqual(document.source_buffer.get_text.call_count, 2)
                self.assertEqual(self.write_content.call_count, 2)
                self.assertEqual(*self.write_content.call_args_list)
                document.source_buffer.set_modified.assert_not_called()

    def test_content_write_includes_hidden_folded_text(self):
        full_text = 'visible\n折叠内容\n'
        document = DocumentStub(text=full_text)
        document.source_buffer.get_text.side_effect = (
            lambda start, end, include_hidden_chars:
            full_text if include_hidden_chars else 'visible\n')
        self._add_document(document)

        self.workspace._collect_open_documents_data()

        document.source_buffer.get_text.assert_called_once_with(
            0, len(full_text), include_hidden_chars=True)
        self.write_content.assert_called_once_with(
            f'/unused/{document._untitled_id}.content', full_text)
        document.source_buffer.set_modified.assert_not_called()

    def test_restored_saved_flag_survives_watching_and_view_snapshots(self):
        document = DocumentStub(text='restored')
        document.source_buffer.begin_irreversible_action = Mock()
        document.source_buffer.set_text = Mock()
        document.source_buffer.end_irreversible_action = Mock()
        self.workspace._load_untitled_content = Mock(return_value='restored')
        self.workspace.create_latex_document = Mock(return_value=document)
        self.workspace.add_document = Mock(side_effect=self._add_document)
        self.workspace._restore_document_state = Mock()

        restored = self.workspace._restore_untitled_document(
            {'untitled_id': 'restored-id'}, attach_preview=False)
        self.assertIs(restored, document)
        self.assertIs(document._untitled_content_saved, True)
        document.source_buffer.set_text.assert_called_once_with('restored')
        document.source_buffer.emit('notify::cursor-position', None)
        document.adjustment.emit('value-changed')
        _, untitled = self.workspace._collect_open_documents_data()
        self.assertIn('restored-id', untitled)
        self.assertIs(document._untitled_content_saved, True)
        document.source_buffer.get_text.assert_not_called()
        self.write_content.assert_not_called()
        document.source_buffer.set_modified.assert_called_once_with(False)

        document.source_buffer.emit('changed')
        self.assertIs(document._untitled_content_saved, False)

    def test_closing_document_disconnects_content_and_view_handlers(self):
        document = DocumentStub()
        document.controller = Mock()
        document.shutdown = Mock()
        document.is_latex_document = Mock(return_value=False)
        document._untitled_content_saved = True
        self._add_document(document)
        self.assertEqual(len(self.workspace._document_state_handlers[document]), 3)
        self.workspace.root_document = None
        self.workspace.active_document = None
        self.workspace.preview_popped_out = False
        self.workspace._cleanup_untitled_content = Mock()
        self.workspace.add_change_code = Mock()

        with patch.dict(_PERSISTENCE_NAMESPACE,
                        DocumentSettings=Mock(), LaTeXDB=Mock()):
            self.workspace.remove_document(document)
        self.assertNotIn(document, self.workspace.open_documents)
        self.assertNotIn(document, self.workspace._document_state_handlers)
        self.assertEqual(document.source_buffer.handlers, {})
        self.assertEqual(document.adjustment.handlers, {})
        self.workspace._cancel_scheduled_persistence()

        document.source_buffer.emit('changed')
        document.source_buffer.emit('notify::cursor-position', None)
        document.adjustment.emit('value-changed')
        self.assertIs(document._untitled_content_saved, True)
        self.assertEqual(self.glib._sources, {})


if __name__ == '__main__':
    unittest.main()

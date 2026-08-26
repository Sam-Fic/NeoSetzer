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
from unittest.mock import Mock

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
)


class WorkspacePersistenceHarness:
    PERSISTENCE_DELAY_MS = _workspace_persistence_delay()


for _name, _method in _PERSISTENCE_METHODS.items():
    setattr(WorkspacePersistenceHarness, _name, _method)


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


if __name__ == '__main__':
    unittest.main()

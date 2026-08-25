#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Orchestration tests for the user file-template action (#279).

The file-template service tests cover filtering and atomic copying.  This test
extracts the action-layer methods from production source and supplies minimal
GTK/FileDialog substitutes, exercising the critical asynchronous hand-off:
choose target -> copy source -> open the new document.
'''

import ast
import os
from pathlib import Path
import unittest
from unittest.mock import Mock

from setzer.dialogs.document_wizard.file_templates import FileTemplateError


ACTIONS_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'workspace' / 'actions' / 'actions.py'
)


class _GLib:
    class Error(Exception):
        pass


class _File:
    def __init__(self, path):
        self.path = path

    def get_path(self):
        return self.path


class _FileDialog:
    instances = []

    def __init__(self):
        self.modal = None
        self.title = None
        self.initial_name = None
        self.initial_folder = None
        self.parent = None
        self.callback = None
        _FileDialog.instances.append(self)

    def set_modal(self, modal):
        self.modal = modal

    def set_title(self, title):
        self.title = title

    def set_initial_name(self, name):
        self.initial_name = name

    def set_initial_folder(self, folder):
        self.initial_folder = folder

    def save(self, parent, cancellable, callback):
        self.parent = parent
        self.callback = callback

    def save_finish(self, result):
        if isinstance(result, BaseException):
            raise result
        return result

    def respond(self, result):
        self.callback(self, result)


class _Gtk:
    FileDialog = _FileDialog


class _GioFile:
    @staticmethod
    def new_for_path(path):
        return ('gio-file', path)


class _Gio:
    File = _GioFile


_COPY_FILE_TEMPLATE = Mock()


def _action_methods(*names):
    tree = ast.parse(ACTIONS_SOURCE.read_text(encoding='utf-8'))
    actions = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Actions'
    )
    selected = [
        node for node in actions.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError('Actions methods missing from production source: ' + repr(missing))
    namespace = {
        'os': os,
        'Gtk': _Gtk,
        'Gio': _Gio,
        'GLib': _GLib,
        'FileTemplateError': FileTemplateError,
        'copy_file_template': _COPY_FILE_TEMPLATE,
        '_': lambda message: message,
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ACTIONS_SOURCE), 'exec'), namespace)
    return {name: namespace[name] for name in names}


_ACTION_METHODS = _action_methods(
    '_choose_file_template_destination',
    '_on_file_template_destination_chosen',
)


class _ActiveDocument:
    def __init__(self, dirname):
        self.dirname = dirname

    def get_dirname(self):
        return self.dirname


class _Workspace:
    def __init__(self, active_document=None):
        self.active_document = active_document
        self.open_document_by_filename = Mock()

    def get_active_document(self):
        return self.active_document


class _ActionsHarness:
    pass


for _name, _method in _ACTION_METHODS.items():
    setattr(_ActionsHarness, _name, _method)


class TestFileTemplateActions(unittest.TestCase):

    def setUp(self):
        _FileDialog.instances = []
        _COPY_FILE_TEMPLATE.reset_mock(return_value=True, side_effect=True)
        self.workspace = _Workspace(_ActiveDocument('/home/writer/papers'))
        self.actions = _ActionsHarness()
        self.actions.main_window = object()
        self.actions.workspace = self.workspace
        self.actions._show_file_template_toast = Mock()

    def test_destination_choice_copies_then_opens_new_document(self):
        source = '/home/writer/Templates/article.tex'
        destination = '/home/writer/papers/paper.tex'

        self.actions._choose_file_template_destination(source)

        self.assertEqual(len(_FileDialog.instances), 1)
        dialog = _FileDialog.instances[0]
        self.assertTrue(dialog.modal)
        self.assertEqual(dialog.initial_name, 'article.tex')
        self.assertEqual(dialog.initial_folder, ('gio-file', '/home/writer/papers'))
        self.assertIs(dialog.parent, self.actions.main_window)

        dialog.respond(_File(destination))

        _COPY_FILE_TEMPLATE.assert_called_once_with(source, destination)
        self.workspace.open_document_by_filename.assert_called_once_with(destination)
        self.actions._show_file_template_toast.assert_not_called()

    def test_cancelled_destination_dialog_never_copies_or_opens(self):
        self.actions._choose_file_template_destination('/tmp/template.tex')

        _FileDialog.instances[0].respond(_GLib.Error('dismissed'))

        _COPY_FILE_TEMPLATE.assert_not_called()
        self.workspace.open_document_by_filename.assert_not_called()
        self.actions._show_file_template_toast.assert_not_called()

    def test_non_local_destination_is_reported_without_copying(self):
        self.actions._choose_file_template_destination('/tmp/template.tex')

        _FileDialog.instances[0].respond(_File(None))

        _COPY_FILE_TEMPLATE.assert_not_called()
        self.workspace.open_document_by_filename.assert_not_called()
        self.actions._show_file_template_toast.assert_called_once_with(
            'The selected destination is not a local file.')

    def test_copy_error_is_reported_without_opening_document(self):
        _COPY_FILE_TEMPLATE.side_effect = FileTemplateError('destination exists')
        self.actions._choose_file_template_destination('/tmp/template.tex')

        _FileDialog.instances[0].respond(_File('/tmp/paper.tex'))

        _COPY_FILE_TEMPLATE.assert_called_once_with('/tmp/template.tex', '/tmp/paper.tex')
        self.workspace.open_document_by_filename.assert_not_called()
        self.actions._show_file_template_toast.assert_called_once_with('destination exists')


if __name__ == '__main__':
    unittest.main()

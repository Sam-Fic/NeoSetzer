#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

import ast
from collections import Counter
from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / 'setzer/workspace/sidebar/document_structure_page'
Observable = runpy.run_path(str(REPO / 'setzer/helpers/observable.py'))['Observable']
path_helpers = SimpleNamespace(**runpy.run_path(str(REPO / 'setzer/helpers/path.py')))


def load_class(path, name, namespace, methods=None):
    # Extract production code without importing GTK or constructing the application.
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == name)
    if methods is not None:
        cls.body = [node for node in cls.body
                    if isinstance(node, ast.FunctionDef) and node.name in methods]
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


DocumentFilename = load_class(REPO / 'setzer/document/document.py', 'Document',
                              {'Observable': Observable, 'os': os}, {'set_filename'})
TodosSection = load_class(PAGE / 'todos.py', 'TodosSection', {}, {'update_items'})


class FakeGLib:
    def __init__(self):
        self.pending = {}
        self.next_id = 0

    def idle_add(self, callback):
        self.next_id += 1
        self.pending[self.next_id] = callback
        return self.next_id

    def drain(self):
        pending, self.pending = self.pending, {}
        for callback in pending.values():
            if callback() is not False:
                raise AssertionError('Structure refresh idle must be one-shot')
        if self.pending:
            raise AssertionError('Structure refresh must not install a repeating idle')


class TrackedObservable(Observable):
    def __init__(self):
        super().__init__()
        self.connections = Counter()
        self.disconnections = Counter()

    def connect(self, signal, callback):
        self.connections[signal] += 1
        super().connect(signal, callback)

    def disconnect(self, signal, callback):
        self.disconnections[signal] += 1
        super().disconnect(signal, callback)


class FakeParser(TrackedObservable):
    def __init__(self):
        super().__init__()
        self.symbols = {'included_latex_files': [], 'included_project_files': [],
                        'blocks': [], 'todos_with_offset': []}

    def finish(self, **symbols):
        self.symbols.update(symbols)
        self.add_change_code('finished_parsing')


class FakeDocument(TrackedObservable):
    set_filename = DocumentFilename.set_filename

    def __init__(self, filename, latex=True):
        super().__init__()
        self.filename = filename
        self.parser = FakeParser()
        self.source_buffer = Observable()
        self.is_root = False
        self.latex = latex

    def get_filename(self):
        return self.filename

    def get_dirname(self):
        return os.path.dirname(self.filename) if self.filename else '/project'

    def get_is_root(self):
        return self.is_root

    def is_latex_document(self):
        return self.latex

    def edit(self):
        self.source_buffer.add_change_code('changed')
        self.add_change_code('changed')
        self.add_change_code('cursor_position_changed')


class FakeWorkspace(Observable):
    def __init__(self, documents, active=None, root=None):
        super().__init__()
        self.open_documents = list(documents)
        self.active_document = active
        self.root_document = root
        if root is not None:
            root.is_root = True

    def get_all_documents(self):
        return self.open_documents.copy()

    def get_root_or_active_latex_document(self):
        if self.active_document is None:
            return None
        if self.root_document is not None:
            return self.root_document
        if self.active_document.is_latex_document():
            return self.active_document
        return None

    def get_document_by_filename(self, filename):
        return next((doc for doc in self.open_documents
                     if doc.get_filename() == filename), None)

    def add(self, document):
        self.open_documents.append(document)
        self.add_change_code('new_document', document)

    def activate(self, document):
        self.active_document = document
        self.add_change_code('new_active_document', document)

    def set_root(self, root):
        self.root_document = root
        for document in self.open_documents:
            if document.is_root != (document is root):
                document.is_root = document is root
                document.add_change_code('is_root_changed')
        self.add_change_code('root_state_change')

    def remove(self, document):
        if document is self.root_document:
            self.set_root(None)
        self.open_documents.remove(document)
        if document is self.active_document:
            self.activate(self.open_documents[0] if self.open_documents else None)
        self.add_change_code('document_removed', document)


class TestStructureRefresh(unittest.TestCase):
    def setUp(self):
        self.stderr = StringIO()
        capture = redirect_stderr(self.stderr)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        self.addCleanup(lambda: self.assertEqual(self.stderr.getvalue(), ''))
        self.glib = FakeGLib()
        self.provider_class = load_class(PAGE / 'data_provider.py', 'DataProvider', {
            'Observable': Observable, 'GLib': self.glib,
            'os': os, 'path_helpers': path_helpers,
        })
        self.root = FakeDocument('/project/main.tex')
        self.include = FakeDocument('/project/chapter.tex')
        self.other = FakeDocument('/project/other.tex')
        self.root.parser.symbols['included_latex_files'] = [('chapter.tex', 10)]
        self.workspace = FakeWorkspace([self.root, self.include, self.other],
                                       active=self.root, root=self.root)
        self.view = Mock()
        self.view.connect.return_value = 1
        self.provider = self.provider_class(SimpleNamespace(view=self.view), self.workspace)
        self.updated = Mock()
        self.documents_changed = Mock()
        self.cursor_changed = Mock()
        self.provider.connect('data_updated', self.updated)
        self.provider.connect('document_changed', self.documents_changed)
        self.provider.connect('cursor_position_changed', self.cursor_changed)
        self.provider.set_document()
        self.drain_once()
        self.updated.reset_mock()
        self.documents_changed.reset_mock()

    def drain_once(self):
        self.assertEqual(len(self.glib.pending), 1)
        self.glib.drain()
        self.assertIsNone(self.provider._update_data_idle_id)

    def assert_subscribed(self, document):
        self.assertEqual(document.parser.connected_functions.get('finished_parsing'),
                         {self.provider.on_finished_parsing})
        self.assertEqual(document.connected_functions.get('filename_change'),
                         {self.provider.on_filename_changed})
        self.assertNotIn('changed', document.connected_functions)
        self.assertNotIn('changed', document.source_buffer.connected_functions)

    def test_continuous_edits_wait_for_parsing_and_latest_symbols_are_visible(self):
        seen = []
        self.provider.connect('data_updated', lambda provider: seen.append(
            (list(provider.document.parser.symbols['blocks']),
             list(self.include.parser.symbols['blocks']))))
        for _ in range(12):
            for document in self.workspace.open_documents:
                document.edit()
            self.glib.drain()
        self.updated.assert_not_called()
        self.assertEqual(seen, [])
        self.assertEqual(self.cursor_changed.call_count, 12)
        self.root.parser.finish(blocks=['intermediate section'])
        self.include.parser.finish(blocks=['final included section'])
        self.root.parser.finish(blocks=['final section'])
        self.assertEqual(seen, [])
        self.drain_once()
        self.updated.assert_called_once_with(self.provider)
        self.assertEqual(seen, [(['final section'], ['final included section'])])
        self.root.parser.finish(blocks=['next section'])
        self.drain_once()
        self.assertEqual(seen[-1][0], ['next section'])

    def test_non_include_todos_refresh_from_finished_parsing(self):
        section = TodosSection()
        section.data_provider = self.provider
        section._show_all = True
        section.view = SimpleNamespace(populate=Mock())
        self.provider.connect('data_updated', section.update_items)
        self.assertNotIn(self.other, self.provider.integrated_includes)
        self.other.edit()
        self.glib.drain()
        section.view.populate.assert_not_called()
        self.other.parser.finish(todos_with_offset=[('Latest TODO', 25)])
        self.drain_once()
        self.assertEqual(section.todos, [['Latest TODO', 25, self.other]])
        section.view.populate.assert_called_once_with()
        self.workspace.remove(self.other)
        self.drain_once()
        self.assertEqual(section.todos, [])

    def test_initial_subscriptions_do_not_require_a_current_document(self):
        missing = FakeDocument('/project/no-parser.txt', latex=False)
        del missing.parser
        null = FakeDocument('/project/null-parser.txt', latex=False)
        null.parser = None
        bib = FakeDocument('/project/refs.bib', latex=False)
        workspace = FakeWorkspace([missing, null, bib])
        provider = self.provider_class(SimpleNamespace(view=Mock()), workspace)
        self.assertIsNone(provider.document)
        self.assertEqual(provider._subscribed_documents, {bib})
        self.assertEqual(bib.parser.connections['finished_parsing'], 1)
        self.assertFalse(missing.connected_functions)
        self.assertFalse(null.connected_functions)
        bib.parser.finish()
        self.assertEqual(len(self.glib.pending), 1)
        self.glib.drain()
        workspace.remove(bib)
        self.assertFalse(bib.connected_functions)
        self.assertFalse(bib.parser.connected_functions)
        self.glib.drain()
        bib.parser.finish()
        bib.set_filename('/project/closed.bib')
        self.assertFalse(self.glib.pending)
        self.assertFalse(provider._subscribed_documents)
        self.assertFalse(provider._subscribed_parsers)

    def test_new_document_subscribes_before_idle_and_coalesces_with_activation(self):
        document = FakeDocument('/project/new.tex')
        self.workspace.add(document)
        self.assert_subscribed(document)
        document.parser.finish(todos_with_offset=[('New TODO', 5)])
        self.workspace.set_root(None)
        self.workspace.activate(document)
        self.assertIs(self.provider.document, document)
        self.documents_changed.assert_called_once_with(self.provider, document)
        self.updated.assert_not_called()
        self.drain_once()
        self.updated.assert_called_once_with(self.provider)

    def test_includes_only_aggregate_and_do_not_reconnect_signals(self):
        for includes in ([('main.tex', 0), ('chapter.tex', 10), ('chapter.tex', 20)],
                         [], [('chapter.tex', 30)]):
            self.root.parser.finish(included_latex_files=includes)
            self.drain_once()
            for document in self.workspace.open_documents:
                self.assert_subscribed(document)
                self.assertEqual(document.parser.connections['finished_parsing'], 1)
                self.assertEqual(document.connections['filename_change'], 1)
        self.assertEqual(self.provider.integrated_includes,
                         {self.include: (self.include, 30)})
        self.assertEqual(self.provider.get_includes(), [
            {'filename': '/project/chapter.tex', 'offset': 30, 'document': self.include}])

    def test_shared_parser_is_connected_once_until_last_document_is_removed(self):
        alias = FakeDocument('/project/alias.tex')
        alias.parser = self.other.parser
        self.workspace.add(alias)
        for _ in range(3):
            self.workspace.add_change_code('new_document', alias)
        self.assert_subscribed(alias)
        self.assertEqual(alias.parser.connections['finished_parsing'], 1)
        self.workspace.remove(self.other)
        self.assertNotIn('filename_change', self.other.connected_functions)
        self.assert_subscribed(alias)
        self.assertEqual(alias.parser.disconnections['finished_parsing'], 0)
        self.workspace.remove(alias)
        self.assertFalse(alias.connected_functions)
        self.assertFalse(alias.parser.connected_functions)
        self.assertEqual(alias.parser.disconnections['finished_parsing'], 1)
        self.drain_once()

    def test_removed_include_disconnects_immediately_and_cannot_refresh_later(self):
        self.include.parser.finish()
        self.workspace.remove(self.include)
        self.assertFalse(self.include.connected_functions)
        self.assertFalse(self.include.parser.connected_functions)
        self.drain_once()
        self.assertNotIn(self.include, self.provider.integrated_includes)
        self.assertIsNone(self.provider.get_includes()[0]['document'])
        self.updated.reset_mock()
        self.include.parser.finish()
        self.include.set_filename('/project/closed.tex')
        self.include.edit()
        self.glib.drain()
        self.updated.assert_not_called()
        self.workspace.add(self.include)
        self.assert_subscribed(self.include)
        self.drain_once()

    def test_filename_change_refreshes_includes_and_root_relative_paths(self):
        self.include.set_filename('/project/renamed.tex')
        self.drain_once()
        self.assertNotIn(self.include, self.provider.integrated_includes)
        self.assertIsNone(self.provider.get_includes()[0]['document'])
        self.include.set_filename('/project/chapter.tex')
        self.drain_once()
        self.assertIn(self.include, self.provider.integrated_includes)
        self.root.set_filename('/project/subdir/main.tex')
        self.drain_once()
        self.assertEqual(self.provider.get_includes()[0]['filename'],
                         '/project/subdir/chapter.tex')
        self.assertIsNone(self.provider.get_includes()[0]['document'])
        self.other.set_filename(None)
        self.drain_once()
        self.other.set_filename('/project/saved.tex')
        self.drain_once()
        self.assertEqual(self.updated.call_count, 5)
        for document in self.workspace.open_documents:
            self.assert_subscribed(document)
            self.assertEqual(document.connections['filename_change'], 1)

    def test_root_and_active_switches_synchronously_transfer_cursor_signals(self):
        for document in (self.other, self.include, self.root):
            self.workspace.set_root(document)
            self.assertIs(self.provider.document, document)
            self.documents_changed.assert_called_with(self.provider, document)
            self.updated.assert_not_called()
            self.cursor_changed.reset_mock()
            for candidate in self.workspace.open_documents:
                candidate.add_change_code('cursor_position_changed')
                if candidate is not document:
                    self.assertNotIn('is_root_changed', candidate.connected_functions)
                    self.assertNotIn('cursor_position_changed', candidate.connected_functions)
                self.assert_subscribed(candidate)
                self.assertEqual(candidate.parser.connections['finished_parsing'], 1)
            self.cursor_changed.assert_called_once_with(self.provider, document)
        self.drain_once()
        self.workspace.set_root(None)
        self.drain_once()
        self.workspace.activate(self.other)
        self.assertIs(self.provider.document, self.other)
        self.documents_changed.assert_called_with(self.provider, self.other)
        self.drain_once()
        self.updated.reset_mock()
        self.root.add_change_code('is_root_changed')
        self.root.add_change_code('cursor_position_changed')
        self.assertFalse(self.glib.pending)
        self.root.parser.finish()
        self.drain_once()
        self.updated.assert_called_once_with(self.provider)

    def test_add_remove_without_current_document_and_close_all_leave_no_subscriptions(self):
        self.workspace.activate(None)
        self.assertIsNone(self.provider.document)
        self.documents_changed.assert_called_with(self.provider)
        self.drain_once()
        self.updated.reset_mock()
        temporary = FakeDocument('/project/temporary.tex')
        self.workspace.add(temporary)
        self.assert_subscribed(temporary)
        self.workspace.remove(temporary)
        self.assertFalse(temporary.connected_functions)
        self.assertFalse(temporary.parser.connected_functions)
        self.drain_once()
        self.updated.assert_not_called()
        self.workspace.activate(self.root)
        self.drain_once()
        for document in list(self.workspace.open_documents):
            self.workspace.remove(document)
            self.assertFalse(document.connected_functions)
            self.assertFalse(document.parser.connected_functions)
        self.assertIsNone(self.provider.document)
        self.assertFalse(self.provider._subscribed_documents)
        self.assertFalse(self.provider._subscribed_parsers)
        self.drain_once()
        for document in (self.root, self.include, self.other, temporary):
            document.parser.finish()
            document.set_filename(None)
            document.edit()
        self.assertFalse(self.glib.pending)

    def test_realize_and_current_root_state_refreshes_are_preserved(self):
        self.provider.on_realize(self.view)
        self.view.disconnect.assert_called_once_with(1)
        self.updated.assert_called_once_with(self.provider)
        self.assertFalse(self.glib.pending)
        self.root.add_change_code('is_root_changed')
        self.drain_once()
        self.assertEqual(self.updated.call_count, 2)


if __name__ == '__main__':
    unittest.main()

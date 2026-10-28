# coding: utf-8

import ast
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

from setzer.snippets.user_snippets import SnippetStoreError, UserSnippetStore


class UserSnippetStoreTest(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = UserSnippetStore(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_create_persists_and_normalizes_body(self):
        snippet = self.store.create('Section', '\\sec', '\\section{•}\r\n')

        self.assertEqual(snippet.name, 'Section')
        self.assertEqual(snippet.trigger, '\\sec')
        self.assertEqual(snippet.body, '\\section{•}\n')
        self.assertEqual(self.store.list_snippets(), [snippet])
        self.assertTrue(os.path.isfile(self.store.index_path))
        self.assertEqual(os.stat(self.store.index_path).st_mode & 0o777, 0o600)

    def test_update_keeps_identifier_and_created_timestamp(self):
        created = self.store.create('Bold', '\\bold', '\\textbf{•}')
        updated = self.store.update(created.identifier, 'Emphasis', '\\emph', '\\emph{•}')

        self.assertEqual(updated.identifier, created.identifier)
        self.assertEqual(updated.created_at, created.created_at)
        self.assertEqual(updated.name, 'Emphasis')
        self.assertEqual(updated.trigger, '\\emph')
        self.assertEqual(updated.body, '\\emph{•}')

    def test_delete_returns_false_for_missing_identifier(self):
        snippet = self.store.create('Bold', '\\bold', '\\textbf{•}')
        self.assertTrue(self.store.delete(snippet.identifier))
        self.assertFalse(self.store.delete(snippet.identifier))
        self.assertEqual(self.store.list_snippets(), [])

    def test_rejects_invalid_or_duplicate_trigger(self):
        self.store.create('Bold', '\\bold', '\\textbf{•}')

        with self.assertRaises(SnippetStoreError):
            self.store.create('Duplicate', '\\BOLD', 'duplicate')
        with self.assertRaises(SnippetStoreError):
            self.store.create('No slash', 'bold', 'text')
        with self.assertRaises(SnippetStoreError):
            self.store.create('Invalid punctuation', '\\bold-text', 'text')
        with self.assertRaises(SnippetStoreError):
            self.store.create('Empty', '\\empty', '   ')

    def test_proposals_are_casefolded_sorted_and_autocomplete_compatible(self):
        self.store.create('Math bold', '\\MathBold', '\\mathbf{•}')
        self.store.create('Matrix', '\\matrix', '\\begin{matrix}\n•\n\\end{matrix}')
        self.store.create('Section', '\\sectionnote', '\\section{•}')

        proposals = self.store.proposals_for('\\ma')

        self.assertEqual([item['command'] for item in proposals], ['\\MathBold', '\\matrix'])
        self.assertEqual(proposals[0]['description'], 'Math bold')
        self.assertEqual(proposals[0]['dotlabels'], '')
        self.assertTrue(proposals[0]['is_snippet'])
        self.assertEqual(proposals[0]['insert_text'], '\\mathbf{•}')
        self.assertEqual(self.store.proposals_for('matrix'), [])

    def test_ignores_damaged_record_but_keeps_valid_entries(self):
        valid = self.store.create('Section', '\\sec', '\\section{•}')
        with open(self.store.index_path, 'r', encoding='utf-8') as source:
            content = json.load(source)
        content['snippets'].append({'id': 'invalid', 'name': 'Broken'})
        with open(self.store.index_path, 'w', encoding='utf-8') as output:
            json.dump(content, output)

        self.assertEqual(self.store.list_snippets(), [valid])

    def test_reports_invalid_library_file(self):
        os.makedirs(self.store.directory)
        with open(self.store.index_path, 'w', encoding='utf-8') as output:
            output.write('{not json')

        with self.assertRaises(SnippetStoreError):
            self.store.list_snippets()
        self.assertEqual(self.store.proposals_for('\\x'), [])

    def test_multiple_prefixes_load_validate_and_sort_only_once(self):
        self.store.create('Section', '\\sectionnote', 'section')
        self.store.create('Matrix', '\\matrix', 'matrix')
        self.store.create('Math bold', '\\MathBold', 'bold')

        with patch('setzer.snippets.user_snippets.json.load', wraps=json.load) as load, \
                patch.object(UserSnippetStore, 'validate_body',
                             wraps=UserSnippetStore.validate_body) as validate, \
                patch('setzer.snippets.user_snippets.sorted', create=True, wraps=sorted) as sort:
            for prefix, expected in (
                    ('\\', ['\\MathBold', '\\matrix', '\\sectionnote']),
                    ('\\m', ['\\MathBold', '\\matrix']),
                    ('\\MA', ['\\MathBold', '\\matrix']),
                    ('\\matr', ['\\matrix']),
                    ('\\missing', [])):
                self.assertEqual([item['command'] for item in self.store.proposals_for(prefix)], expected)
            self.store.list_snippets()

        self.assertEqual(load.call_count, 1)
        self.assertEqual(validate.call_count, 3)
        self.assertEqual(sort.call_count, 1)

    def test_cached_snapshot_is_immutable_and_returns_independent_lists(self):
        snippet = self.store.create('Snippet', '\\snippet', 'body')
        first = self.store.list_snippets()
        second = self.store.list_snippets()
        self.assertIsNot(first, second)
        with self.assertRaises(FrozenInstanceError):
            first[0].body = 'changed'
        first.clear()
        second.append(None)
        self.assertEqual(self.store.list_snippets(), [snippet])

        proposals = self.store.proposals_for('\\s')
        proposals[0]['insert_text'] = 'changed'
        proposals.clear()
        self.assertEqual(self.store.proposals_for('\\s')[0]['insert_text'], 'body')

    def test_cached_sort_keeps_casefolded_trigger_and_name_order(self):
        first = self.store.create('zebra', '\\Alpha', 'first')
        second = self.store.create('Beta', '\\beta', 'second')
        third = self.store.create('apple', '\\other', 'third')
        # Manual records may share a trigger; preserve the existing name tie-break.
        records = [snippet.to_dict() for snippet in (first, second, third)]
        records[2]['trigger'] = '\\alpha'
        Path(self.store.index_path).write_text(
            json.dumps({'version': 1, 'snippets': records}), encoding='utf-8')

        for _ in range(2):
            self.assertEqual([snippet.name for snippet in self.store.list_snippets()],
                             ['apple', 'zebra', 'Beta'])

    def test_revision_is_a_cheap_complete_file_fingerprint(self):
        self.assertIsNone(self.store.revision[0])
        self.store.create('Snippet', '\\snippet', 'body')
        stat = os.stat(self.store.index_path)
        expected = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        revision = self.store.revision
        with patch.object(self.store, '_read_index') as read:
            self.assertEqual(revision[0], expected)
            self.assertEqual(self.store.revision, revision)
            read.assert_not_called()

        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        for field in fields:
            with self.subTest(field=field):
                changed = {name: getattr(stat, name) for name in fields}
                changed[field] += 1
                with patch('setzer.snippets.user_snippets.os.stat',
                           return_value=SimpleNamespace(**changed)):
                    self.assertNotEqual(self.store.revision[0], expected)

    def test_local_writes_explicitly_invalidate_even_with_unchanged_fingerprint(self):
        # A fixed revision isolates snapshot invalidation from OS timestamps and the serial.
        with patch.object(UserSnippetStore, 'revision', new_callable=PropertyMock,
                          return_value=((1, 2, 3, 4, 5), 0)):
            self.assertEqual(self.store.list_snippets(), [])
            created = self.store.create('Snippet', '\\snippet', 'old')
            self.assertEqual(self.store.list_snippets(), [created])
            updated = self.store.update(created.identifier, 'Snippet', '\\snippet', 'new')
            self.assertEqual(self.store.list_snippets(), [updated])
            self.assertTrue(self.store.delete(created.identifier))
            self.assertEqual(self.store.list_snippets(), [])

    def test_other_instance_create_update_and_delete_invalidate_cached_snapshot(self):
        other = UserSnippetStore(self.temporary_directory.name)
        self.assertEqual(self.store.list_snippets(), [])
        created = other.create('Snippet', '\\snippet', 'old')
        self.assertEqual(self.store.list_snippets(), [created])
        revision = self.store.revision
        updated = other.update(created.identifier, 'Snippet', '\\snippet', 'new')
        self.assertNotEqual(self.store.revision, revision)
        self.assertEqual(self.store.list_snippets(), [updated])
        other.delete(created.identifier)
        self.assertEqual(self.store.list_snippets(), [])

    def test_writes_read_fresh_records_and_preserve_validation(self):
        first = self.store.create('First', '\\first', 'first')
        self.store.list_snippets()
        other = UserSnippetStore(self.temporary_directory.name)
        second = other.create('Second', '\\second', 'second')
        with self.assertRaises(SnippetStoreError):
            self.store.create('Duplicate', '\\SECOND', 'duplicate')
        with self.assertRaises(SnippetStoreError):
            self.store.update(first.identifier, 'Duplicate', '\\SECOND', 'duplicate')
        with self.assertRaises(SnippetStoreError):
            self.store.update(first.identifier, 'Empty', '\\first', ' ')
        with self.assertRaises(SnippetStoreError):
            self.store.update('invalid', 'First', '\\first', 'first')
        third = self.store.create('Third', '\\third', 'third')
        self.assertEqual(other.list_snippets(), [first, second, third])
        self.store.list_snippets()
        updated = other.update(second.identifier, 'Second', '\\second', 'new')
        self.store.delete(first.identifier)
        self.assertEqual(other.list_snippets(), [updated, third])

    def test_atomic_replacement_with_same_size_and_mtime_invalidates_snapshot(self):
        self.store.create('Snippet', '\\snippet', 'old body')
        self.store.list_snippets()
        original = Path(self.store.index_path)
        stat = original.stat()
        revision = self.store.revision
        replacement = original.with_name('replacement.json')
        replacement.write_text(original.read_text(encoding='utf-8').replace('old body', 'new body'),
                               encoding='utf-8')
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, original)

        current = original.stat()
        self.assertEqual(current.st_size, stat.st_size)
        self.assertEqual(current.st_mtime_ns, stat.st_mtime_ns)
        self.assertNotEqual(current.st_ino, stat.st_ino)
        self.assertNotEqual(self.store.revision, revision)
        self.assertEqual(self.store.proposals_for('\\s')[0]['insert_text'], 'new body')

    def test_external_deletion_and_recreation_invalidate_snapshot(self):
        self.store.create('Snippet', '\\snippet', 'old')
        self.store.list_snippets()
        os.unlink(self.store.index_path)
        self.assertIsNone(self.store.revision[0])
        self.assertEqual(self.store.proposals_for('\\s'), [])
        self.assertEqual(self.store.list_snippets(), [])
        other = UserSnippetStore(self.temporary_directory.name)
        created = other.create('Snippet', '\\snippet', 'new')
        self.assertEqual(self.store.list_snippets(), [created])

    def test_corrupt_library_is_not_cached_and_recovers(self):
        snippet = self.store.create('Snippet', '\\snippet', 'body')
        original = Path(self.store.index_path).read_text(encoding='utf-8')
        for corrupt in ('{not json', '{"version": 2, "snippets": []}', '{"version": 1}'):
            with self.subTest(corrupt=corrupt):
                self.assertEqual(self.store.list_snippets(), [snippet])
                Path(self.store.index_path).write_text(corrupt, encoding='utf-8')
                with self.assertRaises(SnippetStoreError):
                    self.store.list_snippets()
                with self.assertRaises(SnippetStoreError):
                    self.store.create('New', '\\new', 'new')
                with self.assertRaises(SnippetStoreError):
                    self.store.update(snippet.identifier, 'Snippet', '\\snippet', 'new')
                with self.assertRaises(SnippetStoreError):
                    self.store.delete(snippet.identifier)
                with patch('setzer.snippets.user_snippets.json.load', wraps=json.load) as load:
                    self.assertEqual(self.store.proposals_for('\\s'), [])
                    self.assertEqual(self.store.proposals_for('\\s'), [])
                    self.assertEqual(load.call_count, 2)
                Path(self.store.index_path).write_text(original, encoding='utf-8')
                self.assertEqual(self.store.list_snippets(), [snippet])

    def test_transient_read_failure_retries_without_a_fingerprint_change(self):
        snippet = self.store.create('Snippet', '\\snippet', 'body')
        revision = self.store.revision
        with patch('setzer.snippets.user_snippets.json.load', side_effect=OSError('unavailable')):
            with self.assertRaises(SnippetStoreError):
                self.store.list_snippets()
            self.assertEqual(self.store.proposals_for('\\s'), [])
        self.assertEqual(self.store.revision[0], revision[0])
        self.assertNotEqual(self.store.revision, revision)
        self.assertEqual(self.store.list_snippets(), [snippet])

    def test_stat_failure_does_not_return_stale_data_or_cache_empty_results(self):
        snippet = self.store.create('Snippet', '\\snippet', 'body')
        self.store.list_snippets()
        with patch('setzer.snippets.user_snippets.os.stat', side_effect=PermissionError('unavailable')):
            with self.assertRaises(SnippetStoreError):
                _ = self.store.revision
            with self.assertRaises(SnippetStoreError):
                self.store.list_snippets()
            self.assertEqual(self.store.proposals_for('\\s'), [])
        self.assertEqual(self.store.list_snippets(), [snippet])

    def test_replacement_during_read_does_not_cache_mismatched_snapshot(self):
        old = self.store.create('Snippet', '\\snippet', 'old')
        read_index = self.store._read_index
        other = UserSnippetStore(self.temporary_directory.name)

        def read_then_replace():
            snippets = read_index()
            other.update(old.identifier, 'Snippet', '\\snippet', 'new')
            return snippets

        with patch.object(self.store, '_read_index', side_effect=read_then_replace):
            self.assertEqual(self.store.list_snippets(), [old])
        self.assertEqual(self.store.list_snippets()[0].body, 'new')

    def test_failed_write_still_raises_and_keeps_previous_snapshot(self):
        snippet = self.store.create('Snippet', '\\snippet', 'old')
        self.store.list_snippets()
        with patch('setzer.snippets.user_snippets.os.replace', side_effect=OSError('unavailable')):
            with self.assertRaises(SnippetStoreError):
                self.store.update(snippet.identifier, 'Snippet', '\\snippet', 'new')
        self.assertEqual(self.store.list_snippets(), [snippet])
        updated = self.store.update(snippet.identifier, 'Snippet', '\\snippet', 'new')
        self.assertEqual(self.store.list_snippets(), [updated])


class AutocompleteSnippetRevisionTest(unittest.TestCase):
    '''Execute the real update method with AST stubs, without importing GTK.'''

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[2] / 'setzer/document/autocomplete/autocomplete.py'
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        autocomplete = next(node for node in tree.body
                            if isinstance(node, ast.ClassDef) and node.name == 'Autocomplete')
        update = next(node for node in autocomplete.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'update_suggestions')
        cls.update_code = compile(ast.Module(body=[update], type_ignores=[]), str(path), 'exec')

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = UserSnippetStore(self.temporary_directory.name)
        self.latex_item = {'command': '\\static', '_preamble_only': False}
        self.latex_db = SimpleNamespace(
            last_parse_error=None,
            get_items=Mock(return_value=[self.latex_item]),
            get_environment_items=Mock(return_value=[{'command': '\\begin{example}'}]))
        namespace = {'LaTeXDB': self.latex_db, 'SnippetStoreError': SnippetStoreError}
        exec(self.update_code, namespace)
        self.update_suggestions = namespace['update_suggestions']
        cursor = SimpleNamespace(get_line=lambda: 0, get_line_offset=lambda: 2, get_offset=lambda: 2)
        buffer = SimpleNamespace(
            get_insert=lambda: None,
            get_iter_at_mark=lambda mark: cursor,
            get_iter_at_line=lambda line: (True, SimpleNamespace(get_offset=lambda: 0)))
        self.autocomplete = SimpleNamespace(
            is_active=True, source_buffer=buffer,
            document=SimpleNamespace(get_line=lambda line: '\\s'),
            current_word_offset=0, current_word=None, last_tabbed_item=None, context=None,
            snippet_store=self.store, _last_suggestions_key=None,
            _get_preamble_end=lambda: None, items=[], widget=SimpleNamespace(queue_draw=Mock()),
            deactivate=Mock())

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_same_prefix_reuses_items_but_refreshes_after_external_library_changes(self):
        other = UserSnippetStore(self.temporary_directory.name)
        with patch.object(self.store, 'proposals_for', wraps=self.store.proposals_for) as proposals:
            self.update_suggestions(self.autocomplete)
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items, [self.latex_item])
            self.assertEqual(proposals.call_count, 1)
            self.assertEqual(self.latex_db.get_items.call_count, 1)
            empty_key = self.autocomplete._last_suggestions_key

            created = other.create('Snippet', '\\snippet', 'old')
            self.update_suggestions(self.autocomplete)
            self.assertNotEqual(self.autocomplete._last_suggestions_key, empty_key)
            self.assertEqual(self.autocomplete.items[0]['insert_text'], 'old')
            self.assertEqual(self.autocomplete.items[-1], self.latex_item)
            self.update_suggestions(self.autocomplete)
            self.assertEqual(proposals.call_count, 2)

            other.update(created.identifier, 'Snippet', '\\snippet', 'new')
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items[0]['insert_text'], 'new')
            os.unlink(self.store.index_path)
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items, [self.latex_item])
            self.assertEqual(proposals.call_count, 4)

    def test_math_and_begin_contexts_never_stat_or_query_snippet_library(self):
        with patch('setzer.snippets.user_snippets.os.stat',
                   side_effect=AssertionError('unexpected stat')) as stat, \
                patch.object(self.store, 'proposals_for',
                             side_effect=AssertionError('unexpected snippet query')) as proposals:
            for context in ('math', 'begin'):
                with self.subTest(context=context):
                    self.autocomplete.context = context
                    self.update_suggestions(self.autocomplete)
                    self.update_suggestions(self.autocomplete)
            stat.assert_not_called()
            proposals.assert_not_called()
        self.latex_db.get_items.assert_called_once_with('\\s', None, onlymath=True)
        self.latex_db.get_environment_items.assert_called_once_with('\\s')

    def test_same_prefix_recovers_after_corruption(self):
        self.store.create('Snippet', '\\snippet', 'body')
        self.update_suggestions(self.autocomplete)
        original = Path(self.store.index_path).read_text(encoding='utf-8')
        Path(self.store.index_path).write_text('{broken', encoding='utf-8')
        self.update_suggestions(self.autocomplete)
        self.assertEqual(self.autocomplete.items, [self.latex_item])
        Path(self.store.index_path).write_text(original, encoding='utf-8')
        self.update_suggestions(self.autocomplete)
        self.assertEqual(self.autocomplete.items[0]['insert_text'], 'body')

    def test_same_prefix_retries_after_read_failure_with_unchanged_fingerprint(self):
        self.store.create('Snippet', '\\snippet', 'body')
        revision = self.store.revision
        with patch('setzer.snippets.user_snippets.json.load', side_effect=OSError('unavailable')):
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items, [self.latex_item])
        self.assertEqual(self.store.revision[0], revision[0])
        self.update_suggestions(self.autocomplete)
        self.assertEqual(self.autocomplete.items[0]['insert_text'], 'body')
        self.update_suggestions(self.autocomplete)
        self.assertEqual(self.latex_db.get_items.call_count, 2)

    def test_same_prefix_recovers_after_stat_failure(self):
        self.store.create('Snippet', '\\snippet', 'body')
        self.update_suggestions(self.autocomplete)
        with patch('setzer.snippets.user_snippets.os.stat', side_effect=OSError('unavailable')):
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items, [self.latex_item])
            self.update_suggestions(self.autocomplete)
            self.assertEqual(self.autocomplete.items, [self.latex_item])
        self.update_suggestions(self.autocomplete)
        self.assertEqual(self.autocomplete.items[0]['insert_text'], 'body')


if __name__ == '__main__':
    unittest.main()

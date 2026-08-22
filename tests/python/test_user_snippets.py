# coding: utf-8

import json
import os
import tempfile
import unittest

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


if __name__ == '__main__':
    unittest.main()

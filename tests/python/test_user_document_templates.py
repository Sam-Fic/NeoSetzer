#!/usr/bin/env python3
# coding: utf-8

import builtins
import json
import os
import tempfile
import unittest
from unittest import mock

from setzer.dialogs.document_wizard.user_document_templates import (
    INDEX_FILENAME,
    MAX_TEMPLATE_BYTES,
    STORE_DIRECTORY,
    TemplateStoreError,
    UserDocumentTemplateStore,
)


class UserDocumentTemplateStoreTest(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = UserDocumentTemplateStore(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_save_load_list_and_delete_use_a_snapshot(self):
        template = self.store.save('Research article', 'First line\r\nSecond line\rThird line')

        self.assertEqual(template.name, 'Research article')
        self.assertEqual(template.character_count, len('First line\nSecond line\nThird line'))
        self.assertEqual(self.store.load(template.identifier), 'First line\nSecond line\nThird line')
        self.assertEqual(self.store.list_templates(), [template])
        self.assertTrue(os.path.isfile(os.path.join(self.tempdir.name, STORE_DIRECTORY, template.filename)))

        self.assertTrue(self.store.delete(template.identifier))
        self.assertFalse(self.store.delete(template.identifier))
        self.assertEqual(self.store.list_templates(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tempdir.name, STORE_DIRECTORY, template.filename)))

    def test_template_names_are_sorted_case_insensitively(self):
        beta = self.store.save('beta', '\\documentclass{article}')
        alpha = self.store.save('Alpha', '\\documentclass{book}')
        self.assertEqual([template.identifier for template in self.store.list_templates()],
                         [alpha.identifier, beta.identifier])

    def test_save_rejects_invalid_names_and_source(self):
        for name in ('', '  ', '../outside', 'folder/name', 'folder\\name', 'line\nbreak', 'x' * 81):
            with self.subTest(name=name):
                with self.assertRaises(TemplateStoreError):
                    self.store.save(name, '\\documentclass{article}')
        for source in ('', '  \n\t', 'x' * (MAX_TEMPLATE_BYTES + 1)):
            with self.subTest(source_size=len(source)):
                with self.assertRaises(TemplateStoreError):
                    self.store.save('Valid', source)

    def test_template_errors_use_the_late_installed_translator(self):
        with mock.patch.object(builtins, '_', lambda message: 'translated: ' + message,
                               create=True):
            with self.assertRaisesRegex(TemplateStoreError,
                                        '^translated: Template name cannot be empty$'):
                self.store.save('', '\\documentclass{article}')

    def test_duplicate_name_is_rejected_case_insensitively(self):
        self.store.save('Thesis', '\\documentclass{report}')
        with self.assertRaises(TemplateStoreError):
            self.store.save(' thesis ', '\\documentclass{book}')

    def test_load_rejects_missing_source_without_touching_index(self):
        template = self.store.save('Missing later', '\\documentclass{article}')
        os.unlink(os.path.join(self.tempdir.name, STORE_DIRECTORY, template.filename))
        with self.assertRaises(TemplateStoreError):
            self.store.load(template.identifier)
        self.assertEqual([entry.identifier for entry in self.store.list_templates()], [template.identifier])

    def test_bad_index_entries_are_ignored_without_exposing_paths(self):
        directory = os.path.join(self.tempdir.name, STORE_DIRECTORY)
        os.makedirs(directory)
        good = self.store.save('Good', '\\documentclass{article}')
        with open(os.path.join(directory, INDEX_FILENAME), encoding='utf-8') as index_file:
            raw_index = json.load(index_file)
        raw_index['templates'].append({
            'id': 'not-a-uuid',
            'name': '../../outside',
            'filename': '../../outside.tex',
            'created_at': 'now',
            'updated_at': 'now',
            'character_count': 1,
        })
        with open(os.path.join(directory, INDEX_FILENAME), 'w', encoding='utf-8') as index_file:
            json.dump(raw_index, index_file)
        self.assertEqual([entry.identifier for entry in self.store.list_templates()], [good.identifier])

    def test_unreadable_index_raises_user_safe_error(self):
        directory = os.path.join(self.tempdir.name, STORE_DIRECTORY)
        os.makedirs(directory)
        with open(os.path.join(directory, INDEX_FILENAME), 'w', encoding='utf-8') as index_file:
            index_file.write('{broken')
        with self.assertRaises(TemplateStoreError):
            self.store.list_templates()

    def test_index_write_failure_removes_new_source_and_preserves_existing_templates(self):
        existing = self.store.save('Existing', '\\documentclass{article}')
        with mock.patch.object(self.store, '_write_index_atomic', side_effect=TemplateStoreError('simulated')):
            with self.assertRaises(TemplateStoreError):
                self.store.save('New', '\\documentclass{book}')
        self.assertEqual([entry.identifier for entry in self.store.list_templates()], [existing.identifier])
        filenames = os.listdir(os.path.join(self.tempdir.name, STORE_DIRECTORY))
        self.assertEqual(sorted(filenames), sorted([INDEX_FILENAME, existing.filename]))


if __name__ == '__main__':
    unittest.main()

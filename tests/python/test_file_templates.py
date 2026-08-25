#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Tests for user-directory file templates (#279).'''

import os
import stat
import tempfile
import unittest

from setzer.dialogs.document_wizard.file_templates import (
    FileTemplateError,
    MAX_TEMPLATE_BYTES,
    copy_file_template,
    list_file_templates,
)


class TestFileTemplates(unittest.TestCase):

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.templates_dir = os.path.join(self.tempdir.name, 'Templates')
        self.destination_dir = os.path.join(self.tempdir.name, 'documents')
        os.mkdir(self.templates_dir)
        os.mkdir(self.destination_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_template(self, name, content=b''):
        path = os.path.join(self.templates_dir, name)
        with open(path, 'wb') as output_file:
            output_file.write(content)
        return path

    def test_lists_only_top_level_regular_tex_files_in_sorted_order(self):
        self.write_template('Zebra.tex')
        self.write_template('alpha.TEX')
        self.write_template('.hidden.tex')
        self.write_template('notes.txt')
        os.mkdir(os.path.join(self.templates_dir, 'nested'))
        with open(os.path.join(self.templates_dir, 'nested', 'nested.tex'), 'wb') as output_file:
            output_file.write(b'nested')

        templates = list_file_templates(self.templates_dir)

        self.assertEqual([template.name for template in templates], ['alpha.TEX', 'Zebra.tex'])
        self.assertEqual(
            [template.path for template in templates],
            [
                os.path.join(self.templates_dir, 'alpha.TEX'),
                os.path.join(self.templates_dir, 'Zebra.tex'),
            ],
        )

    def test_ignores_oversized_files_and_missing_directory(self):
        too_large = self.write_template('large.tex')
        with open(too_large, 'r+b') as output_file:
            output_file.truncate(MAX_TEMPLATE_BYTES + 1)

        self.assertEqual(list_file_templates(self.templates_dir), [])
        self.assertEqual(list_file_templates(os.path.join(self.tempdir.name, 'missing')), [])

    def test_copy_preserves_template_bytes_and_creates_destination(self):
        source = self.write_template('article.tex', b'\\documentclass{article}\r\n\xff')
        os.chmod(source, 0o640)
        destination = os.path.join(self.destination_dir, 'paper.tex')

        created = copy_file_template(source, destination)

        self.assertEqual(created, destination)
        with open(destination, 'rb') as created_file:
            self.assertEqual(created_file.read(), b'\\documentclass{article}\r\n\xff')
        self.assertEqual(stat.S_IMODE(os.stat(destination).st_mode), 0o640)
        self.assertFalse(os.path.exists(destination + '.tmp'))

    def test_copy_refuses_existing_destination(self):
        source = self.write_template('article.tex', b'source')
        destination = os.path.join(self.destination_dir, 'paper.tex')
        with open(destination, 'wb') as existing_file:
            existing_file.write(b'existing')

        with self.assertRaises(FileTemplateError):
            copy_file_template(source, destination)

        with open(destination, 'rb') as existing_file:
            self.assertEqual(existing_file.read(), b'existing')

    def test_copy_requires_tex_source_and_destination(self):
        source = self.write_template('article.tex', b'source')

        with self.assertRaises(FileTemplateError):
            copy_file_template(source, os.path.join(self.destination_dir, 'paper.txt'))
        with self.assertRaises(FileTemplateError):
            copy_file_template(os.path.join(self.templates_dir, 'missing.tex'),
                               os.path.join(self.destination_dir, 'paper.tex'))


if __name__ == '__main__':
    unittest.main()

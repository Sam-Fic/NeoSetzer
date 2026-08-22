#!/usr/bin/env python3
# coding: utf-8

import os
import tempfile
import unittest
from pathlib import Path

from setzer.document.bibtex.file_session import (
    BibTeXExternalChangeError,
    BibTeXFileSession,
)


class BibTeXFileSessionTest(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / 'references.bib'
        self.original = '@article{one,\n  title = {Original}\n}\n'
        self.path.write_text(self.original, encoding='utf-8')

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_reads_utf8_text_and_writes_atomically(self):
        session = BibTeXFileSession(str(self.path))
        self.assertEqual(session.text, self.original)
        updated = '@article{one,\n  title = {Updated Å}\n}\n'
        session.write_text(updated)
        self.assertEqual(self.path.read_text(encoding='utf-8'), updated)
        self.assertEqual(session.text, updated)
        self.assertFalse(any(path.name.startswith('.references.bib.') for path in self.path.parent.iterdir()))

    def test_rejects_external_modification_without_overwriting_file(self):
        session = BibTeXFileSession(str(self.path))
        externally_changed = '@book{outside, title = {External}}\n'
        self.path.write_text(externally_changed, encoding='utf-8')
        with self.assertRaises(BibTeXExternalChangeError):
            session.write_text('@article{one, title = {Unsafe overwrite}}\n')
        self.assertEqual(self.path.read_text(encoding='utf-8'), externally_changed)

    def test_reload_accepts_external_change_and_refreshes_fingerprint(self):
        session = BibTeXFileSession(str(self.path))
        externally_changed = '@book{outside, title = {External}}\n'
        self.path.write_text(externally_changed, encoding='utf-8')
        self.assertEqual(session.reload(), externally_changed)
        session.write_text('@book{outside, title = {Saved after reload}}\n')
        self.assertIn('Saved after reload', self.path.read_text(encoding='utf-8'))

    def test_requires_existing_regular_bib_file(self):
        missing = Path(self.temporary_directory.name) / 'missing.bib'
        with self.assertRaises(FileNotFoundError):
            BibTeXFileSession(str(missing))
        directory = Path(self.temporary_directory.name) / 'directory.bib'
        directory.mkdir()
        with self.assertRaises(IsADirectoryError):
            BibTeXFileSession(str(directory))

    def test_failed_replace_keeps_existing_contents(self):
        session = BibTeXFileSession(str(self.path))
        original_replace = os.replace
        try:
            os.replace = lambda source, destination: (_ for _ in ()).throw(OSError('replace failed'))
            with self.assertRaises(OSError):
                session.write_text('@article{one, title = {Failed}}\n')
        finally:
            os.replace = original_replace
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.original)
        self.assertFalse(any(path.name.startswith('.references.bib.') for path in self.path.parent.iterdir()))


if __name__ == '__main__':
    unittest.main()

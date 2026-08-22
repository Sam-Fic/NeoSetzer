#!/usr/bin/env python3
# coding: utf-8

import os
import pathlib
import tempfile
import unittest

from setzer.document.magic_comments import parse_magic_comments, resolve_root_filename
from setzer.example_project.project_store import (
    DEFAULT_PROJECT_NAME,
    ExampleProjectError,
    ExampleProjectStore,
    MAIN_DOCUMENT_FILENAME,
)


class ExampleProjectStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary_directory.name)
        self.source = self.root / 'source'
        self.destination = self.root / 'destination'
        self.source.mkdir()
        (self.source / MAIN_DOCUMENT_FILENAME).write_text(
            '\\documentclass{article}\n\\begin{document}Example\\end{document}\n',
            encoding='utf-8',
        )
        (self.source / 'chapters').mkdir()
        (self.source / 'chapters' / 'intro.tex').write_text(
            'Example chapter\n', encoding='utf-8')

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_create_copies_nested_project_into_unique_writable_directory(self):
        store = ExampleProjectStore(str(self.source), str(self.destination))

        main_document = pathlib.Path(store.create())

        self.assertEqual(main_document.name, MAIN_DOCUMENT_FILENAME)
        self.assertEqual(main_document.parent.name, DEFAULT_PROJECT_NAME)
        self.assertEqual(
            main_document.read_text(encoding='utf-8'),
            (self.source / MAIN_DOCUMENT_FILENAME).read_text(encoding='utf-8'),
        )
        self.assertEqual(
            (main_document.parent / 'chapters' / 'intro.tex').read_text(encoding='utf-8'),
            'Example chapter\n',
        )
        self.assertTrue(os.access(main_document.parent, os.W_OK))

    def test_create_restores_owner_write_permission_for_read_only_resources(self):
        (self.source / MAIN_DOCUMENT_FILENAME).chmod(0o444)
        store = ExampleProjectStore(str(self.source), str(self.destination))

        main_document = pathlib.Path(store.create())

        self.assertTrue(os.access(main_document, os.W_OK))
        main_document.write_text('Editable user copy\\n', encoding='utf-8')
        self.assertEqual(main_document.read_text(encoding='utf-8'), 'Editable user copy\\n')

    def test_create_never_overwrites_an_existing_example_directory(self):
        store = ExampleProjectStore(str(self.source), str(self.destination))

        first_main = pathlib.Path(store.create())
        first_main.write_text('User changes\n', encoding='utf-8')
        second_main = pathlib.Path(store.create())

        self.assertEqual(first_main.parent.name, DEFAULT_PROJECT_NAME)
        self.assertEqual(second_main.parent.name, DEFAULT_PROJECT_NAME + ' 2')
        self.assertEqual(first_main.read_text(encoding='utf-8'), 'User changes\n')

    def test_missing_main_document_is_rejected_without_creating_destination(self):
        (self.source / MAIN_DOCUMENT_FILENAME).unlink()
        store = ExampleProjectStore(str(self.source), str(self.destination))

        with self.assertRaises(ExampleProjectError):
            store.create()

        self.assertFalse(self.destination.exists())

    def test_symlinked_source_entries_are_rejected(self):
        external = self.root / 'external.tex'
        external.write_text('External\n', encoding='utf-8')
        os.symlink(external, self.source / 'linked.tex')
        store = ExampleProjectStore(str(self.source), str(self.destination))

        with self.assertRaises(ExampleProjectError):
            store.create()

        self.assertFalse(self.destination.exists())

    def test_bundled_example_project_is_copyable_and_uses_pdf_latex_baseline(self):
        repository_root = pathlib.Path(__file__).resolve().parents[2]
        bundled_source = repository_root / 'data' / 'resources' / 'example_project'
        destination = self.root / 'bundled-copy'

        main_document = pathlib.Path(
            ExampleProjectStore(str(bundled_source), str(destination)).create())

        source = main_document.read_text(encoding='utf-8')
        self.assertIn('\\documentclass', source)
        self.assertNotIn('\\usepackage{fontspec}', source)
        self.assertTrue((main_document.parent / 'README.md').is_file())
        self.assertEqual(main_document.parent.name, DEFAULT_PROJECT_NAME)

    def test_bundled_project_includes_core_multifile_learning_resources(self):
        repository_root = pathlib.Path(__file__).resolve().parents[2]
        project_root = repository_root / 'data' / 'resources' / 'example_project'
        main_source = (project_root / MAIN_DOCUMENT_FILENAME).read_text(encoding='utf-8')

        self.assertEqual(parse_magic_comments(main_source).program, 'pdflatex')
        self.assertIn('\\input{chapters/01-getting-started}', main_source)
        self.assertIn('\\input{chapters/appendix-structure}', main_source)
        self.assertIn('\\bibliography{references}', main_source)
        self.assertIn('\\appendix', main_source)
        self.assertTrue((project_root / 'data' / 'example-table.csv').is_file())
        self.assertTrue((project_root / 'references.bib').is_file())

        for chapter in (
                '01-getting-started.tex', '02-writing-and-navigation.tex',
                '03-project-workflows.tex', '04-tables-and-data.tex',
                'appendix-structure.tex'):
            chapter_path = project_root / 'chapters' / chapter
            source = chapter_path.read_text(encoding='utf-8')
            self.assertEqual(parse_magic_comments(source).root, '../main.tex')
            self.assertEqual(
                resolve_root_filename(str(chapter_path), '../main.tex'),
                str(project_root / MAIN_DOCUMENT_FILENAME),
            )

        appendix_source = (project_root / 'chapters' / 'appendix-structure.tex').read_text(
            encoding='utf-8')
        self.assertIn('\\setcounter{subsection}{2}', appendix_source)
        navigation_source = (project_root / 'chapters' / '02-writing-and-navigation.tex').read_text(
            encoding='utf-8')
        self.assertIn('\\todo{', navigation_source)
        self.assertIn('\\subsection{A \\textit{nested} heading title}', navigation_source)

    def test_copy_failure_removes_reserved_partial_directory(self):
        store = ExampleProjectStore(str(self.source), str(self.destination))
        original_copy = store._copy_source_to

        def fail_after_creating_file(project_directory):
            pathlib.Path(project_directory, 'partial.txt').write_text('partial', encoding='utf-8')
            raise OSError('simulated copy error')

        store._copy_source_to = fail_after_creating_file
        try:
            with self.assertRaises(ExampleProjectError):
                store.create()
        finally:
            store._copy_source_to = original_copy

        self.assertFalse((self.destination / DEFAULT_PROJECT_NAME).exists())


if __name__ == '__main__':
    unittest.main()

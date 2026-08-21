#!/usr/bin/env python3
# coding: utf-8

import os
import tempfile
import unittest

from setzer.document.magic_comments import (
    MAX_MAGIC_COMMENT_LINES,
    MagicComments,
    parse_magic_comments,
    resolve_root_filename,
)


class MagicCommentsTest(unittest.TestCase):

    def test_parses_texworks_program_and_root_directives(self):
        comments = parse_magic_comments(
            '% !TEX program = lualatex\n'
            '% !TeX root = ../main.tex\n'
            '\\section{Child}\n'
        )
        self.assertEqual(comments.program, 'lualatex')
        self.assertEqual(comments.root, '../main.tex')

    def test_accepts_case_separator_and_quoted_values(self):
        comments = parse_magic_comments(
            '% !tex PROGRAM: "XeLaTeX"\n'
            "% !TEX ROOT: 'main.tex'\n"
        )
        self.assertEqual(comments, MagicComments(program='xelatex', root='main.tex'))

    def test_first_valid_directive_wins_and_unsupported_program_is_ignored(self):
        comments = parse_magic_comments(
            '% !TEX program = not-a-command\n'
            '% !TEX program = pdflatex\n'
            '% !TEX program = lualatex\n'
        )
        self.assertEqual(comments.program, 'pdflatex')

    def test_ignores_non_comment_and_late_directives(self):
        text = '\\def\\value{program = lualatex}\n' + ('% ordinary comment\n' * MAX_MAGIC_COMMENT_LINES)
        text += '% !TEX program = lualatex\n'
        self.assertIsNone(parse_magic_comments(text).program)

    def test_ignores_blank_and_unrelated_directives(self):
        comments = parse_magic_comments(
            '% !TEX encoding = UTF-8\n'
            '% !TEX spellcheck = en-US\n'
            '% !TEX program = \n'
        )
        self.assertEqual(comments, MagicComments())

    def test_resolves_relative_existing_root_tex_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = os.path.join(directory, 'main.tex')
            chapter_dir = os.path.join(directory, 'chapters')
            os.mkdir(chapter_dir)
            child = os.path.join(chapter_dir, 'one.tex')
            for filename in (root, child):
                with open(filename, 'w', encoding='utf-8') as handle:
                    handle.write('% test\n')
            self.assertEqual(resolve_root_filename(child, '../main.tex'), root)

    def test_rejects_absolute_non_tex_and_missing_root_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            child = os.path.join(directory, 'child.tex')
            with open(child, 'w', encoding='utf-8') as handle:
                handle.write('% test\n')
            self.assertIsNone(resolve_root_filename(child, '/tmp/main.tex'))
            self.assertIsNone(resolve_root_filename(child, 'main.pdf'))
            self.assertIsNone(resolve_root_filename(child, 'missing.tex'))


if __name__ == '__main__':
    unittest.main()

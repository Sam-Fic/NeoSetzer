#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# #366 回归测试：主 TeX 文档应识别 KOMA Letter Option（.lco/.loc）及
# 本地 document class（.cls），并仅把实际位于项目目录中的配置文件加入侧栏。

import ast
import os
import re
import tempfile
import types
import unittest

from setzer.document.parser.beamer_frames import extract_beamer_frame_titles
from setzer.document.parser.structure_numbering import (
    SectioningCommand,
    SecnumDepthChange,
    calculate_structure_numbers,
)


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class _Observable:

    def __init__(self):
        pass

    def add_change_code(self, *args, **kwargs):
        pass


class _RegexServiceLocator:

    @staticmethod
    def get_regex_object(pattern):
        return re.compile(pattern)


def _extract_class(path, class_name, namespace):
    tree = ast.parse(open(path, encoding='utf-8').read())
    constants = [node for node in tree.body if isinstance(node, ast.Assign)]
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    exec(compile(ast.Module(body=constants + [cls_node], type_ignores=[]),
                 path, 'exec'), namespace)
    return namespace[class_name]


def _load_parser_class():
    path = os.path.join(REPO, 'setzer/document/parser/parser_latex.py')
    return _extract_class(path, 'ParserLaTeX', {
        'Observable': _Observable,
        'ServiceLocator': _RegexServiceLocator,
        'GLib': types.SimpleNamespace(timeout_add=lambda *args: 1,
                                      source_remove=lambda *args: None),
        'extract_beamer_frame_titles': extract_beamer_frame_titles,
        'SectioningCommand': SectioningCommand,
        'SecnumDepthChange': SecnumDepthChange,
        'calculate_structure_numbers': calculate_structure_numbers,
    })


def _load_data_provider_class():
    path = os.path.join(
        REPO, 'setzer/workspace/sidebar/document_structure_page/data_provider.py')
    path_helpers = types.SimpleNamespace(
        get_abspath=lambda filename, dirname: os.path.abspath(
            os.path.join(dirname, filename)))
    return _extract_class(path, 'DataProvider', {
        'Observable': _Observable,
        'GLib': types.SimpleNamespace(idle_add=lambda *args: 1),
        'os': os,
        'path_helpers': path_helpers,
    })


ParserLaTeX = _load_parser_class()
DataProvider = _load_data_provider_class()


class _Buffer:

    def connect(self, *args):
        pass


class _ParserDocument:

    def __init__(self):
        self.source_buffer = _Buffer()


class _RootDocument:

    def __init__(self, dirname, symbols):
        self._dirname = dirname
        self.parser = types.SimpleNamespace(symbols=symbols)

    def get_is_root(self):
        return True

    def get_dirname(self):
        return self._dirname


class _Workspace:

    @staticmethod
    def get_document_by_filename(path):
        return None


class TestProjectFileDependencyParsing(unittest.TestCase):

    def test_letter_options_and_document_classes_are_collected(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\documentclass[paper=a4]{custom-letter}\n'
            '\\LoadLetterOption{company-letterhead}\n'
            '\\LoadLetterOption{international.loc}\n'
            '\\input{body}\n'
        )
        parser.initial_parse(text)

        self.assertEqual(
            parser.symbols['included_project_files'],
            [
                ('custom-letter.cls', 0),
                ('company-letterhead.lco', text.index('\\LoadLetterOption{company-letterhead}')),
                ('international.loc', text.index('\\LoadLetterOption{international.loc}')),
            ],
        )
        self.assertEqual(parser.symbols['included_latex_files'], [('body.tex', text.index('\\input{body}'))])

    def test_system_document_class_is_still_parsed_as_candidate(self):
        parser = ParserLaTeX(_ParserDocument())
        parser.initial_parse('\\documentclass{scrlttr2}\n')
        self.assertEqual(parser.symbols['included_project_files'], [('scrlttr2.cls', 0)])

    def test_section_blocks_keep_titles_and_store_number_metadata(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\section{One}\n'
            '\\subsection{One One}\n'
            '\\section*{Unnumbered}\n'
            '\\setcounter{secnumdepth}{1}\n'
            '\\subsection{Hidden}\n'
            '\\section{Two}\n'
        )
        parser.initial_parse(text)

        self.assertEqual(
            parser.symbols['block_metadata'],
            {
                text.index('\\section{One}'): {'number': '1', 'starred': False},
                text.index('\\subsection{One One}'): {'number': '1.1', 'starred': False},
                text.index('\\section*{Unnumbered}'): {'number': None, 'starred': True},
                text.index('\\subsection{Hidden}'): {'number': None, 'starred': False},
                text.index('\\section{Two}'): {'number': '2', 'starred': False},
            },
        )
        sections = [block for block in parser.symbols['blocks'] if block[4] in ('section', 'subsection')]
        self.assertEqual([block[5] for block in sections],
                         ['One', 'One One', 'Unnumbered', 'Hidden', 'Two'])

    def test_titled_beamer_frames_become_structure_blocks(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\begin{document}\n'
            '\\section{Demo}\n'
            '\\begin{frame}{Overview}\n'
            'Content\n'
            '\\end{frame}\n'
            '\\begin{frame}\n'
            '\\frametitle{Method}\n'
            '\\end{frame}\n'
            '\\begin{frame}\n'
            'Untitled content\n'
            '\\end{frame}\n'
            '\\end{document}\n'
        )
        parser.initial_parse(text)
        frames = [block for block in parser.symbols['blocks'] if block[4] == 'frame']
        self.assertEqual(
            [(block[0], block[2], block[5]) for block in frames if len(block) > 5],
            [
                (text.index('\\begin{frame}{Overview}'), 2, 'Overview'),
                (text.index('\\begin{frame}\n\\frametitle'), 5, 'Method'),
            ],
        )
        self.assertEqual(len(frames), 3)


class TestProjectFileSidebarFiltering(unittest.TestCase):

    def test_only_existing_project_configurations_are_added_to_sidebar(self):
        with tempfile.TemporaryDirectory() as project_dir:
            open(os.path.join(project_dir, 'company-letterhead.lco'), 'w', encoding='utf-8').close()
            open(os.path.join(project_dir, 'custom-letter.cls'), 'w', encoding='utf-8').close()
            symbols = {
                'included_latex_files': [('chapter.tex', 30)],
                'included_project_files': [
                    ('scrlttr2.cls', 0),
                    ('company-letterhead.lco', 10),
                    ('custom-letter.cls', 20),
                    ('not-a-local-option.loc', 25),
                ],
            }
            provider = DataProvider.__new__(DataProvider)
            provider.document = _RootDocument(project_dir, symbols)
            provider.workspace = _Workspace()
            provider.integrated_includes = {}
            provider._includes_cache = []
            provider.on_buffer_changed = lambda *args: None

            provider.update_integrated_includes()

            included_names = [os.path.basename(item['filename'])
                              for item in provider.get_includes()]
            self.assertEqual(
                included_names,
                ['chapter.tex', 'company-letterhead.lco', 'custom-letter.cls'],
            )


if __name__ == '__main__':
    unittest.main()

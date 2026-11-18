#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# #366 回归测试：主 TeX 文档应识别 KOMA Letter Option（.lco/.loc）及
# 本地 document class（.cls），并仅把实际位于项目目录中的配置文件加入侧栏。

import ast
import importlib
import os
import re
import sys
import tempfile
import types
import unittest

# math preview 是并发开发中的特性：parser 引用 find_math_regions 时注入
# 真实实现；模块不存在（如只检出不含该特性的提交）则跳过注入。
try:
    from setzer.document.snippet_preview.math_region_finder import find_math_regions
except ImportError:
    find_math_regions = None
from setzer.document.parser.beamer_frames import extract_beamer_frame_titles
from setzer.document.parser.latex_braces import scan_balanced_braced_argument
from setzer.document.parser.structure_numbering import (
    AppendixStart,
    CounterChange,
    SectioningCommand,
    SecnumDepthChange,
    calculate_structure_numbers,
)


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

from setzer.project.files import scan_document_dependencies


class _Observable:

    def __init__(self):
        pass

    def add_change_code(self, *args, **kwargs):
        pass


class _RegexServiceLocator:

    @staticmethod
    def get_regex_object(pattern):
        return re.compile(pattern)


def _inject_stdlib_imports(tree, namespace):
    # Execute top-level imports of standard-library modules so the extracted
    # class can freely use them (e.g. ``bisect``) without this harness having
    # to inject each one by hand.  Non-stdlib imports (gi.repository,
    # setzer.*, ...) are expected to be provided as stubs in `namespace` and
    # are skipped here.
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in sys.stdlib_module_names:
                    continue
                name = alias.asname or alias.name.split('.')[0]
                if name in namespace:
                    continue
                namespace[name] = importlib.import_module(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split('.')[0] not in sys.stdlib_module_names:
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                if alias.name == '*':
                    namespace.update({key: value for key, value in vars(module).items()
                                      if not key.startswith('_') and key not in namespace})
                elif alias.asname or alias.name not in namespace:
                    namespace[alias.asname or alias.name] = getattr(module, alias.name)


def _extract_class(path, class_name, namespace):
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    constants = [node for node in tree.body if isinstance(node, ast.Assign)]
    _inject_stdlib_imports(tree, namespace)
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    exec(compile(ast.Module(body=constants + [cls_node], type_ignores=[]),
                 path, 'exec'), namespace)
    return namespace[class_name]


def _load_parser_class():
    namespace = {
        'Observable': _Observable,
        'ServiceLocator': _RegexServiceLocator,
        'GLib': types.SimpleNamespace(timeout_add=lambda *args: 1,
                                      source_remove=lambda *args: None),
        'extract_beamer_frame_titles': extract_beamer_frame_titles,
        'scan_balanced_braced_argument': scan_balanced_braced_argument,
        'AppendixStart': AppendixStart,
        'CounterChange': CounterChange,
        'SectioningCommand': SectioningCommand,
        'SecnumDepthChange': SecnumDepthChange,
        'calculate_structure_numbers': calculate_structure_numbers,
    }
    if find_math_regions is not None:
        namespace['find_math_regions'] = find_math_regions
    path = os.path.join(REPO, 'setzer/document/parser/parser_latex.py')
    return _extract_class(path, 'ParserLaTeX', namespace)


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
        'scan_document_dependencies': scan_document_dependencies,
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

    def __init__(self, dirname, symbols, filename=None):
        self._dirname = dirname
        self._filename = filename
        self.parser = types.SimpleNamespace(symbols=symbols)

    def get_is_root(self):
        return True

    def get_dirname(self):
        return self._dirname

    def get_filename(self):
        return self._filename


class _Workspace:

    @staticmethod
    def get_document_by_filename(path):
        return None

    @staticmethod
    def resolve_project_root_filename(document):
        return None


class _RootResolvingWorkspace(_Workspace):
    '''模拟 magic root / build.json root 解析：root_filename 可指向已打开
    （documents 提供）或未打开（读盘离线扫描）的主文档。'''

    def __init__(self, root_filename, documents=None):
        self.root_filename = root_filename
        self.documents = documents or {}

    def resolve_project_root_filename(self, document):
        return self.root_filename

    def get_document_by_filename(self, path):
        return self.documents.get(path)


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

    def test_nested_section_titles_are_kept_without_creating_inner_sections(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\section{The \\textit{quick settings} menu}\n'
            '\\section{A \\href{https://example.test/{path}}{linked title}}\n'
            '\\section{Outer \\texttt{\\section{not a heading}} title}\n'
        )
        parser.initial_parse(text)

        sections = [block for block in parser.symbols['blocks'] if block[4] == 'section']
        self.assertEqual(
            [block[5] for block in sections],
            [
                'The \\textit{quick settings} menu',
                'A \\href{https://example.test/{path}}{linked title}',
                'Outer \\texttt{\\section{not a heading}} title',
            ],
        )
        self.assertEqual(
            parser.symbols['block_metadata'],
            {
                text.index('\\section{The'): {'number': '1', 'starred': False},
                text.index('\\section{A'): {'number': '2', 'starred': False},
                text.index('\\section{Outer'): {'number': '3', 'starred': False},
            },
        )

    def test_unclosed_nested_section_title_is_ignored_until_edit_is_complete(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\section{Visible}\n'
            '\\section{Still editing \\textit{nested title} \\section{not real}\n'
        )
        parser.initial_parse(text)

        sections = [block for block in parser.symbols['blocks'] if block[4] == 'section']
        self.assertEqual([block[5] for block in sections], ['Visible'])
        self.assertEqual(
            parser.symbols['block_metadata'],
            {text.index('\\section{Visible}'): {'number': '1', 'starred': False}},
        )

    def test_article_appendix_uses_sections_as_the_alphabetic_root(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\documentclass{article}\n'
            '\\section{Body}\n'
            '\\appendix\n'
            '\\section{Supplement}\n'
            '\\subsection{Details}\n'
        )
        parser.initial_parse(text)

        self.assertEqual(
            parser.symbols['block_metadata'],
            {
                text.index('\\section{Body}'): {'number': '1', 'starred': False},
                text.index('\\section{Supplement}'): {'number': 'A', 'starred': False},
                text.index('\\subsection{Details}'): {'number': 'A.1', 'starred': False},
            },
        )

    def test_appendix_and_literal_counter_changes_flow_into_block_metadata(self):
        parser = ParserLaTeX(_ParserDocument())
        text = (
            '\\documentclass{book}\n'
            '\\chapter{Body}\n'
            '\\section{Introduction}\n'
            '\\appendix\n'
            '\\setcounter{chapter}{2}\n'
            '\\chapter{Supplement}\n'
            '\\addtocounter{section}{3}\n'
            '\\section{Data}\n'
        )
        parser.initial_parse(text)

        self.assertEqual(
            parser.symbols['block_metadata'],
            {
                text.index('\\chapter{Body}'): {'number': '1', 'starred': False},
                text.index('\\section{Introduction}'): {'number': '1.1', 'starred': False},
                text.index('\\chapter{Supplement}'): {'number': 'C', 'starred': False},
                text.index('\\section{Data}'): {'number': 'C.4', 'starred': False},
            },
        )

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


class _NonRootDocument(_RootDocument):

    def get_is_root(self):
        return False


class TestFilesSectionWithoutRoot(unittest.TestCase):
    '''Files 列表不再要求显式设 root；处于子文件时列表应来自 magic root
    主文档（已打开用其增量 parser，未打开读盘离线扫描），而非子文件自身。'''

    def _make_provider(self, document, workspace):
        provider = DataProvider.__new__(DataProvider)
        provider.document = document
        provider.workspace = workspace
        provider.integrated_includes = {}
        provider._includes_cache = []
        provider._files_cache = {'main': None, 'includes': [], 'bibliographies': []}
        provider._offline_files_cache = None
        return provider

    def test_includes_are_built_without_explicit_root(self):
        provider = self._make_provider(
            _NonRootDocument(
                '/tmp/irrelevant', {'included_latex_files': [('chapter.tex', 10)]},
                filename='/tmp/irrelevant/main.tex'),
            _Workspace())

        provider.update_integrated_includes()

        # 常规 include 文件保留占位（不要求磁盘存在），与设 root 时一致。
        self.assertEqual(
            [os.path.basename(item['filename']) for item in provider.get_includes()],
            ['chapter.tex'],
        )

    def test_files_cache_without_root_falls_back_to_active_document(self):
        document = _NonRootDocument(
            '/tmp/irrelevant', {'included_latex_files': [], 'bibliographies': set()},
            filename='/tmp/irrelevant/main.tex')
        provider = self._make_provider(document, _Workspace())

        provider.update_files_cache()

        # 无 magic root：主文档 = 活跃文档自身。
        self.assertEqual(provider.get_files_main(), {'filename': '/tmp/irrelevant/main.tex', 'document': document})
        self.assertEqual(provider.get_files_includes(), [])

    def test_only_existing_bibliographies_are_added_sorted(self):
        with tempfile.TemporaryDirectory() as project_dir:
            for name in ('refs-b.bib', 'refs-a.bib'):
                open(os.path.join(project_dir, name), 'w', encoding='utf-8').close()
            document = _NonRootDocument(
                project_dir,
                {'included_latex_files': [], 'bibliographies': {'refs-b.bib', 'refs-a.bib', 'missing.bib'}},
                filename=os.path.join(project_dir, 'main.tex'))
            provider = self._make_provider(document, _Workspace())

            provider.update_files_cache()

            # set 无序且缺失文件不收录：按文件名排序保证展示稳定。
            self.assertEqual(
                [item['filename'] for item in provider.get_bibliographies()],
                [
                    os.path.join(project_dir, 'refs-a.bib'),
                    os.path.join(project_dir, 'refs-b.bib'),
                ],
            )
            self.assertTrue(
                all(item['document'] is None and item['offset'] is None
                    for item in provider.get_bibliographies()),
            )

    def test_child_file_with_open_main_shows_main_list(self):
        with tempfile.TemporaryDirectory() as project_dir:
            chapters_dir = os.path.join(project_dir, 'chapters')
            os.makedirs(chapters_dir)
            open(os.path.join(chapters_dir, '01.tex'), 'w', encoding='utf-8').close()
            open(os.path.join(project_dir, 'refs.bib'), 'w', encoding='utf-8').close()
            main_filename = os.path.join(project_dir, 'main.tex')
            main_document = _RootDocument(
                project_dir,
                {'included_latex_files': [('chapters/01.tex', 30)], 'bibliographies': {'refs.bib'}},
                filename=main_filename)
            child = _NonRootDocument(
                chapters_dir, {'included_latex_files': []},
                filename=os.path.join(chapters_dir, '01.tex'))
            provider = self._make_provider(
                child, _RootResolvingWorkspace(main_filename, documents={main_filename: main_document}))

            provider.update_files_cache()

            # 处于子文件时：主文档行 = magic root 主文档（已打开），列表来自主文档符号。
            self.assertEqual(provider.get_files_main(), {'filename': main_filename, 'document': main_document})
            self.assertEqual(
                [os.path.basename(item['filename']) for item in provider.get_files_includes()],
                ['01.tex'],
            )
            self.assertEqual(
                [os.path.basename(item['filename']) for item in provider.get_bibliographies()],
                ['refs.bib'],
            )

    def test_child_file_with_closed_main_uses_offline_scan(self):
        with tempfile.TemporaryDirectory() as project_dir:
            chapters_dir = os.path.join(project_dir, 'chapters')
            os.makedirs(chapters_dir)
            open(os.path.join(chapters_dir, '01.tex'), 'w', encoding='utf-8').close()
            open(os.path.join(project_dir, 'refs.bib'), 'w', encoding='utf-8').close()
            main_filename = os.path.join(project_dir, 'main.tex')
            with open(main_filename, 'w', encoding='utf-8') as file:
                file.write('\\documentclass{article}\n\\input{chapters/01.tex}\n\\bibliography{refs}\n')
            child = _NonRootDocument(
                chapters_dir, {'included_latex_files': []},
                filename=os.path.join(chapters_dir, '01.tex'))
            provider = self._make_provider(child, _RootResolvingWorkspace(main_filename))

            provider.update_files_cache()

            # 主文档未打开：读盘离线扫描。系统 documentclass（article.cls）不在
            # 本地，按 must-be-local 语义被过滤。
            self.assertIsNone(provider.get_files_main()['document'])
            self.assertEqual(provider.get_files_main()['filename'], main_filename)
            self.assertEqual(
                [os.path.basename(item['filename']) for item in provider.get_files_includes()],
                ['01.tex'],
            )
            self.assertEqual(
                [os.path.basename(item['filename']) for item in provider.get_bibliographies()],
                ['refs.bib'],
            )

    def test_magic_root_pointing_to_active_document_itself_falls_back(self):
        document = _NonRootDocument(
            '/tmp/irrelevant', {'included_latex_files': []},
            filename='/tmp/irrelevant/main.tex')
        provider = self._make_provider(document, _RootResolvingWorkspace('/tmp/irrelevant/main.tex'))

        provider.update_files_cache()

        # magic root 指向自身：主文档 = 活跃文档，不产生自引用条目。
        self.assertEqual(provider.get_files_main()['document'], document)


if __name__ == '__main__':
    unittest.main()

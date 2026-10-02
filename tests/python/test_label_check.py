#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# 未使用 / 重复 \label 检查的测试：纯逻辑分析（label_check）、parser 的
# \ref 族提取（含注释过滤）以及 problem-center 问题展开。
#
# ParserLaTeX 依赖 gi，headless 环境不可用；沿用 test_project_file_dependencies
# 的做法：AST 抽取类定义 + 桩命名空间执行，全程不 import gi。

import ast
import importlib
import os
import re
import sys
import types
import unittest

from setzer.document.label_check import analyze_labels, iter_label_problems
from setzer.project.problem_center import ProjectProblem

# math preview 是并发开发中的特性：其解析调用出现在 parser 里时注入真实
# 实现（math_region_finder 本身 gi-free）；模块不存在（如只检出本提交）
# 则跳过注入，此时 parser 也不引用该名字。
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


class _Observable:

    def __init__(self):
        pass

    def add_change_code(self, *args, **kwargs):
        pass


class _RegexServiceLocator:

    @staticmethod
    def get_regex_object(pattern):
        return re.compile(pattern)


def _load_parser_class():
    path = os.path.join(REPO, 'setzer/document/parser/parser_latex.py')
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    constants = [node for node in tree.body if isinstance(node, ast.Assign)]
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'ParserLaTeX')
    # 顶层标准库 import（bisect 等）自动注入；非标准库依赖以桩提供。
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
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in sys.stdlib_module_names:
                    continue
                name = alias.asname or alias.name.split('.')[0]
                if name not in namespace:
                    namespace[name] = importlib.import_module(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split('.')[0] not in sys.stdlib_module_names:
                continue
            module = importlib.import_module(node.module)
            for alias in node.names:
                if alias.asname or alias.name not in namespace:
                    namespace[alias.asname or alias.name] = getattr(module, alias.name)
    if find_math_regions is not None:
        namespace['find_math_regions'] = find_math_regions
    exec(compile(ast.Module(body=constants + [cls_node], type_ignores=[]),
                 path, 'exec'), namespace)
    return namespace['ParserLaTeX']


ParserLaTeX = _load_parser_class()


class _Buffer:

    def connect(self, *args):
        pass


class _ParserDocument:

    def __init__(self):
        self.source_buffer = _Buffer()


class _StubLabelsSectionView:

    def __init__(self, model):
        self.model = model

    def populate(self):
        pass


def _load_labels_section_class():
    path = os.path.join(
        REPO, 'setzer/workspace/sidebar/document_structure_page/labels.py')
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    constants = [node for node in tree.body if isinstance(node, ast.Assign)]
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'LabelsSection')
    namespace = {
        'GLib': types.SimpleNamespace(utf8_collate_key=lambda text, length: text),
        'labels_section_view': types.SimpleNamespace(
            LabelsSectionView=_StubLabelsSectionView),
        'analyze_labels': analyze_labels,
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root not in sys.stdlib_module_names:
                    continue
                name = alias.asname or alias.name.split('.')[0]
                if name not in namespace:
                    namespace[name] = importlib.import_module(alias.name)
    exec(compile(ast.Module(body=constants + [cls_node], type_ignores=[]),
                 path, 'exec'), namespace)
    return namespace['LabelsSection']


LabelsSection = _load_labels_section_class()


class AnalyzeLabelsTest(unittest.TestCase):

    def test_labels_without_references_are_unused(self):
        report = analyze_labels(['sec:intro', 'fig:plot'], ['sec:intro'])
        self.assertEqual(report.unused_names, frozenset(['fig:plot']))
        self.assertEqual(report.duplicate_names, frozenset())

    def test_repeated_definitions_are_duplicates(self):
        report = analyze_labels(['a', 'a', 'b'], ['a'])
        self.assertEqual(report.duplicate_names, frozenset(['a']))
        self.assertEqual(report.duplicate_counts, {'a': 2})
        self.assertEqual(report.unused_names, frozenset(['b']))

    def test_every_reference_use_counts(self):
        report = analyze_labels(['a'], ['a', 'a'])
        self.assertEqual(report.unused_names, frozenset())
        self.assertTrue(report.is_empty)

    def test_empty_definition_names_are_ignored(self):
        report = analyze_labels(['', 'a'], [])
        self.assertEqual(report.unused_names, frozenset(['a']))

    def test_empty_input_is_empty_report(self):
        self.assertTrue(analyze_labels([], []).is_empty)


class IterLabelProblemsTest(unittest.TestCase):

    def describe(self, occurrence):
        document_index, offset = occurrence
        if document_index != 0:
            return ('/project/chapter.tex', offset // 10)
        return ('/project/main.tex', offset)

    def test_unused_labels_become_per_occurrence_problems(self):
        problems = list(iter_label_problems(
            [('sec:a', (0, 10)), ('fig:b', (1, 20))],
            ['sec:a'],
            self.describe,
        ))
        self.assertEqual(len(problems), 1)
        problem = problems[0]
        self.assertIsInstance(problem, ProjectProblem)
        self.assertEqual(problem.severity, 'warning')
        self.assertEqual(problem.source, 'labels')
        self.assertEqual(problem.filename, '/project/chapter.tex')
        self.assertEqual(problem.line, 2)
        self.assertIn('fig:b', problem.description)
        self.assertEqual(problem.actions, ('open-file', 'jump-to-source'))

    def test_duplicates_win_over_unused(self):
        problems = list(iter_label_problems(
            [('a', (0, 1)), ('a', (0, 2))],
            [],
            self.describe,
        ))
        # 'a' 重复且未被引用：只报重复（每处定义一条），不叠加未使用。
        self.assertEqual(len(problems), 2)
        self.assertTrue(all('defined 2 times' in problem.description
                            for problem in problems))
        self.assertEqual(sorted(problem.line for problem in problems), [1, 2])

    def test_unresolvable_occurrence_keeps_problem_without_location(self):
        def describe_none(occurrence):
            return None
        problems = list(iter_label_problems([('a', ('doc', 0))], [], describe_none))
        self.assertEqual(len(problems), 1)
        self.assertIsNone(problems[0].filename)
        self.assertIsNone(problems[0].line)
        self.assertEqual(problems[0].actions, ())

    def test_clean_documents_yield_nothing(self):
        problems = list(iter_label_problems(
            [('a', (0, 1))], ['a'], self.describe))
        self.assertEqual(problems, [])


class ParserRefsExtractionTest(unittest.TestCase):

    def parse(self, text):
        parser = ParserLaTeX(_ParserDocument())
        parser.initial_parse(text)
        return parser

    def test_ref_family_commands_are_extracted_with_offsets(self):
        text = (
            '\\section{Intro}\\label{sec:intro}\n'
            'As shown in \\ref{sec:intro} and \\eqref{eq:x} on\n'
            '\\pageref{sec:intro}, see \\autoref{fig:y}, \\cref{sec:intro},\n'
            '\\Cref{sec:intro}, \\vref{sec:intro}, \\nameref{sec:intro}.\n'
        )
        refs = self.parse(text).symbols['refs_with_offset']
        names = [name for name, _offset in refs]
        self.assertEqual(names, ['sec:intro', 'eq:x', 'sec:intro', 'fig:y',
                                 'sec:intro', 'sec:intro', 'sec:intro',
                                 'sec:intro'])
        self.assertEqual(refs[0][1], text.index('\\ref{sec:intro}'))

    def test_starred_and_optional_argument_forms_are_matched(self):
        text = '\\ref*{sec:a} and \\pageref[p.~1]{sec:b}\n'
        names = [name for name, _offset in self.parse(text).symbols['refs_with_offset']]
        self.assertEqual(names, ['sec:a', 'sec:b'])

    def test_comma_separated_multi_references_are_split(self):
        text = '\\cref{fig:a, fig:b,fig:c}\n'
        names = [name for name, _offset in self.parse(text).symbols['refs_with_offset']]
        self.assertEqual(names, ['fig:a', 'fig:b', 'fig:c'])

    def test_longer_ref_prefixed_commands_are_not_matched(self):
        text = '\\refstepcounter{equation}\n\\refname\n'
        self.assertEqual(self.parse(text).symbols['refs_with_offset'], [])

    def test_commented_out_references_are_skipped(self):
        text = (
            '\\ref{alive}\n'
            '% \\ref{in-comment}\n'
            '100\\% \\ref{after-escaped-percent}\n'
            '\\\\\n% \\ref{after-linebreak}\n'
        )
        names = [name for name, _offset in self.parse(text).symbols['refs_with_offset']]
        self.assertEqual(names, ['alive', 'after-escaped-percent'])

    def test_is_in_comment_parities(self):
        is_in_comment = ParserLaTeX._is_in_comment
        # 未转义 % 之后的行内部分是注释。
        self.assertTrue(is_in_comment('ok % \\ref{x}', 10))
        # \% 是字面百分号，其后不是注释。
        self.assertFalse(is_in_comment('100\\% \\ref{x}', 11))
        # \\ 是换行命令，其后仍未转义的 % 开启注释。
        self.assertTrue(is_in_comment('a\\\\% \\ref{x}', 10))
        # 注释不跨行。
        self.assertFalse(is_in_comment('% c\n\\ref{x}', 8))
        # 行首的 % 整行是注释。
        self.assertTrue(is_in_comment('% \\ref{x}', 5))

    def test_labels_are_still_extracted_alongside_refs(self):
        text = '\\label{sec:a}\n\\ref{sec:a}\n'
        parser = self.parse(text)
        self.assertEqual(parser.symbols['labels'], {'sec:a'})
        names = [name for name, _offset in parser.symbols['refs_with_offset']]
        self.assertEqual(names, ['sec:a'])


class _FakeDocument:
    '''可哈希的假文档（integrated_includes 以 document 对象为 dict 键）。'''

    def __init__(self, symbols):
        self.parser = types.SimpleNamespace(symbols=symbols)


class LabelsSectionFlaggingTest(unittest.TestCase):
    '''侧栏 labels 模型的聚合逻辑：根文档 + 已打开 include 合并分析，
    duplicate 优先于 unused，跨文档的 \ref 算有效使用。'''

    def _make_section(self, root_symbols, include_symbols):
        root = _FakeDocument(root_symbols)
        include = _FakeDocument(include_symbols)
        data_provider = types.SimpleNamespace(
            document=root,
            integrated_includes={include: (include, 0)},
            connect=lambda *args: None,
        )
        return LabelsSection(data_provider)

    def test_flags_merge_root_and_includes(self):
        section = self._make_section(
            {
                'labels_with_offset': [('dup', 0), ('used', 10), ('shared', 20)],
                'refs_with_offset': [('used', 30)],
            },
            {
                'labels_with_offset': [('dup', 0), ('lone', 5)],
                'refs_with_offset': [('shared', 8)],
            },
        )
        section.update_items()
        flags = {label[0]: label[3] for label in section.labels}
        self.assertEqual(flags, {
            'dup': 'duplicate',   # 两处定义 → 重复优先
            'lone': 'unused',     # 定义在 include、无任何引用
            'shared': None,       # 根文档定义、include 里引用
            'used': None,         # 同文档定义并引用
        })
        # 排序键仍是名字；行结构 [name, offset, document, flag] 保持
        # on_row_activated 依赖的索引。'dup' 两处定义各占一行。
        self.assertEqual([label[0] for label in section.labels],
                         ['dup', 'dup', 'lone', 'shared', 'used'])
        self.assertEqual(section.labels[0][2], section.data_provider.document)

    def test_missing_refs_symbol_is_tolerated(self):
        # 旧解析结果或非 LaTeX 文档没有 refs_with_offset 时按无引用处理，
        # 不抛异常。
        section = self._make_section(
            {'labels_with_offset': [('a', 0)]}, {'labels_with_offset': []})
        section.update_items()
        self.assertEqual(section.labels[0][3], 'unused')


if __name__ == '__main__':
    unittest.main()

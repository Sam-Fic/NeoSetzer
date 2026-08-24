#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

r"""cite 命令带方括号可选项（页码）时的补全（上游 issue #312）。

验证三层：
- LaTeXDB._compile_dynamic_regexes：cite 正则允许命令名与 '{' 之间带闭合的
  [...]，且可选项并入捕获组——get_dynamic_proposals 的提案因此携带 [34]，
  tab/submit 从词起点替换后选项不被吞掉。
- get_dynamic_proposals / get_items：\autocite[34]{en 场景下所有提案都以已
  输入的完整前缀开头（插入后缀 = proposal[len(word):] 的对齐不变量）；
  无选项的经典 \cite{ 路径与 \ref 路径行为不变。
- autocomplete._CITE_OPTARG_REGEX：\autocite[34]{ 形态激活、白名单外的
  带可选参数命令（\textcolor 等）不误触发。
"""

import re
import unittest

from setzer.app.latex_db import LaTeXDB


class TestCiteDynamicProposals(unittest.TestCase):
    """带可选参数的 cite 动态补全：prefix 携带 [..]，提案与输入前缀对齐。"""

    @classmethod
    def setUpClass(cls):
        cls._saved_ref = LaTeXDB._ref_regex
        cls._saved_cite = LaTeXDB._cite_regex
        LaTeXDB._compile_dynamic_regexes()

    @classmethod
    def tearDownClass(cls):
        LaTeXDB._ref_regex = cls._saved_ref
        LaTeXDB._cite_regex = cls._saved_cite

    def setUp(self):
        self._saved_files = LaTeXDB.files
        LaTeXDB.files = {
            'master.tex': {'bibitems': {'endsley2011', 'smith2020'}, 'labels': set()},
            'extra.tex': {'bibitems': set(), 'labels': {'sec:intro'}},
        }

    def tearDown(self):
        LaTeXDB.files = self._saved_files

    def test_issue_312_scenario_proposals_carry_option(self):
        """\autocite[34]{en 的提案形如 \autocite[34]{endsley2011}。"""
        proposals = LaTeXDB.get_dynamic_proposals(r'\autocite[34]{en')
        commands = [p['command'] for p in proposals]
        self.assertIn(r'\autocite[34]{endsley2011}', commands)
        for command in commands:
            self.assertTrue(command.startswith(r'\autocite[34]{'),
                            f'{command!r} must keep the optional argument')

    def test_empty_key_still_lists_all(self):
        """issue 原文形态：刚打到 \autocite[34]{ 时列出全部 key。"""
        commands = [p['command'] for p in LaTeXDB.get_dynamic_proposals(r'\autocite[34]{')]
        # bibitems 是 set，迭代顺序随哈希种子变化，比较前排序。
        self.assertEqual(sorted(commands),
                         sorted([r'\autocite[34]{endsley2011}', r'\autocite[34]{smith2020}']))

    def test_insertion_suffix_alignment_invariant(self):
        """补全替换从词起点起算（追加后缀 = proposal[len(word):]），因此
        对齐的提案必须存在且排名第一；key 尚未输入时所有提案都必须严格
        以 current_word 开头。

        注意不要求模糊命中的非前缀提案也满足 startswith——那是有意为之
        的既有排序语义（纯 \citep{s 场景在改动前同样如此）。"""
        # 空 key 态：全部提案天然对齐。
        for word in [r'\autocite[34]{', r'\citet*[note]{', r'\autocite[]{']:
            for item in LaTeXDB.get_items(word):
                self.assertTrue(item['command'].startswith(word),
                                f'{item["command"]!r} must extend {word!r}')
        # 部分 key 态：至少有对齐提案，且排在首位。
        for word, expected in [(r'\autocite[34]{e', r'\autocite[34]{endsley2011}'),
                               (r'\citep[p.~12]{sm', r'\citep[p.~12]{smith2020}')]:
            commands = [item['command'] for item in LaTeXDB.get_items(word)]
            self.assertIn(expected, commands)
            self.assertEqual(commands[0], expected,
                             f'{expected!r} should rank first for {word!r}')

    def test_plain_cite_path_unchanged(self):
        """无可选参数的经典路径不受影响，且不出现双重括号/重复选项。"""
        commands = [p['command'] for p in LaTeXDB.get_dynamic_proposals(r'\cite{smi')]
        self.assertIn(r'\cite{smith2020}', commands)
        for command in commands:
            self.assertNotIn('[', command)

    def test_ref_path_unaffected(self):
        """ref 正则未扩展：\ref{sec: 提案保持原样。"""
        commands = [p['command'] for p in LaTeXDB.get_dynamic_proposals(r'\ref{sec:')]
        self.assertIn(r'\ref{sec:intro}', commands)
        for command in commands:
            self.assertNotIn('[', command)

    def test_unclosed_bracket_falls_back_to_bare_prefix(self):
        """页码尚未打完（\autocite[34）时正则回退为裸命令匹配，不崩溃。"""
        match = LaTeXDB._cite_regex.match(r'\autocite[34')
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), r'\autocite')

    def test_is_dynamic_query_with_option(self):
        """db_error 提示行的判定覆盖新场景。"""
        self.assertTrue(LaTeXDB.is_dynamic_query(r'\autocite[34]{en'))

    def test_multifile_dedup_still_applies(self):
        """跨文件去重逻辑在带选项场景同样生效。"""
        LaTeXDB.files['extra.tex']['bibitems'] = {'endsley2011'}
        commands = [p['command'] for p in LaTeXDB.get_dynamic_proposals(r'\autocite[34]{')]
        self.assertEqual(len(commands), len(set(commands)))


class TestCiteOptargActivationRegex(unittest.TestCase):
    """激活正则：命中 issue #312 形态，不误伤其他带可选参数的命令。"""

    @classmethod
    def setUpClass(cls):
        # autocomplete 模块级正则由 citations 白名单构建，import 时即就绪。
        from setzer.document.autocomplete.autocomplete import _CITE_OPTARG_REGEX
        cls.regex = _CITE_OPTARG_REGEX

    def assert_activates(self, line):
        match = self.regex.search(line)
        self.assertIsNotNone(match, f'{line!r} should activate citation completion')
        self.assertEqual(match.start(), line.rindex('\\'))

    def test_issue_312_exact_form(self):
        self.assert_activates(r'\autocite[34]{')

    def test_with_partial_key(self):
        self.assert_activates(r'\autocite[34]{en')

    def test_page_number_with_dot_and_tilde(self):
        self.assert_activates(r'\citep[p.~12]{smi')

    def test_starred_variant(self):
        self.assert_activates(r'\citet*[note]{x')

    def test_leading_text_on_line(self):
        self.assert_activates('see \\textcite[chs. 3-4]{')

    def test_empty_optional_argument(self):
        self.assert_activates(r'\parencite[]{k')

    def test_rejects_non_cite_commands_with_options(self):
        for line in [r'\textcolor[RGB]{255,0', r'\includegraphics[width=3cm]{img',
                     r'\href[http://x]{y', r'\makebox[2cm][l]{ab']:
            self.assertIsNone(self.regex.search(line), f'{line!r} must not activate')

    def test_rejects_malformed_states(self):
        for line in [r'\cite[34', r'\cite[unclosed{x', r'\autocite{plain',
                     r'\section{intro', 'no command here', '']:
            self.assertIsNone(self.regex.search(line), f'{line!r} must not activate')


if __name__ == '__main__':
    unittest.main()

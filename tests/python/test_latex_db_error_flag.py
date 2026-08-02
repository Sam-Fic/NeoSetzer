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

r"""LaTeXDB 解析错误标志 + is_dynamic_query 单元测试（UX 报告 #8）。

验证：
- _do_parse_included_files 解析失败时设 last_parse_error，成功时清除。
- is_dynamic_query 正确区分 \ref/\cite 动态查询与静态命令查询。

gi-free：latex_db.py 顶层 import gi + GLib，但本环境已装 gi.typelib，
直接 import 即可（无需 conftest_stub）。仅测类方法 + 类属性，不依赖
workspace/resources/init()。
"""

import io
import re
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from setzer.app.latex_db import LaTeXDB


class TestIsDynamicQuery(unittest.TestCase):
    """is_dynamic_query: 判断 word 是否为 \\ref/\\cite 类动态补全查询。"""

    @classmethod
    def setUpClass(cls):
        # 编译 ref/cite 正则（init() 需要 resources + workspace，此处直接编译）。
        # dynamic_commands 是类级属性，类定义时即就绪。
        ref_pattern = '(' + re.escape('|'.join(LaTeXDB.dynamic_commands['references'])).replace('\\|', '|') + ')'
        cite_pattern = '(' + re.escape('|'.join(LaTeXDB.dynamic_commands['citations'])).replace('\\|', '|') + ')'
        cls._saved_ref = LaTeXDB._ref_regex
        cls._saved_cite = LaTeXDB._cite_regex
        LaTeXDB._ref_regex = re.compile(ref_pattern)
        LaTeXDB._cite_regex = re.compile(cite_pattern)

    @classmethod
    def tearDownClass(cls):
        LaTeXDB._ref_regex = cls._saved_ref
        LaTeXDB._cite_regex = cls._saved_cite

    def test_ref_commands_are_dynamic(self):
        for word in [r'\ref', r'\ref{', r'\ref{label', r'\pageref', r'\eqref{x']:
            with self.subTest(word=word):
                self.assertTrue(LaTeXDB.is_dynamic_query(word),
                                f'{word!r} should be a dynamic query')

    def test_cite_commands_are_dynamic(self):
        for word in [r'\cite', r'\cite{', r'\citep{key', r'\textcite{author2020', r'\citet*']:
            with self.subTest(word=word):
                self.assertTrue(LaTeXDB.is_dynamic_query(word),
                                f'{word!r} should be a dynamic query')

    def test_static_commands_not_dynamic(self):
        for word in [r'\section', r'\begin', r'\textbf', r'\usepackage', r'\item', 'abc', '']:
            with self.subTest(word=word):
                self.assertFalse(LaTeXDB.is_dynamic_query(word),
                                 f'{word!r} should NOT be a dynamic query')

    def test_no_regex_returns_false(self):
        """正则未编译时（init 未调用）安全返回 False 而非崩溃。"""
        old_ref, old_cite = LaTeXDB._ref_regex, LaTeXDB._cite_regex
        LaTeXDB._ref_regex = None
        LaTeXDB._cite_regex = None
        try:
            self.assertFalse(LaTeXDB.is_dynamic_query(r'\ref'))
        finally:
            LaTeXDB._ref_regex, LaTeXDB._cite_regex = old_ref, old_cite


class TestParseErrorFlag(unittest.TestCase):
    """_do_parse_included_files: 解析失败设 error、成功清 error。"""

    def setUp(self):
        self._saved_error = LaTeXDB.last_parse_error
        self._saved_idle = LaTeXDB._refresh_idle_id
        LaTeXDB._refresh_idle_id = None

    def tearDown(self):
        LaTeXDB.last_parse_error = self._saved_error
        LaTeXDB._refresh_idle_id = self._saved_idle

    def test_initial_state_is_none(self):
        """last_parse_error 初始值（类属性）为 None。"""
        # 直接检查类属性定义，不受 setUp 保存值影响
        self.assertIsNone(LaTeXDB.__dict__.get('last_parse_error'))

    def test_error_set_on_exception(self):
        """parse_included_files 抛异常时，last_parse_error 设为 traceback 文本。"""
        with redirect_stderr(io.StringIO()):
            with patch.object(LaTeXDB, 'parse_included_files',
                              side_effect=Exception('parse failed')):
                LaTeXDB._do_parse_included_files()
        self.assertIsNotNone(LaTeXDB.last_parse_error)
        self.assertIn('parse failed', LaTeXDB.last_parse_error)
        self.assertIn('Traceback', LaTeXDB.last_parse_error)

    def test_error_cleared_on_success(self):
        """parse_included_files 成功时，last_parse_error 清为 None。"""
        LaTeXDB.last_parse_error = 'previous error traceback'
        with patch.object(LaTeXDB, 'parse_included_files', return_value=None):
            LaTeXDB._do_parse_included_files()
        self.assertIsNone(LaTeXDB.last_parse_error)

    def test_idle_id_cleared_after_call(self):
        """_do_parse_included_files 执行后 _refresh_idle_id 重置为 None。"""
        with redirect_stderr(io.StringIO()):
            with patch.object(LaTeXDB, 'parse_included_files', return_value=None):
                LaTeXDB._do_parse_included_files()
        self.assertIsNone(LaTeXDB._refresh_idle_id)

    def test_error_then_success_cycle(self):
        """先失败再成功：error 先设后清。"""
        # 失败
        with redirect_stderr(io.StringIO()):
            with patch.object(LaTeXDB, 'parse_included_files',
                              side_effect=ValueError('bad xml')):
                LaTeXDB._do_parse_included_files()
        self.assertIsNotNone(LaTeXDB.last_parse_error)
        self.assertIn('bad xml', LaTeXDB.last_parse_error)

        # 成功
        with patch.object(LaTeXDB, 'parse_included_files', return_value=None):
            LaTeXDB._do_parse_included_files()
        self.assertIsNone(LaTeXDB.last_parse_error)


if __name__ == '__main__':
    unittest.main()

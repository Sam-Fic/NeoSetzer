#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Statistics refreshes must not install continuously repeating idle sources."""

import ast
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


PATH = Path(__file__).resolve().parents[2] / 'setzer/workspace/sidebar/document_stats/document_stats.py'


def load_stats(idle_add, popen):
    # Exercise production methods without constructing GTK widgets/display.
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DocumentStats')
    methods = {'count_chars_lines', '_update_view_idle', 'run_query', 'update_view'}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    counter = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'count_chars_lines')
    namespace = {
        'GLib': SimpleNamespace(idle_add=idle_add),
        'subprocess': SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE,
                                     STDOUT=subprocess.STDOUT,
                                     TimeoutExpired=subprocess.TimeoutExpired),
        'sys': SimpleNamespace(platform='linux'),
    }
    exec(compile(ast.Module(body=[counter, cls], type_ignores=[]), str(PATH), 'exec'), namespace)
    return namespace['DocumentStats']()


class TestStatsIdleRefresh(unittest.TestCase):
    def setUp(self):
        self.pending = []
        self.process = Mock()
        self.process.communicate.return_value = (b'12+3+4 total', None)
        self.popen = Mock(return_value=self.process)
        self.stats = load_stats(self.pending.append, self.popen)
        self.stats.values_lock = threading.Lock()
        self.stats.values = {'test.tex': {'counts': None, 'python_counts': None}}
        self.stats._inflight = {'test.tex'}
        self.stats.texcount_missing = False
        self.stats.update_view = Mock(return_value=True)

    def assert_one_shot(self):
        self.assertEqual(len(self.pending), 1)
        callback = self.pending.pop()
        # GLib removes sources returning False; True would spin indefinitely.
        self.assertIs(callback(), False)
        self.stats.update_view.assert_called_once_with()

    def test_character_count_refresh_is_one_shot(self):
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as source:
            source.write('hello world\n')
            source.flush()
            self.stats.values[source.name] = {}
            self.stats.count_chars_lines(source.name)
            self.assertEqual(self.stats.values[source.name]['python_counts'], (12, 10, 1))
        self.assert_one_shot()

    def test_query_success_refresh_is_one_shot(self):
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.assertEqual(self.stats.values['test.tex']['counts'], ['12', '3', '4'])
        self.assertFalse(self.stats._inflight)
        self.assert_one_shot()

    def test_missing_texcount_refresh_is_one_shot(self):
        self.popen.side_effect = FileNotFoundError
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.assertTrue(self.stats.texcount_missing)
        self.assertFalse(self.stats._inflight)
        self.assert_one_shot()

    def test_timeout_refresh_is_one_shot(self):
        self.process.wait.side_effect = [subprocess.TimeoutExpired('texcount', 30), None]
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.process.kill.assert_called_once_with()
        self.assertFalse(self.stats._inflight)
        self.assert_one_shot()

    def test_invalid_output_refresh_is_one_shot(self):
        self.process.communicate.return_value = (b'invalid output', None)
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.assertIsNone(self.stats.values['test.tex']['counts'])
        self.assert_one_shot()

    def test_periodic_refresh_still_repeats(self):
        for name in ('_show_texcount_missing', '_hide_whole_document',
                     '_hide_current_file_words', '_hide_texcount_missing',
                     '_update_whole_document_words', '_update_current_file_words',
                     '_update_chars_lines'):
            setattr(self.stats, name, Mock())
        for missing in (False, True):
            self.stats.texcount_missing = missing
            self.assertIs(type(self.stats).update_view(self.stats), True)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later
"""Statistics refreshes must not install continuously repeating idle sources."""

import ast
import os.path
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[2] / 'setzer/workspace/sidebar/document_stats/document_stats.py'


def load_stats(idle_add, popen):
    # Exercise production methods without constructing GTK widgets/display.
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DocumentStats')
    methods = {'count_chars_lines', '_update_view_idle', 'run_query', 'update_view', 'update_data'}
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    counter = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'count_chars_lines')
    namespace = {
        'os': os,
        'path_helpers': SimpleNamespace(get_abspath=lambda name, directory: os.path.join(directory, name)),
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
        self.process.communicate.side_effect = [subprocess.TimeoutExpired('texcount', 30), (b'', None)]
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.process.kill.assert_called_once_with()
        self.assertEqual(self.process.communicate.call_count, 2)
        self.assertFalse(self.stats._inflight)
        self.assert_one_shot()

    def test_subprocess_output_is_drained_without_holding_view_lock(self):
        def communicate(timeout):
            self.assertEqual(timeout, 30)
            self.assertFalse(self.stats.values_lock.locked())
            return b'12+3+4 total', None
        self.process.communicate.side_effect = communicate
        self.stats.run_query(['texcount', 'test.tex'], 'test.tex')
        self.process.wait.assert_not_called()
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


class TestStatsRefreshScheduling(unittest.TestCase):
    def setUp(self):
        self.stats = load_stats(Mock(), Mock())
        self.stats.values = {}
        self.stats.values_lock = threading.Lock()
        self.stats._inflight = set()
        self.stats.document = SimpleNamespace(
            get_filename=lambda: '/project/main.tex', get_dirname=lambda: '/project',
            parser=SimpleNamespace(symbols={'included_latex_files': [('child.tex', 0)]}))
        self.active_filename = '/project/main.tex'
        self.stats.workspace = SimpleNamespace(get_active_document=lambda: SimpleNamespace(
            get_filename=lambda: self.active_filename))
        self.stats.count_words = Mock(side_effect=lambda name: self.stats._inflight.add(name))
        self.stats.count_chars_lines = Mock()
        self.mtimes = {'/project/main.tex': 1, '/project/child.tex': 1}
        self.patcher = patch('os.path.getmtime', side_effect=self.mtimes.__getitem__)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_character_counts_only_read_the_active_file(self):
        self.assertIs(self.stats.update_data(), True)
        self.stats.count_chars_lines.assert_called_once_with('/project/main.tex')
        self.assertEqual(self.stats.count_words.call_count, 2)
        self.stats.update_data()
        self.stats.count_chars_lines.assert_called_once_with('/project/main.tex')
        self.assertEqual(self.stats.count_words.call_count, 2)

    def test_switching_to_previously_inactive_file_fills_character_cache(self):
        self.stats.update_data()
        self.stats.count_chars_lines.reset_mock()
        self.active_filename = '/project/child.tex'
        self.stats.update_data()
        self.stats.count_chars_lines.assert_called_once_with('/project/child.tex')
        self.stats.update_data()
        self.stats.count_chars_lines.assert_called_once_with('/project/child.tex')

    def test_inflight_edits_are_not_marked_as_counted(self):
        self.stats.update_data()
        self.stats.count_words.reset_mock()
        self.mtimes['/project/main.tex'] = 2
        self.stats.update_data()
        self.mtimes['/project/main.tex'] = 3
        self.stats.update_data()
        self.assertEqual(self.stats.values['/project/main.tex']['save_date'], 1)
        self.stats.count_words.assert_not_called()
        self.stats._inflight.remove('/project/main.tex')
        self.stats.update_data()
        self.stats.count_words.assert_called_once_with('/project/main.tex')
        self.assertEqual(self.stats.values['/project/main.tex']['save_date'], 3)

    def test_older_mtime_also_invalidates_counts(self):
        self.stats.update_data()
        self.stats._inflight.clear()
        self.stats.count_words.reset_mock()
        self.stats.count_chars_lines.reset_mock()
        self.mtimes['/project/main.tex'] = 0.5
        self.stats.update_data()
        self.stats.count_words.assert_called_once_with('/project/main.tex')
        self.stats.count_chars_lines.assert_called_once_with('/project/main.tex')


if __name__ == '__main__':
    unittest.main()

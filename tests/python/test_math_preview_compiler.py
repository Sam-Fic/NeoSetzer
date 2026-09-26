#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''snippet_compiler 的纯逻辑测试：命令构造 + 注入 runner 的编译路径 +
失败原因提取。'''

import os
import subprocess
import tempfile
import unittest

from setzer.document.snippet_preview.snippet_compiler import (
    build_compile_command,
    compile_snippet,
    extract_failure_reason,
)
from setzer.document.snippet_preview.snippet_cache import SnippetCache


class ExtractFailureReasonTest(unittest.TestCase):

    def test_latex_error_line(self):
        output = (
            'This is pdfTeX, Version 3.141592653\n'
            '(./snippet.tex\n'
            "! LaTeX Error: File `microtype.sty' not found.\n"
            'Type  X to quit or <RETURN> to proceed,\n'
        )
        self.assertEqual(extract_failure_reason(output),
                         "LaTeX Error: File `microtype.sty' not found.")

    def test_package_error_line(self):
        output = "! Package pgf Error: No shape named `A' is known.\n"
        self.assertEqual(extract_failure_reason(output),
                         "Package pgf Error: No shape named `A' is known.")

    def test_first_error_wins(self):
        output = '! Undefined control sequence.\n! Emergency stop.\n'
        self.assertEqual(extract_failure_reason(output),
                         'Undefined control sequence.')

    def test_engine_without_bang_marker(self):
        self.assertEqual(extract_failure_reason('error: could not fetch bundle'),
                         'error: could not fetch bundle')

    def test_unrecognized_output_returns_empty(self):
        self.assertEqual(extract_failure_reason('This is pdfTeX\n(./x.tex)'), '')
        self.assertEqual(extract_failure_reason(''), '')
        self.assertEqual(extract_failure_reason(None), '')

    def test_bang_inside_a_line_is_not_an_error_block(self):
        # 只认行首的 ``!``：正文里出现的 ! 不是错误块。
        self.assertEqual(extract_failure_reason('see this ! note'), '')


class BuildCompileCommandTest(unittest.TestCase):

    def test_standard_engines_share_shape(self):
        for engine in ('xelatex', 'pdflatex', 'lualatex'):
            with self.subTest(engine=engine):
                cmd = build_compile_command(engine, '/w/abc.tex', '/w')
                self.assertEqual(cmd[0], engine)
                self.assertIn('-interaction=nonstopmode', cmd)
                self.assertIn('-halt-on-error', cmd)
                self.assertIn('-output-directory=/w', cmd)
                self.assertEqual(cmd[-1], '/w/abc.tex')

    def test_tectonic(self):
        cmd = build_compile_command('tectonic', '/w/abc.tex', '/w')
        self.assertEqual(cmd, ['tectonic', '--outdir', '/w', '/w/abc.tex'])

    def test_no_shell_metachar_risk(self):
        # 列表形态参数：路径含空格也无需 shell 引用。
        cmd = build_compile_command('xelatex', '/my dir/abc.tex', '/my dir')
        self.assertEqual(cmd[-1], '/my dir/abc.tex')
        self.assertEqual(cmd[-2], '-output-directory=/my dir')


class CompileSnippetTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work_dir = os.path.join(self.tmp.name, 'work')
        self.cache = SnippetCache(os.path.join(self.tmp.name, 'cache'))

    def _fake_runner(self, writes_pdf=True, returncode=0):
        calls = []
        def run(cmd, cwd, timeout):
            calls.append((cmd, cwd, timeout))
            if writes_pdf:
                stem = os.path.splitext(cmd[-1])[0]
                with open(stem + '.pdf', 'wb') as f:
                    f.write(b'%PDF-1.4 fake')
            return subprocess.CompletedProcess(cmd, returncode)
        run.calls = calls
        return run

    def test_success_stores_pdf_in_cache(self):
        runner = self._fake_runner()
        result = compile_snippet(
            '\\documentclass{article}', 'xelatex',
            self.work_dir, self.cache, 'key1', runner=runner)
        self.assertIsNotNone(result)
        self.assertEqual(result, self.cache.pdf_path('key1'))
        self.assertTrue(self.cache.has_pdf('key1'))
        # 命令指向临时目录里的 .tex，且 .tex 用后清理。
        cmd, cwd, timeout = runner.calls[0]
        self.assertEqual(cwd, self.work_dir)
        self.assertTrue(cmd[-1].endswith('key1.tex'))
        self.assertFalse(os.path.exists(cmd[-1]))
        self.assertEqual(timeout, 30.0)

    def test_engine_failure_returns_none(self):
        runner = self._fake_runner(writes_pdf=False)
        result = compile_snippet(
            'broken', 'xelatex', self.work_dir, self.cache, 'key2', runner=runner)
        self.assertIsNone(result)
        self.assertFalse(self.cache.has_pdf('key2'))

    def test_failure_reason_is_reported(self):
        def runner(cmd, cwd, timeout):
            return subprocess.CompletedProcess(
                cmd, 1, stdout="! LaTeX Error: File `microtype.sty' not found.\n")
        diagnostics = dict()
        result = compile_snippet(
            'broken', 'xelatex', self.work_dir, self.cache, 'key6',
            runner=runner, diagnostics=diagnostics)
        self.assertIsNone(result)
        self.assertEqual(diagnostics['reason'],
                         "LaTeX Error: File `microtype.sty' not found.")

    def test_reason_stays_untouched_on_success(self):
        diagnostics = dict()
        result = compile_snippet(
            '$x$', 'xelatex', self.work_dir, self.cache, 'key7',
            runner=self._fake_runner(), diagnostics=diagnostics)
        self.assertIsNotNone(result)
        self.assertNotIn('reason', diagnostics)

    def test_timeout_reason_is_reported(self):
        def slow_runner(cmd, cwd, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)
        diagnostics = dict()
        result = compile_snippet(
            '$x$', 'xelatex', self.work_dir, self.cache, 'key8',
            runner=slow_runner, diagnostics=diagnostics)
        self.assertIsNone(result)
        self.assertTrue(diagnostics['reason'])

    def test_timeout_returns_none(self):
        def slow_runner(cmd, cwd, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)
        result = compile_snippet(
            '$x$', 'xelatex', self.work_dir, self.cache, 'key3', runner=slow_runner)
        self.assertIsNone(result)

    def test_oserror_returns_none(self):
        def failing_runner(cmd, cwd, timeout):
            raise FileNotFoundError('xelatex not found')
        result = compile_snippet(
            '$x$', 'xelatex', self.work_dir, self.cache, 'key4', runner=failing_runner)
        self.assertIsNone(result)

    def test_recompile_overwrites_cache_entry(self):
        compile_snippet('$x$', 'xelatex', self.work_dir, self.cache,
                        'key5', runner=self._fake_runner())
        # 第二次编译写入不同内容，同名缓存应被新结果覆盖。
        runner = self._fake_runner()
        result = compile_snippet('$x$', 'xelatex', self.work_dir, self.cache,
                                 'key5', runner=runner)
        with open(result, 'rb') as f:
            self.assertEqual(f.read(), b'%PDF-1.4 fake')


if __name__ == '__main__':
    unittest.main()

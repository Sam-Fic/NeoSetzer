#!/usr/bin/env python3
# coding: utf-8
#
# PhiTeX 常驻 session 控制面的关键契约。
#
# 这几条都是实测踩出来的，不是从文档推的：
#   - `session pdf` 现场物化完整 PDF 要 ~285 ms，会把增量优势全吃掉，
#     所以必须 --exact 起 watch 并从 status 取路径（pdf_path 优先级）。
#   - 同目录多个 main 时，不带 --session 的 session 子命令会解析到别的
#     watch，把上一次构建的 PDF 当成本次结果返回。
#   - wait 的退出码 1 表示"文档有错但已构建完成"，不能当失败处理。

import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE = 'setzer/document/build_system/phitex_session/phitex_session.py'

import setzer.document.build_system.phitex_session.phitex_session as ps


class FakeProcess:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self._stdout = stdout

    def communicate(self, timeout=None):
        return self._stdout, ''

    def kill(self):
        pass


def fake_popen(returncode, stdout):
    calls = []

    def _popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess(returncode, stdout)
    return _popen, calls


class StartTest(unittest.TestCase):

    def test_start_passes_exact_and_session_scoped_watch_args(self):
        popen, calls = fake_popen(0, 'started pid 4321 · viewer http://x/ · log /tmp/log\n')
        session = ps.PhitexSession('/proj/main.tex', '/proj/build', True)
        with mock.patch.object(ps, 'shutil') as which, \
                mock.patch.object(ps, 'popen_no_window', popen), \
                mock.patch.object(ps, 'build_env', lambda: {}):
            which.which.return_value = '/usr/bin/phitex'
            self.assertTrue(session.start())
        command = calls[0][0]
        self.assertEqual(session.pid, 4321)
        # watch 自己的参数在 `--` 之后
        self.assertIn('--', command)
        watch_args = command[command.index('--') + 1:]
        self.assertEqual(watch_args, ['--exact', '-o', '/proj/build', '--synctex'])

    def test_reuses_an_existing_watch(self):
        popen, _ = fake_popen(0, 'running pid 999 · viewer http://x/\n')
        session = ps.PhitexSession('/proj/main.tex', None, False)
        with mock.patch.object(ps, 'shutil') as which, \
                mock.patch.object(ps, 'popen_no_window', popen), \
                mock.patch.object(ps, 'build_env', lambda: {}):
            which.which.return_value = '/usr/bin/phitex'
            self.assertTrue(session.start())
        self.assertEqual(session.pid, 999)

    def test_missing_binary_is_not_a_session(self):
        session = ps.PhitexSession('/proj/main.tex', None, False)
        with mock.patch.object(ps, 'shutil') as which:
            which.which.return_value = None
            self.assertFalse(session.start())
            self.assertIsNone(session.pid)


class SessionScopingTest(unittest.TestCase):
    '''每条 session 子命令都必须显式带 --session <pid>。'''

    def _run(self, method, *args):
        popen, calls = fake_popen(0, '{"ok": true}\n')
        session = ps.PhitexSession('/proj/main.tex', None, False)
        session.pid = 1234
        with mock.patch.object(ps, 'shutil') as which, \
                mock.patch.object(ps, 'popen_no_window', popen), \
                mock.patch.object(ps, 'build_env', lambda: {}):
            which.which.return_value = '/usr/bin/phitex'
            result = getattr(session, method)(*args)
        return calls[0][0], result

    def test_status_problems_and_pdf_are_pid_scoped(self):
        for method, args in (('status', ()), ('problems', ())):
            command, _ = self._run(method, *args)
            self.assertIn('--session', command)
            self.assertEqual(command[command.index('--session') + 1], '1234')

    def test_stop_answers_the_quit_prompt(self):
        command, _ = self._run('stop')
        self.assertIn('stop', command)
        # watch 退出前会问"要不要写出完整 PDF"，后台终止无人应答会挂住。
        self.assertIn('-y', command)


class PdfPathTest(unittest.TestCase):

    def setUp(self):
        self.session = ps.PhitexSession('/proj/main.tex', None, False)
        self.session.pid = 1

    def test_prefers_the_path_from_status_without_calling_session_pdf(self):
        '''--exact 已把 PDF 写好；再调 `session pdf` 实测多花 ~285 ms。'''
        with mock.patch.object(ps.os.path, 'isfile', return_value=True), \
                mock.patch.object(self.session, '_call') as call:
            path = self.session.pdf_path({'pdf': './main.pdf', 'root': '/proj'})
        self.assertEqual(path, '/proj/main.pdf')
        call.assert_not_called()

    def test_falls_back_to_writing_pdf_when_status_has_no_path(self):
        with mock.patch.object(self.session, '_call',
                               return_value=(0, '/proj/./main.pdf\n')) as call:
            path = self.session.pdf_path({'pdf': None})
        self.assertEqual(path, '/proj/main.pdf')
        call.assert_called_once()

    def test_falls_back_when_the_status_path_is_missing_on_disk(self):
        with mock.patch.object(ps.os.path, 'isfile', return_value=False), \
                mock.patch.object(self.session, '_call',
                                  return_value=(0, '/proj/main.pdf\n')):
            self.assertEqual(self.session.pdf_path({'pdf': './gone.pdf',
                                                   'root': '/proj'}),
                             '/proj/main.pdf')


class BuildTest(unittest.TestCase):

    def _build(self, returncode):
        session = ps.PhitexSession('/proj/main.tex', None, False)
        session.pid = 7
        with mock.patch.object(session, '_call',
                               return_value=(returncode, '')) as call, \
                mock.patch.object(session, 'status', return_value={'ok': True}):
            return session.build(), call

    def test_document_errors_still_count_as_built(self):
        '''wait 沿用引擎语义：1 = 文档有错但已构建完，结果可用。'''
        (waited, status), _ = self._build(1)
        self.assertTrue(waited)
        self.assertEqual(status, {'ok': True})

    def test_phitex_startup_failures_are_not_silently_built(self):
        '''2/3 是 PhiTeX 自己没跑起来，不能拿旧状态冒充本次结果。'''
        for returncode in (2, 3):
            (waited, status), call = self._build(returncode)
            self.assertFalse(waited)
            self.assertIsNone(status)
            call.assert_called_once()


class LogMessagesTest(unittest.TestCase):
    '''problems → 既有 log_messages 结构的映射，构建日志与跳转都按它取值。'''

    def _items(self, problems, root='/proj'):
        return ps.to_log_messages(problems, root, '/proj/main.tex')

    def test_shape_is_filename_to_three_buckets_of_triples(self):
        items = self._items([{'kind': 'error', 'severity': 'error', 'file': 'main.tex',
                              'line': 12, 'message': 'Undefined control sequence'}])
        self.assertEqual(list(items), ['/proj/main.tex'])
        entry = items['/proj/main.tex']
        self.assertEqual(sorted(entry), ['badbox', 'error', 'warning'])
        self.assertEqual(entry['error'],
                         [(None, 12, 'Undefined control sequence')])

    def test_overfull_goes_to_badbox_and_references_to_warning(self):
        items = self._items([
            {'kind': 'overfull', 'severity': 'warning', 'file': 'a.tex', 'line': 3,
             'message': 'Overfull \\hbox'},
            {'kind': 'reference', 'severity': 'warning', 'file': 'a.tex', 'line': 4,
             'message': 'Reference undefined'},
        ])
        entry = items['/proj/a.tex']
        self.assertEqual(len(entry['badbox']), 1)
        self.assertEqual(len(entry['warning']), 1)
        self.assertEqual(entry['error'], [])

    def test_items_sort_by_line_number(self):
        items = self._items([{'kind': 'error', 'severity': 'error', 'file': 'a.tex',
                              'line': line, 'message': 'e'} for line in (30, 2, 17)])
        self.assertEqual([i[1] for i in items['/proj/a.tex']['error']], [2, 17, 30])

    def test_absolute_file_paths_are_not_rebased(self):
        items = self._items([{'kind': 'error', 'severity': 'error',
                              'file': '/other/chap.tex', 'line': 1, 'message': 'e'}])
        self.assertIn('/other/chap.tex', items)

    def test_missing_line_number_does_not_break_the_jump(self):
        items = self._items([{'kind': 'error', 'severity': 'error', 'file': 'a.tex',
                              'message': 'no line'}])
        self.assertEqual(items['/proj/a.tex']['error'][0][1], -1)

    def test_detail_is_appended_but_not_duplicated(self):
        items = self._items([{'kind': 'error', 'severity': 'error', 'file': 'a.tex',
                              'line': 1, 'message': 'Boom', 'detail': 'Boom'}])
        self.assertEqual(items['/proj/a.tex']['error'][0][2], 'Boom')

    def test_no_problems_yields_empty_dict(self):
        self.assertEqual(self._items([]), {})


if __name__ == '__main__':
    unittest.main()

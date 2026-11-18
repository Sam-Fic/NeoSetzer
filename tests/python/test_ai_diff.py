#!/usr/bin/env python3
# coding: utf-8
#
# AiDiff（AI 编辑后的 PDF diff 显示）的门控状态机与接线。
#
# 状态机的两条硬约束：
#   - 自动构建会在 AI 编辑后连跑好几轮，同一文本绝不能重复 compare；
#   - 结果已出、正文又变了 → 是用户手改，AI 审阅轮必须结束（再 diff 就是
#     "AI 改动+用户改动"的混合体，横幅还写着 AI edited 就是误导）。
#
# 模块 import 依赖 GLib（idle_add 回主线程），无显示环境可直接导入；
# 测试里统一 mock 掉 GLib 与线程，不真起后台任务。

import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]

import setzer.helpers.phitex_compare as phitex_compare
import setzer.document.ai_diff.ai_diff as ai_diff_module
from setzer.document.ai_diff.ai_diff import AiDiff


class FakePreview:
    def __init__(self):
        self.results = []

    def set_diff_result(self, result):
        self.results.append(result)


class FakeSession:
    def __init__(self, returncode=0, stdout='', pid=12345):
        self.pid = pid
        self._returncode = returncode
        self._stdout = stdout
        self.compare_calls = []
        self.stopped = False

    def compare(self, baseline_path, timeout=None):
        self.compare_calls.append((baseline_path, timeout))
        return self._returncode, self._stdout

    def compare_stop(self):
        self.stopped = True


class FakeBuilder:
    def __init__(self, session=None):
        self.phitex_session = session


class FakeBuildSystem:
    def __init__(self, session=None):
        self.builders = {'build_latex': FakeBuilder(session)}


class FakeDocument:
    def __init__(self, text=''):
        self.text = text
        self.preview = FakePreview()
        # AiDiff 偷看（不消费）：非空 = 用户在 buffer 里手改过。
        self._dirty_line_ranges = []

    def get_all_text(self):
        return self.text


def make_diff(text='x'):
    # 默认带一个可用的 session：多数门控用例要走到 spawn/coalesce 分支。
    return AiDiff(FakeDocument(text), FakeBuildSystem(FakeSession()))


class DiffAfterBuildGatingTest(unittest.TestCase):
    '''diff_after_build 的三态门控。'''

    def setUp(self):
        # GLib 只在 _query_and_show 末尾用 idle_add，门控测试里不会走到；
        # 统一 mock 掉以防万一。
        patcher = mock.patch.object(ai_diff_module, 'GLib')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_baseline_does_nothing(self):
        diff = make_diff(text='anything')
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_unsupported_phitex_does_nothing(self):
        diff = make_diff(text='x')
        diff.arm_baseline('/tmp/base.tex')
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=False), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_text_equal_baseline_clears_stale_result(self):
        diff = make_diff(text='original')
        diff.arm_baseline('/tmp/base.tex')
        diff._last_compared_text = hash('edited')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=True), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True):
            diff.diff_after_build()
        # 旧结果被清（None 送达 preview）；基线保留武装——agent 之后若再改
        # 磁盘，仍按同一基线 diff。
        self.assertEqual(diff.document.preview.results, [None])
        self.assertIsNone(diff._last_compared_text)
        self.assertEqual(diff.baseline_path, '/tmp/base.tex')

    def test_text_equal_baseline_with_no_result_keeps_quiet(self):
        diff = make_diff(text='original')
        diff.arm_baseline('/tmp/base.tex')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=True), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True):
            diff.diff_after_build()
        # agent 还没改：不清（保留可能存在的上一轮结果——arm 已归零标记）。
        self.assertEqual(diff.document.preview.results, [])

    def test_same_text_skips_repeat_compare(self):
        diff = make_diff(text='edited')
        diff.arm_baseline('/tmp/base.tex')
        diff._last_compared_text = hash('edited')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_user_edit_ends_review(self):
        # 用户在 buffer 里键入（脏行非空）：清结果 + 解除武装，无论正文为何。
        diff = make_diff(text='user_edit')
        diff.arm_baseline('/tmp/base.tex')
        diff._last_compared_text = hash('edited')
        diff.document._dirty_line_ranges = [(3, 5)]
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()
        self.assertEqual(diff.document.preview.results, [None])
        self.assertIsNone(diff.baseline_path)
        self.assertIsNone(diff._last_compared_text)

    def test_user_edit_without_result_disarms_quietly(self):
        diff = make_diff(text='user_edit')
        diff.arm_baseline('/tmp/base.tex')
        diff.document._dirty_line_ranges = [(3, 5)]
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()
        self.assertEqual(diff.document.preview.results, [])
        self.assertIsNone(diff.baseline_path)

    def test_agent_edit_after_result_recompares(self):
        # 结果已出，正文又变但脏行为空 → agent 又编辑了一轮：重跑 compare
        # 更新结果，绝不能按"用户手改"处理（agent 分多轮编辑是常态）。
        diff = make_diff(text='ai_edit_2')
        diff.arm_baseline('/tmp/base.tex')
        diff._last_compared_text = hash('ai_edit_1')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(diff.baseline_path, '/tmp/base.tex')

    def test_new_text_spawns_query_thread(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        args = thread.call_args
        self.assertEqual(args.kwargs['target'], diff._query_and_show)
        # 线程参数：session 与文本 hash。
        self.assertEqual(args.kwargs['args'][1], hash('ai_edit'))

    def test_no_session_does_nothing(self):
        diff = AiDiff(FakeDocument(text='ai_edit'), FakeBuildSystem(session=None))
        diff.arm_baseline('/tmp/base.tex')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_running_query_coalesces(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        diff._running = True
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()
        self.assertTrue(diff._stale)


class QueryAndShowTest(unittest.TestCase):
    '''_query_and_show 的结果构建与交付。'''

    def _run_query(self, diff, session):
        with mock.patch.object(ai_diff_module, 'GLib') as glib:
            diff._query_and_show(session, hash('ai_edit'))
            return glib

    def test_success_delivers_result(self):
        payload = {
            'pdf': '/tmp/.phitex/compare/main-diff.pdf', 'pages': 3,
            'changes': [
                {'n': 1, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4],
                 'file': 'main.tex', 'line': 9, 'old_text': '', 'new_text': 'x'},
                {'n': 2, 'kind': 'del', 'page': None, 'bbox': None,
                 'file': 'main.tex', 'line': 3, 'old_text': 'y', 'new_text': ''},
            ],
            'errors': ['main.tex:12: Oops'],
        }
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        session = FakeSession(stdout=json.dumps(payload))
        glib = self._run_query(diff, session)
        glib.idle_add.assert_called_once()
        result = glib.idle_add.call_args.args[1]
        self.assertEqual(result['change_count'], 2)
        self.assertEqual(result['pages'], 3)
        # 一个有定位（矩形换算过），一个 page/bbox 全 null（被跳过）。
        self.assertEqual(len(result['rectangles']), 1)
        self.assertEqual(result['errors'], ['main.tex:12: Oops'])
        # 交付端预览真收得到（_show 走 set_diff_result）。_show 返回值恒为
        # False——GLib.idle_add 的一次性回调约定，效果以 preview 收到的为准。
        diff._show(result)
        self.assertEqual(diff.document.preview.results[-1], result)
        self.assertEqual(diff._last_compared_text, hash('ai_edit'))
        # baseline_path 被线程读到（compare 的入参）。
        self.assertEqual(session.compare_calls[0][0], '/tmp/base.tex')

    def test_request_failure_delivers_none_and_keeps_guard(self):
        stdout = json.dumps({'error': 'no phitex watch runs here', 'status': 3})
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        glib = self._run_query(diff, FakeSession(returncode=3, stdout=stdout))
        result = glib.idle_add.call_args.args[1]
        self.assertIsNone(result)
        # 失败不标记 _last_compared_text：下一轮构建还会再试。
        self.assertIsNone(diff._last_compared_text)

    def test_garbage_stdout_delivers_none(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        glib = self._run_query(diff, FakeSession(returncode=0, stdout='total garbage'))
        self.assertIsNone(glib.idle_add.call_args.args[1])

    def test_zero_changes_delivers_none_but_records_compared_text(self):
        # 只动了注释/空白：compare 成功但没东西可审阅 → 不亮档位、不通知。
        # 仍要记下"这段文本已经比过了"，否则每次自动构建都白跑一遍 compare。
        payload = {'pdf': '/tmp/x-diff.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        glib = self._run_query(diff, FakeSession(returncode=0, stdout=json.dumps(payload)))
        self.assertIsNone(glib.idle_add.call_args.args[1])
        self.assertEqual(diff._last_compared_text, hash('ai_edit'))

    def test_shutdown_suppresses_delivery(self):
        payload = {'pdf': '/tmp/x.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        diff._shutdown = True
        with mock.patch.object(ai_diff_module, 'GLib') as glib:
            diff._query_and_show(FakeSession(stdout=json.dumps(payload)), hash('ai_edit'))
        glib.idle_add.assert_not_called()

    def test_stale_reruns_after_finish(self):
        payload = {'pdf': '/tmp/x.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex')
        diff._stale = True
        # 模拟时序：compare 在跑期间文档又变了一轮（build N+1）。补跑读到的
        # 是新文本，hash ≠ 刚标记的 _last_compared_text → 应再起一次查询。
        diff.document.text = 'ai_edit_2'
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread, \
             mock.patch.object(ai_diff_module, 'GLib'):
            diff._query_and_show(FakeSession(stdout=json.dumps(payload)), hash('ai_edit'))
            # 查询结束后 _stale 触发一次补跑（diff_after_build 再起线程）。
            self.assertEqual(thread.call_count, 1)


class StopCompareTest(unittest.TestCase):

    def test_stop_runs_in_background(self):
        diff = make_diff(text='x')
        session = FakeSession()
        diff.build_system = FakeBuildSystem(session)
        with mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.stop_compare()
        thread.assert_called_once()
        thread.call_args.kwargs['target']()

    def test_stop_without_session_is_noop(self):
        diff = AiDiff(FakeDocument(), FakeBuildSystem(session=None))
        diff.stop_compare()  # 不抛异常即通过


class WiringGuardTest(unittest.TestCase):
    '''接线守护：diff 查询必须挂在 is_phitex_live_build 分支内
    （同 ChangeHighlight 的 LivePathOnlyTest 范式）。'''

    def test_diff_after_build_in_phitex_live_branch(self):
        source = (ROOT / 'setzer/document/build_system/build_system.py').read_text(
            encoding='utf-8')
        marker = 'self.ai_diff.diff_after_build()'
        position = source.find(marker)
        self.assertNotEqual(position, -1,
                            'build_system 必须调用 ai_diff.diff_after_build')
        # 调用点之前 700 字符内必须出现 live 门控——防止哪天被挪到
        # 无条件路径（传统引擎每次构建都整份重编，compare 毫无意义）。
        window = source[max(0, position - 700):position]
        self.assertIn('is_phitex_live_build()', window)

    def test_shutdown_calls_ai_diff(self):
        source = (ROOT / 'setzer/document/build_system/build_system.py').read_text(
            encoding='utf-8')
        self.assertIn('self.ai_diff.shutdown()', source)

    def test_all_three_ai_entries_snapshot_baseline(self):
        # 三个 AI 发送入口都要武装基线（漏一个就是"这个入口改了不显示 diff"）。
        for path, needle in (
                ('setzer/ai_fix/annotation_sender.py', 'snapshot_ai_baseline'),
                ('setzer/dialogs/build_log/build_log_dialog_controller.py',
                 'snapshot_ai_baseline'),
                ('setzer/workspace/headerbar/headerbar.py', 'snapshot_ai_baseline')):
            source = (ROOT / path).read_text(encoding='utf-8')
            self.assertIn(needle, source, path)


if __name__ == '__main__':
    unittest.main()

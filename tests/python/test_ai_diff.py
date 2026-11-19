#!/usr/bin/env python3
# coding: utf-8
#
# AiDiff（AI 编辑后的 PDF diff 显示）的门控状态机与接线。
#
# 状态机的两条硬约束：
#   - 自动构建会在 AI 编辑后连跑好几轮，同一文本绝不能重复 compare；
#   - 持续 diff 语义：diff 永远是"基线 → 当前正文"，人工修改也要出现在
#     diff 里（旧结果只被新结果覆盖，绝不因用户手改被清走）；手改只切换
#     通知措辞（_human_edited）。结束审阅只走手动按钮或新一轮 AI 发送。
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

    def get_filename(self):
        return '/tmp/e2e-diff/main.tex'


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
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=False), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_text_equal_baseline_clears_stale_result(self):
        diff = make_diff(text='original')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._last_compared_text = hash('edited')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=True), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True):
            diff.diff_after_build()
        # 旧结果被清（None 送达 preview）；基线保留武装——agent 之后若再改
        # 磁盘，仍按同一基线 diff。
        self.assertEqual(diff.document.preview.results, [None])
        self.assertIsNone(diff._last_compared_text)
        self.assertEqual(diff.baseline_ref, '/tmp/base.tex')

    def test_text_equal_baseline_with_no_result_keeps_quiet(self):
        diff = make_diff(text='original')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=True), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True):
            diff.diff_after_build()
        # agent 还没改：不清（保留可能存在的上一轮结果——arm 已归零标记）。
        self.assertEqual(diff.document.preview.results, [])

    def test_same_text_skips_repeat_compare(self):
        diff = make_diff(text='edited')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._last_compared_text = hash('edited')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_user_edit_marks_flag_and_still_compares(self):
        # 持续 diff 语义：用户在 buffer 里键入（脏行非空）只切换通知措辞
        # （_human_edited），**不**拦 compare——diff 永远是"基线 → 当前正文"，
        # 人工修改也要出现在里面，否则只看 diff 视图的人会误以为 AI 的版本
        # 还是现状。曾冻结过一版（手改即停），旧的 AI-only diff 比混合视图
        # 更误导，故撤销。
        diff = make_diff(text='user_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._last_compared_text = hash('edited')
        diff.document._dirty_line_ranges = [(3, 5)]
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(diff._human_edited)
        self.assertTrue(diff.human_edited_since_baseline)
        self.assertIsNotNone(diff.baseline_ref)
        # 旧结果没被抢先清掉：新结果送达时自然覆盖。
        self.assertEqual(diff.document.preview.results, [])

    def test_user_edit_without_prior_result_compares_too(self):
        # 手改是 diff 的合法内容来源之一：没有旧结果也一样要 compare。
        diff = make_diff(text='user_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff.document._dirty_line_ranges = [(3, 5)]
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(diff.human_edited_since_baseline)

    def test_no_user_edit_keeps_flag_false(self):
        # 纯 agent 编辑（脏行为空——磁盘重载不进 _dirty_line_ranges）：
        # 措辞仍是"AI edited"。
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        with mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread'):
            diff.diff_after_build()
        self.assertFalse(diff.human_edited_since_baseline)

    def test_arm_baseline_clears_human_edited(self):
        # 新一轮 AI 发送 = 新基线 = 新起点：上一轮的措辞标记作废。
        diff = make_diff(text='anything')
        diff._human_edited = True
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        self.assertFalse(diff.human_edited_since_baseline)

    def test_end_review_disarms_and_clears_file_snapshot(self):
        diff = make_diff(text='x')
        diff.arm_baseline('/tmp/proj/.neosetzer/ai-baseline/main.tex', 'baseline text')
        diff._human_edited = True
        with mock.patch.object(phitex_compare, 'clear_baseline') as clear, \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.end_review()
        clear.assert_called_once_with(
            '/tmp/proj/.neosetzer/ai-baseline', 'main.tex')
        thread.assert_called_once()  # stop_compare 后台跑
        self.assertIsNone(diff.baseline_ref)
        self.assertIsNone(diff.baseline_text)
        self.assertFalse(diff.human_edited_since_baseline)

    def test_end_review_git_channel_skips_file_cleanup(self):
        # git 通道的基线是仓库里不可达的提交 sha（不是路径），没有文件可清。
        diff = make_diff(text='x')
        diff.arm_baseline('0123abcd' * 5, 'baseline text')
        with mock.patch.object(phitex_compare, 'clear_baseline') as clear, \
             mock.patch.object(ai_diff_module.threading, 'Thread'):
            diff.end_review()
        clear.assert_not_called()
        self.assertIsNone(diff.baseline_ref)

    def test_disarm_is_silent(self):
        # Git 页发起 HEAD diff 前的解除武装：清基线但**不**停 JOB-diff
        # （紧接着的 git compare 要用它）、不删快照文件。
        diff = make_diff(text='x')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._last_compared_text = hash('x')
        diff._human_edited = True
        with mock.patch.object(phitex_compare, 'clear_baseline') as clear, \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.disarm()
        clear.assert_not_called()
        thread.assert_not_called()  # 不 stop_compare
        self.assertIsNone(diff.baseline_ref)
        self.assertIsNone(diff.baseline_text)
        self.assertIsNone(diff._last_compared_text)
        self.assertFalse(diff.human_edited_since_baseline)

    def test_agent_edit_after_result_recompares(self):
        # 结果已出，正文又变但脏行为空 → agent 又编辑了一轮：重跑 compare
        # 更新结果，绝不能按"用户手改"处理（agent 分多轮编辑是常态）。
        diff = make_diff(text='ai_edit_2')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._last_compared_text = hash('ai_edit_1')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        self.assertEqual(diff.baseline_ref, '/tmp/base.tex')

    def test_new_text_spawns_query_thread(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        self.assertEqual(thread.call_count, 1)
        args = thread.call_args
        self.assertEqual(args.kwargs['target'], diff._query_and_show)
        # 线程参数：session、文本 hash、正式 PDF 路径。
        self.assertEqual(args.kwargs['args'][1], hash('ai_edit'))
        self.assertIsNone(args.kwargs['args'][2])

    def test_no_session_does_nothing(self):
        diff = AiDiff(FakeDocument(text='ai_edit'), FakeBuildSystem(session=None))
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread:
            diff.diff_after_build()
        thread.assert_not_called()

    def test_running_query_coalesces(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
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

    def _run_query(self, diff, session, pdf_filename=None):
        with mock.patch.object(ai_diff_module, 'GLib') as glib:
            diff._query_and_show(session, hash('ai_edit'), pdf_filename)
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
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
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
        # baseline_ref 被线程读到（compare 的入参）。
        self.assertEqual(session.compare_calls[0][0], '/tmp/base.tex')

    def test_request_failure_delivers_none_and_keeps_guard(self):
        stdout = json.dumps({'error': 'no phitex watch runs here', 'status': 3})
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        glib = self._run_query(diff, FakeSession(returncode=3, stdout=stdout))
        result = glib.idle_add.call_args.args[1]
        self.assertIsNone(result)
        # 失败不标记 _last_compared_text：下一轮构建还会再试。
        self.assertIsNone(diff._last_compared_text)

    def test_garbage_stdout_delivers_none(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        glib = self._run_query(diff, FakeSession(returncode=0, stdout='total garbage'))
        self.assertIsNone(glib.idle_add.call_args.args[1])

    def test_zero_changes_delivers_none_but_records_compared_text(self):
        # 只动了注释/空白：compare 成功但没东西可审阅 → 不亮档位、不通知。
        # 仍要记下"这段文本已经比过了"，否则每次自动构建都白跑一遍 compare。
        payload = {'pdf': '/tmp/x-diff.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        glib = self._run_query(diff, FakeSession(returncode=0, stdout=json.dumps(payload)))
        self.assertIsNone(glib.idle_add.call_args.args[1])
        self.assertEqual(diff._last_compared_text, hash('ai_edit'))

    def test_shutdown_suppresses_delivery(self):
        payload = {'pdf': '/tmp/x.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._shutdown = True
        with mock.patch.object(ai_diff_module, 'GLib') as glib:
            diff._query_and_show(FakeSession(stdout=json.dumps(payload)), hash('ai_edit'), '/tmp/e2e-diff/main.pdf')
        glib.idle_add.assert_not_called()

    def test_stale_reruns_after_finish(self):
        payload = {'pdf': '/tmp/x.pdf', 'pages': 1, 'changes': [], 'errors': []}
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        diff._stale = True
        # 模拟时序：compare 在跑期间文档又变了一轮（build N+1）。补跑读到的
        # 是新文本，hash ≠ 刚标记的 _last_compared_text → 应再起一次查询。
        diff.document.text = 'ai_edit_2'
        with mock.patch.object(phitex_compare, 'baseline_matches', return_value=False), \
             mock.patch.object(ai_diff_module.PhitexSession, 'supports_compare',
                               return_value=True), \
             mock.patch.object(ai_diff_module.threading, 'Thread') as thread, \
             mock.patch.object(ai_diff_module, 'GLib'):
            diff._query_and_show(FakeSession(stdout=json.dumps(payload)), hash('ai_edit'), '/tmp/e2e-diff/main.pdf')
            # 查询结束后 _stale 触发一次补跑（diff_after_build 再起线程）。
            self.assertEqual(thread.call_count, 1)


class FailedResultTest(unittest.TestCase):
    # diff 构建自己编译失败：papilot 原则——报失败，绝不出"截断的成功"。

    def _run(self, payload, pdf_filename=None):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        session = FakeSession(returncode=0, stdout=json.dumps(payload))
        with mock.patch.object(ai_diff_module, 'GLib') as glib:
            diff._query_and_show(session, hash('ai_edit'), pdf_filename)
            return diff, glib

    def test_missing_pdf_marks_failed(self):
        payload = {'pdf': None, 'pages': None,
                   'changes': [{'n': 1, 'kind': 'add', 'page': None, 'bbox': None,
                                'file': 'main.tex', 'line': 3,
                                'old_text': '', 'new_text': 'x'}],
                   'errors': ['main.tex:7: Undefined control sequence']}
        diff, glib = self._run(payload)
        result = glib.idle_add.call_args.args[1]
        self.assertIsNotNone(result)
        self.assertTrue(result['failed'])
        self.assertEqual(result['errors'], ['main.tex:7: Undefined control sequence'])
        # 失败也算"比过了"，不重跑。
        self.assertEqual(diff._last_compared_text, hash('ai_edit'))

    def test_failed_result_skips_flash(self):
        payload = {'pdf': None, 'pages': None, 'changes': [], 'errors': ['boom']}
        diff, glib = self._run(payload, pdf_filename='/tmp/e2e-diff/main.pdf')
        # 失败结果不触发 synctex flash：idle_add 只有交付一次。
        self.assertEqual(glib.idle_add.call_count, 1)


class FlashOnResultPdfTest(unittest.TestCase):
    # compare 的 file:line → 正式 PDF 矩形闪烁（ papilot 的 SyncTeX 关联思路）。

    PAYLOAD = {'pdf': '/tmp/e2e-diff/.phitex/compare/main-diff.pdf', 'pages': 1,
               'changes': [
                   {'n': 1, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4],
                    'file': 'main.tex', 'line': 7, 'old_text': '', 'new_text': 'x'},
                   {'n': 2, 'kind': 'add', 'page': 1, 'bbox': [5, 6, 7, 8],
                    'file': 'sec.tex', 'line': 2, 'old_text': '', 'new_text': 'y'},
               ],
               'errors': []}

    def test_flash_queries_synctex_and_delivers_merged(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        session = FakeSession(returncode=0, stdout=json.dumps(self.PAYLOAD))
        rects = [{'page': 1, 'h': 10.0, 'v': 20.0, 'width': 5.0, 'height': 2.0}]
        with mock.patch.object(ai_diff_module, 'GLib') as glib, \
             mock.patch('setzer.helpers.synctex_query.query_rectangles',
                        return_value=rects) as query, \
             mock.patch('setzer.document.change_highlight.change_highlight.'
                        'merge_rectangles', side_effect=lambda r: r):
            diff._query_and_show(session, hash('ai_edit'), '/tmp/e2e-diff/main.pdf')
        # 两次 idle_add：交付结果 + 闪矩形。
        self.assertEqual(glib.idle_add.call_count, 2)
        # 反查用 change 的 file 解析出的绝对路径 + 本次构建的 PDF。
        first_call = query.call_args_list[0]
        self.assertEqual(first_call.args[0], '/tmp/e2e-diff/main.tex')
        self.assertEqual(first_call.args[1], '/tmp/e2e-diff/main.pdf')
        self.assertEqual(first_call.args[2], 7)
        # 交付的是结果 dict，闪的是矩形列表。
        self.assertIsInstance(glib.idle_add.call_args_list[1].args[1], list)

    def test_flash_capped_at_max_changes(self):
        payload = dict(self.PAYLOAD)
        payload['changes'] = [
            {'n': i, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4],
             'file': 'main.tex', 'line': i, 'old_text': '', 'new_text': 'x'}
            for i in range(1, 12)]
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        session = FakeSession(returncode=0, stdout=json.dumps(payload))
        with mock.patch.object(ai_diff_module, 'GLib'), \
             mock.patch('setzer.helpers.synctex_query.query_rectangles',
                        return_value=[]) as query:
            diff._query_and_show(session, hash('ai_edit'), '/tmp/e2e-diff/main.pdf')
        self.assertEqual(query.call_count, ai_diff_module.MAX_FLASH_CHANGES)

    def test_synctex_failure_is_silent(self):
        diff = make_diff(text='ai_edit')
        diff.arm_baseline('/tmp/base.tex', 'baseline text')
        session = FakeSession(returncode=0, stdout=json.dumps(self.PAYLOAD))
        with mock.patch.object(ai_diff_module, 'GLib'), \
             mock.patch('setzer.helpers.synctex_query.query_rectangles',
                        side_effect=RuntimeError('boom')):
            # 不抛异常即通过：定位是锦上添花，失败不影响结果交付。
            diff._query_and_show(session, hash('ai_edit'), '/tmp/e2e-diff/main.pdf')



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
        marker = 'self.ai_diff.diff_after_build(pdf_filename)'
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

    def test_end_review_wiring(self):
        # 手动结束的链路：工具栏按钮 → 面板 presenter → Preview.end_diff_review
        # → AiDiff.end_review。断一环，"结束审阅"就是死按钮。
        preview_source = (ROOT / 'setzer/document/preview/preview.py').read_text(
            encoding='utf-8')
        self.assertIn('def end_diff_review', preview_source)
        self.assertIn('end_review()', preview_source)
        view_source = (ROOT / 'setzer/workspace/preview_panel/preview_panel_viewgtk.py').read_text(
            encoding='utf-8')
        self.assertIn('diff_end_button', view_source)
        self.assertIn('set_diff_end_visible', view_source)
        presenter_source = (ROOT / 'setzer/workspace/preview_panel/'
                            'preview_panel_presenter.py').read_text(encoding='utf-8')
        self.assertIn('on_diff_end_requested', presenter_source)
        self.assertIn('end_diff_review()', presenter_source)

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

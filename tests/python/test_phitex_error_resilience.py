#!/usr/bin/env python3
# coding: utf-8
#
# PhiTeX 报错不得打断实时编译（"报错只是输入中间态"）。
#
# 症状是"报错后自动编译彻底停住，只能手动 F5"。整条链上任何一个环节把
# build_state 留在 building_in_progress，下一轮自动编译就会被判成"文档正忙"，
# PhiTeX 的 150ms 节奏下等于实时预览停摆。这里把四层各自钉住：
#   1. worker 抛异常也必须调度 _on_query_done（状态窗口的唯一出口）
#   2. 没有任何结果的构建必须回落 idle；recover_if_stalled 自愈僵尸态
#   3. AutoBuild 在 PhiTeX 下永不丢弃 pending 构建
#   4. 引擎层：文档报错 ≠ 引擎不可用，session 不拆、不退回冷启动
# 另加两条"报错期零打扰"：不弹模态框、结果 toast 不再连发。
#
# 断言全部走 ast 提取 + exec 隔离（python 测试不能 import 带 gi 的顶层模块），
# 与 test_frequent_build_ui_feedback.py / test_phitex_build_command.py 同范式。

import ast
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BUILD_SYSTEM = 'setzer/document/build_system/build_system.py'
BUILDER = 'setzer/document/build_system/builder/builder_build_latex.py'
AUTO_BUILD = 'setzer/workspace/auto_build/auto_build.py'
BUILD_WIDGET = 'setzer/document/build_widget/build_widget.py'

import setzer.document.build_system.phitex_session.phitex_session as ps


def load_methods(relative_path, class_name, method_names, namespace):
    '''把类里的若干方法抽出来 exec 到隔离 namespace，返回 SimpleNamespace。'''
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    wanted = [node for node in cls.body if isinstance(node, ast.FunctionDef)
              and node.name in method_names]
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source), 'exec'),
         namespace)
    return SimpleNamespace(**{node.name: namespace[node.name] for node in wanted})


class FakeQuery:
    def __init__(self, jobs=('build_latex',)):
        self.tex_filename = '/proj/main.tex'
        self.jobs = list(jobs)
        self.force_building_to_stop = False
        self.build_data = {'additional_arguments': '', 'enable_synctex': True,
                           'latex_interpreter': 'phitex'}
        self.build_result_lock = threading.Lock()
        self.build_result = None
        self.forward_sync_result = None
        self.backward_sync_result = None
        self.log_messages = dict()
        self.bibtex_log_messages = {'error': [], 'warning': [], 'badbox': []}
        self.error_count = 0
        self.can_sync = False

    def get_build_result(self):
        return self.build_result

    def get_forward_sync_result(self):
        return self.forward_sync_result

    def get_backward_sync_result(self):
        return self.backward_sync_result


class FakeState:
    '''把 change_build_state / show_build_state 接到一个可变 holder 上。

    替身必须把状态写回被测对象本身（而不是 holder），否则断言永远读到初值
    ——上一版测试就是这么被"绿"过去的：状态其实改了，只是改在别处。
    '''

    def __init__(self, build_system, build_state='building_in_progress'):
        self.build_system = build_system
        self.build_system.build_state = build_state
        self.build_system.state_changes = list()
        self.build_system.show_build_state = mock.Mock()

    def change_build_state(self, state):
        self.build_system.build_state = state
        self.build_system.state_changes.append(state)


class Doc:
    '''文档替身：要能当字典键（SimpleNamespace 是不可哈希的）。'''

    def __init__(self):
        self.source_buffer = mock.Mock()


class ExecuteQueryTest(unittest.TestCase):
    '''F1：worker 抛异常时 _on_query_done 仍必须被调度。'''

    def setUp(self):
        self.GLib = mock.Mock()
        self.fns = load_methods(BUILD_SYSTEM, 'BuildSystem', {'execute_query'},
                                {'GLib': self.GLib, 'sys': sys,
                                 'traceback': mock.Mock()})

    def run_query(self, builder):
        query = FakeQuery()
        build_system = SimpleNamespace(
            active_query=query,
            builders={'build_latex': builder},
            _on_query_done=mock.Mock(),
            _emit_build_stage=mock.Mock())
        self.fns.execute_query(build_system, query)
        return build_system, query

    def scheduled_done(self, build_system, query):
        return mock.call(build_system._on_query_done, query) in \
            self.GLib.idle_add.call_args_list

    def test_worker_exception_still_schedules_the_done_callback(self):
        builder = mock.Mock()
        builder.run.side_effect = RuntimeError('boom')
        build_system, query = self.run_query(builder)
        self.assertTrue(self.scheduled_done(build_system, query))

    def test_normal_path_schedules_the_done_callback(self):
        build_system, query = self.run_query(mock.Mock())
        self.assertTrue(self.scheduled_done(build_system, query))


class QueryDoneTest(unittest.TestCase):
    '''F2：没有任何结果的构建必须回落 idle。'''

    def setUp(self):
        self.fns = load_methods(BUILD_SYSTEM, 'BuildSystem', {'_on_query_done'},
                                {'sys': sys})

    def test_resultless_build_resets_a_stuck_state(self):
        query = FakeQuery()
        build_system = SimpleNamespace(
            active_query=query,
            document=SimpleNamespace(filename='/proj/main.tex'),
            last_build_interpreter=None,
            parse_result=mock.Mock())
        state = FakeState(build_system)
        build_system.change_build_state = state.change_build_state
        self.fns._on_query_done(build_system, query)
        self.assertEqual(build_system.build_state, 'idle')
        self.assertIsNone(build_system.active_query)

    def test_result_goes_through_parse_result(self):
        query = FakeQuery()
        query.build_result = {'pdf_filename': None}
        build_system = SimpleNamespace(
            active_query=query, build_state='building_in_progress',
            last_build_interpreter=None,
            parse_result=mock.Mock(),
            change_build_state=mock.Mock(),
            show_build_state=mock.Mock())
        self.fns._on_query_done(build_system, query)
        build_system.parse_result.assert_called_once()
        # 收尾交给 parse_result；这里不再重复发 idle 通知。
        build_system.change_build_state.assert_not_called()


class ParseResultTest(unittest.TestCase):
    '''F3：结果解析抛异常也必须以 idle 收尾。'''

    def setUp(self):
        self.fns = load_methods(BUILD_SYSTEM, 'BuildSystem', {'parse_result'},
                                {'sys': sys, 'traceback': mock.Mock()})

    def build_system(self, consume):
        build_system = SimpleNamespace(
            document=SimpleNamespace(filename='/proj/main.tex'),
            _consume_build_result=consume)
        state = FakeState(build_system)
        build_system.change_build_state = state.change_build_state
        return build_system

    def test_exception_in_consumption_still_ends_idle(self):
        build_system = self.build_system(mock.Mock(side_effect=ValueError('bad log')))
        self.fns.parse_result(build_system, {'build': {}, 'forward_sync': None,
                                            'backward_sync': None})
        self.assertEqual(build_system.build_state, 'idle')

    def test_early_return_branches_do_not_double_notify(self):
        '''interpreter_missing / not_working 已自行置 idle，finally 不该再发一次。'''
        def consume(_blob):
            build_system.change_build_state('idle')

        build_system = self.build_system(mock.Mock(side_effect=consume))
        self.fns.parse_result(build_system, {'build': {}, 'forward_sync': None,
                                            'backward_sync': None})
        self.assertEqual(build_system.state_changes, ['idle'])


class RecoverIfStalledTest(unittest.TestCase):
    '''F4：僵尸态用不变式自愈（busy 且没有任何 query 拥有它）。'''

    def setUp(self):
        self.fns = load_methods(BUILD_SYSTEM, 'BuildSystem',
                                {'recover_if_stalled'}, {'sys': sys})

    def build_system(self, state='building_in_progress', active_query=None,
                     builders=None):
        build_system = SimpleNamespace(
            active_query=active_query,
            builders=builders if builders is not None else dict(),
            document=SimpleNamespace(filename='/proj/main.tex'))
        holder = FakeState(build_system, state)
        build_system.change_build_state = holder.change_build_state
        return build_system

    def test_stuck_busy_state_is_reset(self):
        build_system = self.build_system()
        self.assertTrue(self.fns.recover_if_stalled(build_system))
        self.assertEqual(build_system.build_state, 'idle')

    def test_live_build_is_left_alone(self):
        build_system = self.build_system(active_query=FakeQuery())
        self.assertFalse(self.fns.recover_if_stalled(build_system))
        self.assertEqual(build_system.build_state, 'building_in_progress')

    def test_running_builder_process_is_left_alone(self):
        builders = {'build_latex': SimpleNamespace(process=mock.Mock())}
        build_system = self.build_system(builders=builders)
        self.assertFalse(self.fns.recover_if_stalled(build_system))

    def test_idle_is_not_touched(self):
        build_system = self.build_system(state='idle')
        self.assertFalse(self.fns.recover_if_stalled(build_system))
        self.assertEqual(build_system.state_changes, [])
        build_system.show_build_state.assert_not_called()


class AutoBuildNeverDropsTest(unittest.TestCase):
    '''F5：PhiTeX live 下忙重试没有"放弃"这一说。'''

    def setUp(self):
        self.fns = load_methods(AUTO_BUILD, 'AutoBuild', {'on_timer'},
                                {'_': lambda text: text})

    def auto_build(self, phitex_live, retries=99):
        document = Doc()
        target = SimpleNamespace(build_system=SimpleNamespace(
            get_build_state=lambda: 'building_in_progress',
            is_phitex_live_build=lambda: phitex_live,
            recover_if_stalled=mock.Mock(return_value=False),
            build_and_forward_sync=mock.Mock()))
        auto = SimpleNamespace(
            timers=dict(),
            _retry_counts={document: retries},
            MAX_RETRIES=10,
            PHITEX_RETRY_INTERVAL_MS=300,
            RETRY_INTERVAL_MS=1000,
            _auto_build_values=lambda doc: (True, 150),
            _show_toast=mock.Mock(),
            schedule_build=mock.Mock(),
            workspace=SimpleNamespace(
                open_documents=[document],
                get_magic_root_or_active_latex_document=lambda: target,
                get_active_document=lambda: document))
        return auto, document, target

    def test_phitex_keeps_the_pending_build_alive(self):
        auto, document, target = self.auto_build(phitex_live=True)
        self.fns.on_timer(auto, document)
        # 远超 MAX_RETRIES 仍重新排期，且退避到 1s 而不是放弃。
        auto.schedule_build.assert_called_once_with(document, auto.RETRY_INTERVAL_MS)
        auto._show_toast.assert_not_called()
        self.assertIn(document, auto._retry_counts)
        target.build_system.recover_if_stalled.assert_called_once_with()

    def test_phitex_still_retries_fast_while_under_the_threshold(self):
        auto, document, _ = self.auto_build(phitex_live=True, retries=0)
        self.fns.on_timer(auto, document)
        auto.schedule_build.assert_called_once_with(document, auto.PHITEX_RETRY_INTERVAL_MS)

    def test_other_engines_keep_the_give_up_path(self):
        auto, document, _ = self.auto_build(phitex_live=False)
        self.fns.on_timer(auto, document)
        auto.schedule_build.assert_not_called()
        auto._show_toast.assert_called_once()
        self.assertNotIn(document, auto._retry_counts)

    def test_stalled_state_is_recovered_before_the_busy_check(self):
        auto, document, target = self.auto_build(phitex_live=False)

        def recover():
            target.build_system.get_build_state = lambda: 'idle'
            return True

        target.build_system.recover_if_stalled = recover
        document.source_buffer.get_modified.return_value = False
        self.fns.on_timer(auto, document)
        # 自愈后不再走"文档正忙"，而是真的发起构建。
        target.build_system.build_and_forward_sync.assert_called_once_with(document)
        auto._show_toast.assert_not_called()


class FakeSession:
    '''run_via_phitex_session 用到的 session 面。'''

    def __init__(self, tex_filename, output_directory, enable_synctex=True,
                 additional_arguments=''):
        self.tex_filename = tex_filename
        self.output_directory = output_directory
        self.enable_synctex = enable_synctex
        self.additional_arguments = additional_arguments
        self.pid = 4242
        self.build_result = (True, dict())
        self.problems_result = list()
        self.alive_result = True

    @property
    def config_signature(self):
        return (self.tex_filename, self.output_directory, self.enable_synctex,
                self.additional_arguments)

    def start(self):
        return True

    def build(self, timeout=None):
        return self.build_result

    def problems(self):
        return self.problems_result

    def status(self):
        return self.build_result[1]

    def alive(self):
        return self.alive_result


class PhitexSessionBuildTest(unittest.TestCase):
    '''F6：文档报错 / problems 读不到都不得拆 session。'''

    def setUp(self):
        self.phitex = SimpleNamespace(
            PhitexSession=FakeSession,
            to_log_messages=ps.to_log_messages,
            build_succeeded=ps.build_succeeded)
        self.fns = load_methods(BUILDER, 'BuilderBuildLaTeX',
                                {'run_via_phitex_session', '_revive_phitex_session'},
                                {'phitex_session': self.phitex})

    def builder(self, session):
        holder = SimpleNamespace(phitex_session=session,
                                 copy_synctex_file=mock.Mock(return_value=False),
                                 _is_pdf_valid=mock.Mock(return_value=True),
                                 stop_phitex_session=mock.Mock())
        holder._revive_phitex_session = mock.Mock(return_value=None)
        return holder

    def run_build(self, session, query=None):
        query = query or FakeQuery()
        builder = self.builder(session)
        result = self.fns.run_via_phitex_session(builder, query, '/proj/out', True)
        return result, builder, query

    def test_document_errors_keep_the_session_and_report_the_problems(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.build_result = (True, {'errors': 1, 'ok': False, 'pdf': None,
                                       'root': '/proj'})
        session.problems_result = [{'kind': 'error', 'severity': 'error',
                                    'file': 'main.tex', 'line': 7,
                                    'message': 'Undefined control sequence'}]
        result, builder, query = self.run_build(session)
        self.assertTrue(result)
        builder.stop_phitex_session.assert_not_called()
        self.assertEqual(query.error_count, 1)
        self.assertIsNone(query.build_result['pdf_filename'])

    def test_missing_problem_list_falls_back_to_status_errors(self):
        '''problems 读不到时不能 return False —— 那会让调用方拆 session 退回冷启动。'''
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.build_result = (True, {'errors': 2, 'ok': False, 'pdf': None,
                                       'root': '/proj'})
        session.problems_result = None
        result, builder, query = self.run_build(session)
        self.assertTrue(result)
        builder.stop_phitex_session.assert_not_called()
        self.assertEqual(query.error_count, 1)          # 合成的"无问题列表"错误
        self.assertIsNone(query.build_result['pdf_filename'])

    def test_status_problems_are_used_when_available(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.build_result = (True, {'errors': 1, 'ok': False, 'pdf': None,
                                       'root': '/proj',
                                       'problems': [{'kind': 'error',
                                                     'severity': 'error',
                                                     'file': 'main.tex',
                                                     'line': 3,
                                                     'message': 'Boom'}]})
        session.problems_result = None
        result, _, query = self.run_build(session)
        self.assertTrue(result)
        self.assertEqual(query.error_count, 1)
        self.assertIn('Boom', query.build_result['log_messages']['/proj/main.tex']['error'][0][2])

    def test_unknown_status_is_not_reported_as_success(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.build_result = (True, dict())
        session.problems_result = None
        result, _, query = self.run_build(session)
        self.assertTrue(result)
        self.assertEqual(query.error_count, 1)

    def test_unusable_engine_still_falls_back(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.build_result = (False, None)
        result, builder, _ = self.run_build(session)
        self.assertFalse(result)
        builder._revive_phitex_session.assert_called_once()

    def test_revive_reuses_a_session_that_is_still_alive(self):
        '''wait 没等到不等于 watch 死了：重启会白丢整张增量图。'''
        session = FakeSession('/proj/main.tex', '/proj/out')
        holder = SimpleNamespace(phitex_session=session,
                                 stop_phitex_session=mock.Mock())
        revived = self.fns._revive_phitex_session(
            holder, FakeQuery(), '/proj/out', True, session)
        self.assertIs(revived, session)
        holder.stop_phitex_session.assert_not_called()

    def test_revive_rebuilds_a_dead_session(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.alive_result = False
        holder = SimpleNamespace(phitex_session=session,
                                 stop_phitex_session=mock.Mock())
        revived = self.fns._revive_phitex_session(
            holder, FakeQuery(), '/proj/out', True, session)
        self.assertIsInstance(revived, FakeSession)
        self.assertIsNot(revived, session)
        holder.stop_phitex_session.assert_called_once()
        self.assertIs(holder.phitex_session, revived)

    def test_revive_gives_up_when_the_engine_cannot_start(self):
        session = FakeSession('/proj/main.tex', '/proj/out')
        session.alive_result = False
        holder = SimpleNamespace(phitex_session=session,
                                 stop_phitex_session=mock.Mock())

        class DeadSession(FakeSession):
            def start(self):
                return False

        self.phitex.PhitexSession = DeadSession
        try:
            self.assertIsNone(self.fns._revive_phitex_session(
                holder, FakeQuery(), '/proj/out', True, session))
        finally:
            self.phitex.PhitexSession = FakeSession


class BuildSucceededTest(unittest.TestCase):
    '''status --json 的三态解读（实测 phitex 1.0.2 带 errors / ok）。'''

    def test_errors_field_wins(self):
        self.assertTrue(ps.build_succeeded({'errors': 0, 'ok': False}))
        self.assertFalse(ps.build_succeeded({'errors': 2, 'ok': True}))

    def test_ok_field_is_the_fallback(self):
        self.assertTrue(ps.build_succeeded({'ok': True}))
        self.assertFalse(ps.build_succeeded({'ok': False}))

    def test_dead_session_json_is_a_failure(self):
        self.assertFalse(ps.build_succeeded({'error': 'no phitex watch with pid 1 runs',
                                             'status': 3}))

    def test_unknown_shapes_are_none(self):
        for status in (None, {}, {'status': 3}):
            self.assertIsNone(ps.build_succeeded(status))


class SessionAliveTest(unittest.TestCase):
    '''alive() 用 `session status` 的退出码判定：0/1 活着，3 是没这个 watch。'''

    def session(self, returncode, pid=7):
        session = ps.PhitexSession('/proj/main.tex', None, False)
        session.pid = pid
        session._call = mock.Mock(return_value=(returncode, ''))
        return session

    def test_live_watch(self):
        self.assertTrue(self.session(0).alive())

    def test_watch_with_document_errors_is_alive(self):
        '''实测 status 在有错误的文档上返回 1：那是"报错了"，不是"死了"。'''
        self.assertTrue(self.session(1).alive())

    def test_dead_watch(self):
        self.assertFalse(self.session(3).alive())

    def test_unreachable_engine_is_not_alive(self):
        self.assertFalse(self.session(None).alive())

    def test_no_pid(self):
        session = self.session(0, pid=None)
        self.assertFalse(session.alive())
        session._call.assert_not_called()


class QuietErrorFeedbackTest(unittest.TestCase):
    '''F7/F8：报错期不弹模态框、结果 toast 不再连发。'''

    def setUp(self):
        self.dialog_locator = mock.Mock()
        self.fns = load_methods(BUILD_SYSTEM, 'BuildSystem',
                                {'_show_build_error_dialog'},
                                {'sys': __import__('sys'),
                                 'DialogLocator': self.dialog_locator})

    def build_system(self, phtiex_live, auto):
        return SimpleNamespace(is_auto_build=auto,
                               is_phitex_live_build=lambda: phtiex_live)

    def test_phitex_auto_build_does_not_steal_focus(self):
        self.fns._show_build_error_dialog(
            self.build_system(True, True), 'building_failed', 'output dir')
        self.dialog_locator.get_dialog.assert_not_called()

    def test_manual_build_still_reports(self):
        build_system = self.build_system(True, False)
        self.fns._show_build_error_dialog(build_system, 'interpreter_missing',
                                          'phitex')
        self.dialog_locator.get_dialog.assert_called_once_with('interpreter_missing')

    def test_other_engines_auto_build_still_reports(self):
        self.fns._show_build_error_dialog(
            self.build_system(False, True), 'building_failed', 'x')
        self.dialog_locator.get_dialog.assert_called_once_with('building_failed')

    def test_error_toasts_are_silent_but_recovery_is_announced(self):
        fns = load_methods(BUILD_WIDGET, 'BuildWidget',
                           {'_suppress_result_toast'}, {})
        build_system = mock.Mock()
        build_system.is_phitex_live_build.return_value = True
        build_system.is_auto_build = True
        widget = SimpleNamespace(_last_toasted_error_count=None)

        build_system.get_error_count.return_value = 2
        self.assertTrue(fns._suppress_result_toast(widget, build_system, 'error'))
        # 错误数变化也不再放行：输入中间态下 0/1/2 会来回跳。
        build_system.get_error_count.return_value = 3
        self.assertTrue(fns._suppress_result_toast(widget, build_system, 'error'))
        self.assertEqual(widget._last_toasted_error_count, 3)

        # 修好之后还是要说一声（"我改对了"是用户唯一关心的成功）。
        self.assertFalse(fns._suppress_result_toast(widget, build_system, 'success'))
        self.assertEqual(widget._last_toasted_error_count, 0)


if __name__ == '__main__':
    unittest.main()

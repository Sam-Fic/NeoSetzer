#!/usr/bin/env python3
# coding: utf-8
#
# 高频构建下的四类 UI 反馈抑制。
#
# PhiTeX live 强制自动构建、延迟 150ms，等于把"一次构建"从几分钟一次变成
# 每秒六次。原本被慢节奏掩护过去的反馈会当场变成干扰：结果 toast 叠成墙、
# 构建日志面板反复弹开、淡出循环变成常驻 60fps 全页重绘。这几条判定就是
# 那条因果链的落点，值得单独钉住。

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_method(relative_path, class_name, method_names, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    wanted = [node for node in cls.body if isinstance(node, ast.FunctionDef)
              and node.name in method_names]
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source), 'exec'), namespace)
    return SimpleNamespace(**{node.name: namespace[node.name] for node in wanted})


class ToastSuppressionTest(unittest.TestCase):
    '''F1：结果 toast 只在值得说的时候说。'''

    def setUp(self):
        self.fns = load_method(
            'setzer/document/build_widget/build_widget.py', 'BuildWidget',
            {'_suppress_result_toast'}, {})

    def build_system(self, live=True, auto=True, error_count=0):
        bs = mock.Mock()
        bs.is_phitex_live_build.return_value = live
        bs.is_auto_build = auto
        bs.get_error_count.return_value = error_count
        return bs

    def widget(self, last=None):
        return SimpleNamespace(_last_toasted_error_count=last)

    def test_other_engines_always_toast(self):
        widget = self.widget()
        self.assertFalse(self.fns._suppress_result_toast(
            widget, self.build_system(live=False), 'success'))
        self.assertFalse(self.fns._suppress_result_toast(
            widget, self.build_system(live=False), 'error'))

    def test_manual_builds_always_toast(self):
        widget = self.widget()
        self.assertFalse(self.fns._suppress_result_toast(
            widget, self.build_system(auto=False), 'success'))

    def test_repeated_success_is_silent(self):
        widget = self.widget(last=0)
        self.assertTrue(self.fns._suppress_result_toast(
            widget, self.build_system(), 'success'))

    def test_recovery_from_errors_still_announces_success(self):
        '''用户最关心的恰是"我改对了"，这一条不能哑。'''
        widget = self.widget(last=3)
        self.assertFalse(self.fns._suppress_result_toast(
            widget, self.build_system(), 'success'))
        self.assertEqual(widget._last_toasted_error_count, 0)

    def test_errors_are_always_silent(self):
        '''输入中间态下错误数会在 0/1/2 之间来回跳，按数字去重仍会连发。'''
        widget = self.widget(last=None)
        bs = self.build_system(error_count=2)
        self.assertTrue(self.fns._suppress_result_toast(widget, bs, 'error'))
        self.assertTrue(self.fns._suppress_result_toast(widget, bs, 'error'))
        self.assertTrue(self.fns._suppress_result_toast(widget, bs, 'error'))

    def test_changing_error_count_stays_silent_but_is_remembered(self):
        '''记住错误数是给"恢复成功"用的：那一句仍要说。'''
        widget = self.widget(last=1)
        self.assertTrue(self.fns._suppress_result_toast(
            widget, self.build_system(error_count=4), 'error'))
        self.assertEqual(widget._last_toasted_error_count, 4)
        self.assertFalse(self.fns._suppress_result_toast(
            widget, self.build_system(error_count=0), 'success'))

    def test_unrelated_messages_not_affected(self):
        self.assertFalse(self.fns._suppress_result_toast(
            self.widget(), self.build_system(), ''))


class FadeLoopBranchTest(unittest.TestCase):
    '''F3：连续高亮时不跑逐帧淡出。'''

    def presenter(self, live, fade_id=None, timeout_id=None, reduced=False):
        ns = {'animation_prefs': SimpleNamespace(is_reduced=lambda: reduced),
              'GLib': mock.Mock(timeout_add=mock.Mock(return_value=7))}
        fns = load_method('setzer/document/preview/preview_presenter.py',
                          'PreviewPresenter',
                          {'start_fade_loop', 'cancel_fade_loop'}, ns)
        presenter = mock.Mock()
        presenter._fade_loop_id = fade_id
        presenter._fade_timeout_id = timeout_id
        presenter._current_time_factor = 0
        presenter.highlight_duration = 1.5
        presenter.preview = SimpleNamespace(
            document=SimpleNamespace(
                build_system=SimpleNamespace(is_phitex_live_build=lambda: live)))
        presenter.cancel_fade_loop = lambda: None
        presenter.start_fade_loop = lambda: fns.start_fade_loop(presenter)
        return presenter, ns['GLib']

    def test_continuous_flashes_use_the_timeout_path(self):
        presenter, GLib = self.presenter(live=True, fade_id=3)
        presenter.start_fade_loop()
        GLib.timeout_add.assert_called_once()
        self.assertEqual(presenter._fade_timeout_id, 7)

    def test_first_flash_keeps_the_tick_animation(self):
        '''停手后的第一闪仍要有淡出——那是"我改完了"的收束感。'''
        presenter, GLib = self.presenter(live=True, fade_id=None)
        presenter.start_fade_loop()
        GLib.timeout_add.assert_not_called()
        presenter.view.drawing_area.add_tick_callback.assert_called_once()

    def test_other_engines_keep_the_tick_animation(self):
        presenter, GLib = self.presenter(live=False, fade_id=3)
        presenter.start_fade_loop()
        GLib.timeout_add.assert_not_called()
        presenter.view.drawing_area.add_tick_callback.assert_called_once()

    def test_missing_build_system_falls_back_to_tick(self):
        '''本类单测按需抽取方法 + 桩命名空间，拿不到引擎就保持原手感。'''
        presenter, GLib = self.presenter(live=True, fade_id=3)
        presenter.preview = SimpleNamespace()
        presenter.start_fade_loop()
        GLib.timeout_add.assert_not_called()

    def test_reduced_motion_still_wins(self):
        presenter, GLib = self.presenter(live=False, fade_id=None, reduced=True)
        presenter.start_fade_loop()
        GLib.timeout_add.assert_called_once()


class BuildLogPopupTest(unittest.TestCase):
    '''F2：PhiTeX live 的自动构建不弹日志面板（默认开关是"开"，会被反复触发）。'''

    def log(self, live, is_auto, autoshow_errors=True):
        fns = load_method('setzer/workspace/build_log/build_log.py', 'BuildLog',
                          {'update_items'}, {})
        build_log = mock.Mock()
        build_log.settings = SimpleNamespace(
            get_value=lambda section, key: autoshow_errors if key == 'auto_build_autoshow_errors' else 'errors')
        build_log.document = SimpleNamespace(
            build_system=SimpleNamespace(
                build_log_data={'items': []},
                is_auto_build=is_auto,
                is_phitex_live_build=lambda: live))
        build_log.has_items = lambda mode: True
        build_log.update_items = lambda just_built: fns.update_items(build_log, just_built)
        return build_log

    def test_phitex_live_auto_build_does_not_open_the_panel(self):
        build_log = self.log(live=True, is_auto=True)
        build_log.update_items(True)
        build_log.workspace.set_show_build_log.assert_not_called()

    def test_other_engine_auto_build_still_opens(self):
        build_log = self.log(live=False, is_auto=True)
        build_log.update_items(True)
        build_log.workspace.set_show_build_log.assert_called_once_with(True)

    def test_manual_build_still_opens(self):
        build_log = self.log(live=True, is_auto=False)
        build_log.update_items(True)
        build_log.workspace.set_show_build_log.assert_called_once_with(True)


class DelayAndRetryTest(unittest.TestCase):
    '''F4：重试节奏跟着构建节奏走。'''

    def test_retry_interval_is_shorter_for_phitex(self):
        source = (ROOT / 'setzer/workspace/auto_build/auto_build.py').read_text(encoding='utf-8')
        self.assertIn('PHITEX_RETRY_INTERVAL_MS = 300', source)
        # 不能无条件用 1000ms 那个常数，否则 PhiTeX 下的撞车仍要等一秒。
        self.assertIn('if phitex_live:', source)
        # "放弃"必须只属于传统引擎：PhiTeX 报错是输入中间态，不能被丢弃。
        self.assertIn('if not phitex_live and count > self.MAX_RETRIES:', source)

    def test_deferred_toast_fires_once_per_busy_episode(self):
        source = (ROOT / 'setzer/workspace/auto_build/auto_build.py').read_text(encoding='utf-8')
        # count==1 只在每个"忙"片段开头成立；PhiTeX 下这条提示整个不发。
        self.assertIn('if count == 1 and not phitex_live:', source)


if __name__ == '__main__':
    unittest.main()

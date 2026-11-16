#!/usr/bin/env python3
# coding: utf-8
#
# 构建按钮计时器的三态策略。
#
# auto 的判据是"这次构建值不值得看"，而计时器的显示粒度就是一秒——不足一秒
# 的构建里那个数字永远停在 0:00，是个不含信息的摆设。所以规则只看时长，不看
# 用的是哪个引擎：这样 build_widget 也不用再依赖 phitex_session。

import ast
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
WIDGET = 'setzer/document/build_widget/build_widget.py'


def load_widget_method(method_name, namespace=None):
    source = ROOT / WIDGET
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'BuildWidget')
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    ns = {'time': time, 'GLib': mock.Mock()}
    if namespace:
        ns.update(namespace)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), ns)
    return ns[method_name]


class PolicyTest(unittest.TestCase):

    def setUp(self):
        self.policy = load_widget_method('_build_timer_policy')

    def widget(self, mode):
        obj = mock.Mock()
        obj.settings.get_value.return_value = mode
        return obj

    def test_default_is_reveal(self):
        self.assertEqual(self.policy(self.widget('auto')), 'reveal')

    def test_explicit_choices_override(self):
        self.assertEqual(self.policy(self.widget('always')), 'now')
        self.assertEqual(self.policy(self.widget('never')), 'off')

    def test_unknown_value_falls_back_to_reveal(self):
        # 手改设置文件写了未定义的值时不该抛异常，按默认策略走。
        self.assertEqual(self.policy(self.widget('banana')), 'reveal')

    def test_threshold_is_the_display_granularity(self):
        source = (ROOT / WIDGET).read_text(encoding='utf-8')
        self.assertIn('AUTO_REVEAL_MS = 1000', source)

    def test_timer_logic_does_not_depend_on_phitex(self):
        source = (ROOT / WIDGET).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)
                 and 'timer' in n.name.lower()]
        for name in names:
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == name)
            self.assertNotIn('is_phitex_live_build', ast.dump(fn), name)


class ApplyPolicyTest(unittest.TestCase):

    def setUp(self):
        self.GLib = mock.Mock()
        self.GLib.timeout_add.return_value = 77
        self.apply = load_widget_method('_apply_build_timer_policy',
                                        {'GLib': self.GLib})
        # _apply_build_timer_policy 会调 _build_timer_policy；桩对象必须给回
        # 真实的策略字符串，否则拿到 Mock、三条分支全部落到 else。
        self.policy = load_widget_method('_build_timer_policy')

    def widget(self, mode):
        obj = mock.Mock()
        obj.settings.get_value.return_value = mode
        obj._timer_reveal_id = None
        obj.AUTO_REVEAL_MS = 1000
        obj.cancel_timer_reveal = lambda: None
        obj.view.timer_active = False
        obj._build_timer_policy = lambda: self.policy(obj)
        return obj

    def test_now_starts_the_timer_immediately(self):
        obj = self.widget('always')
        self.apply(obj)
        obj.view.start_timer.assert_called_once()
        obj.view.set_timer_visible.assert_called_with(True)
        self.GLib.timeout_add.assert_not_called()

    def test_reveal_hides_it_and_schedules_the_threshold(self):
        obj = self.widget('auto')
        self.apply(obj)
        obj.view.set_timer_visible.assert_called_with(False)
        obj.view.start_timer.assert_not_called()
        self.GLib.timeout_add.assert_called_once_with(1000, obj._reveal_timer)

    def test_off_schedules_nothing(self):
        obj = self.widget('never')
        self.apply(obj)
        obj.view.set_timer_visible.assert_called_with(False)
        self.GLib.timeout_add.assert_not_called()


class RevealTest(unittest.TestCase):

    def setUp(self):
        self.reveal = load_widget_method('_reveal_timer')

    def widget(self, state, elapsed_ms, active=False):
        obj = mock.Mock()
        obj.build_button_state = (state, int(time.time() * 1000) - elapsed_ms)
        obj._timer_reveal_id = 77
        obj.view.timer_active = active
        obj.view.timer = 0
        return obj

    def test_seeds_the_real_elapsed_time_not_zero(self):
        '''到点现身时应显示真实已用时，第一格就是 0:01 而不是重新开始数。'''
        obj = self.widget('building', 1000)
        self.reveal(obj)
        self.assertGreaterEqual(obj.view.timer, 990)
        obj.view.refresh_timer.assert_called_once()
        obj.view.set_timer_visible.assert_called_once_with(True)
        obj.view.start_timer.assert_called_once()

    def test_ignored_once_the_build_finished(self):
        obj = self.widget('idle', 300)
        self.reveal(obj)
        obj.view.start_timer.assert_not_called()
        obj.view.set_timer_visible.assert_not_called()

    def test_does_not_stack_a_second_timeout(self):
        obj = self.widget('building', 1000, active=True)
        self.reveal(obj)
        obj.view.start_timer.assert_not_called()

    def test_clears_its_own_source_id(self):
        obj = self.widget('building', 1000)
        self.reveal(obj)
        self.assertIsNone(obj._timer_reveal_id)


class LifecycleTest(unittest.TestCase):
    '''待触发的现身 source 必须在离开构建态时取消。'''

    def test_all_idle_paths_cancel_it(self):
        '''三条回到空闲态的路径都要取消未到期的现身 source。'''
        source = (ROOT / WIDGET).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        for name in ('shutdown', 'show_message', 'apply_build_timer'):
            fn = next((n for n in cls.body if isinstance(n, ast.FunctionDef)
                       and n.name == name), None)
            self.assertIsNotNone(fn, name)
            self.assertIn('cancel_timer_reveal', ast.dump(fn), name)

    def test_callback_is_guarded_even_if_cancellation_is_missed(self):
        '''取消之外还要有守卫：漏一条路径也只是不显示，不会显示错。'''
        reveal = load_widget_method('_reveal_timer')
        obj = self.widget_idle()
        reveal(obj)
        obj.view.start_timer.assert_not_called()

    @staticmethod
    def widget_idle():
        obj = mock.Mock()
        obj.build_button_state = ('idle', int(time.time() * 1000))
        obj._timer_reveal_id = 1
        obj.view.timer_active = False
        return obj


class SettingsWiringTest(unittest.TestCase):

    def test_default_is_auto(self):
        source = (ROOT / 'setzer/settings/settings.py').read_text(encoding='utf-8')
        self.assertIn("['preferences']['build_timer'] = 'auto'", source)

    def test_labels_match_the_value_order(self):
        source = (ROOT / 'setzer/dialogs/preferences/pages/page_build_system.py'
                  ).read_text(encoding='utf-8')
        self.assertIn("build_timer_values = ['auto', 'always', 'never']", source)
        labels = source[source.index("for label in [_('Show once the build passes a second')"):]
        labels = labels[:labels.index(']')]
        self.assertEqual(labels.count('_('), 3)

    def test_invalid_stored_value_does_not_crash_the_page(self):
        source = (ROOT / 'setzer/dialogs/preferences/pages/page_build_system.py'
                  ).read_text(encoding='utf-8')
        self.assertIn('if build_timer not in self.build_timer_values:', source)


if __name__ == '__main__':
    unittest.main()

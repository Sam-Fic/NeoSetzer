#!/usr/bin/env python3
# coding: utf-8
#
# 构建按钮计时器的三态显示。
#
# 三态而不是开关：`auto` 才是这次要的行为——慢引擎显示（数字是用户判断等待
# 的依据），PhiTeX 常驻 session 隐藏（一轮约 18 ms，一秒一跳只会抖成噪音），
# 同时用户在 PhiTeX 下仍要能把它打开。做成"默认关 + PhiTeX 强制关"就没有
# 那个选择；做成两态又区分不出"没设过"和"设成了默认值"（settings.data 是
# 稀疏的，但缺设置文件时它直接别名到 defaults，判断并不可靠）。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
WIDGET = 'setzer/document/build_widget/build_widget.py'


def load_widget_method(method_name):
    source = ROOT / WIDGET
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'BuildWidget')
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = {'time': __import__('time')}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class ShowTimerTest(unittest.TestCase):

    def setUp(self):
        self.show = load_widget_method('_show_build_timer')

    def widget(self, mode, live):
        obj = mock.Mock()
        obj.settings.get_value.return_value = mode
        obj.document.build_system.is_phitex_live_build.return_value = live
        return obj

    def test_auto_shows_for_slow_builders(self):
        self.assertTrue(self.show(self.widget('auto', False)))

    def test_auto_hides_for_phitex(self):
        self.assertFalse(self.show(self.widget('auto', True)))

    def test_always_wins_over_phitex(self):
        '''用户在 PhiTeX 下仍要能把计时器打开。'''
        self.assertTrue(self.show(self.widget('always', True)))

    def test_never_wins_over_traditional_engine(self):
        self.assertFalse(self.show(self.widget('never', False)))

    def test_unknown_value_falls_back_to_auto(self):
        # 手改设置文件写了个没定义的值时，按最保守的 auto 处理而不是抛异常。
        self.assertTrue(self.show(self.widget('banana', False)))
        self.assertFalse(self.show(self.widget('banana', True)))

    def test_default_is_auto(self):
        source = (ROOT / 'setzer/settings/settings.py').read_text(encoding='utf-8')
        self.assertIn("['preferences']['build_timer'] = 'auto'", source)


class ApplyWhileBuildingTest(unittest.TestCase):

    def setUp(self):
        self.apply = load_widget_method('apply_build_timer')

    def presenter(self, state, show_timer, active=False):
        obj = mock.Mock()
        obj.build_button_state = (state, 1_000_000)
        obj._show_build_timer.return_value = show_timer
        obj.view.timer_active = active
        obj.view.timer = 0
        return obj

    def test_noop_when_not_building(self):
        obj = self.presenter('idle', True)
        self.apply(obj)
        obj.view.set_timer_visible.assert_not_called()
        obj.view.start_timer.assert_not_called()

    def test_hiding_stops_the_running_timeout(self):
        '''只藏标签不停 timeout，那个每秒醒一次的源就还挂着。'''
        obj = self.presenter('building', False)
        obj.view.timer_active = True
        self.apply(obj)
        obj.view.stop_timer.assert_called_once()
        obj.view.set_timer_visible.assert_called_once_with(False)

    def test_showing_resumes_from_real_elapsed_time(self):
        obj = self.presenter('building', True, active=False)
        self.apply(obj)
        obj.view.set_timer_visible.assert_called_once_with(True)
        obj.view.refresh_timer.assert_called_once()
        obj.view.start_timer.assert_called_once()
        # 已用时从 build_button_state[1] 起算，不该从 0:00 重来
        self.assertGreater(obj.view.timer, 0)

    def test_showing_twice_does_not_stack_timeouts(self):
        obj = self.presenter('building', True, active=True)
        self.apply(obj)
        obj.view.start_timer.assert_not_called()


class SettingsWiringTest(unittest.TestCase):
    '''偏好页的取值顺序必须和 build_timer_values 对齐。'''

    def test_option_order_matches_values_list(self):
        source = (ROOT / 'setzer/dialogs/preferences/pages/page_build_system.py'
                  ).read_text(encoding='utf-8')
        order = source.index("build_timer_values = ['auto', 'always', 'never']")
        labels = source.index("for label in [_('Show for slow builders, hide for PhiTeX'),")
        self.assertLess(order, labels)
        # 三态标签与取值必须一一对应，否则 set_selected 会越界。
        labels = source[source.index("for label in [_('Show for slow builders"):]
        labels = labels[:labels.index(']')]
        self.assertEqual(labels.count('_('), len(self.page_values()))

    @staticmethod
    def page_values():
        return ['auto', 'always', 'never']

    def test_invalid_stored_value_does_not_crash_the_page(self):
        source = (ROOT / 'setzer/dialogs/preferences/pages/page_build_system.py'
                  ).read_text(encoding='utf-8')
        self.assertIn("if build_timer not in self.build_timer_values:", source)


if __name__ == '__main__':
    unittest.main()

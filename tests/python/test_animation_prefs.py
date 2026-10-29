#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''「减弱动画」偏好中枢（setzer.helpers.animation_prefs）的无界面测试。

中枢承担三件事，每件都要有回归保护：
1. 把每个 Stack/Revealer「本应有」的过渡记在控件上，按当前偏好落地，并在
   用户中途切换偏好时把**所有**已登记控件一起改回来（含各自不同的时长）；
2. 切换偏好后新建的控件必须立刻拿到正确状态，而不是等下次切换；
3. 对没有 transition 属性的 Adw.*SplitView，只能在 set_show_sidebar 这一次
   同步调用期间临时关掉 gtk-enable-animations —— 窗口/对话框的打开关闭动画
   不在减弱范围内，所以这个开关绝不允许留在 False。
'''

import ast
import gc
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

from setzer.app.service_locator import ServiceLocator
from setzer.helpers import animation_prefs as ap


ROOT = Path(__file__).resolve().parents[2]


class FakeSettings:
    '''最小 Observable 替身：只实现 animation_prefs 用到的三个方法。'''

    def __init__(self, reduce_animations=False):
        self.data = {'preferences': {'reduce_animations': reduce_animations}}
        self.connected = {}

    def get_value(self, section, item):
        return self.data[section][item]

    def set_value(self, section, item, value):
        self.data[section][item] = value

    def connect(self, change_code, callback):
        self.connected.setdefault(change_code, set()).add(callback)

    def fire(self, parameter):
        for callback in self.connected.get('settings_changed', ()):
            callback(self, parameter)

    def toggle_reduce(self, value):
        self.set_value('preferences', 'reduce_animations', value)
        self.fire(('preferences', 'reduce_animations', value))


class FakeWidget:
    '''Gtk.Stack / Gtk.Revealer 替身：记录被写入的过渡类型与时长。'''

    def __init__(self):
        self.transition_type = None
        self.transition_duration = None

    def set_transition_type(self, value):
        self.transition_type = value

    def set_transition_duration(self, value):
        self.transition_duration = value


class FakeGtkSettings:
    def __init__(self, enable_animations=True):
        self.props = {'gtk-enable-animations': enable_animations}
        self.history = []

    def get_property(self, name):
        return self.props[name]

    def set_property(self, name, value):
        self.props[name] = value
        self.history.append(value)


class AnimationPrefsTestCase(unittest.TestCase):

    def setUp(self):
        ap.reset()
        self.addCleanup(ap.reset)

    def install(self, reduce_animations=False):
        settings = FakeSettings(reduce_animations)
        ap.install(settings)
        return settings


class TestTransitionRegistration(AnimationPrefsTestCase):

    def test_applies_desired_transition_when_not_reduced(self):
        self.install(False)
        widget = FakeWidget()
        ap.set_transition(widget, 5, 200)
        self.assertEqual(widget.transition_type, 5)
        self.assertEqual(widget.transition_duration, 200)

    def test_reduced_at_startup_suppresses_first_registration(self):
        '''偏好在启动时就是 True：界面构造期登记的控件不能先闪一下动画值。'''
        self.install(True)
        widget = FakeWidget()
        ap.set_transition(widget, 5, 200)
        self.assertEqual(widget.transition_type, ap._NONE)
        self.assertEqual(widget.transition_duration, 0)

    def test_flipping_preference_reaches_every_registered_widget(self):
        settings = self.install(False)
        stack = FakeWidget()
        revealer = FakeWidget()
        ap.set_transition(stack, 5, 200)
        ap.set_transition(revealer, 3, 150)
        settings.toggle_reduce(True)
        self.assertEqual((stack.transition_type, stack.transition_duration), (ap._NONE, 0))
        self.assertEqual((revealer.transition_type, revealer.transition_duration), (ap._NONE, 0))

    def test_flipping_back_restores_each_widgets_own_transition(self):
        '''关掉再打开：各控件必须回到**自己**的过渡类型与时长，不能被串成同一份。'''
        settings = self.install(False)
        stack = FakeWidget()
        revealer = FakeWidget()
        ap.set_transition(stack, 5, 200)
        ap.set_transition(revealer, 3, 150)
        settings.toggle_reduce(True)
        settings.toggle_reduce(False)
        self.assertEqual((stack.transition_type, stack.transition_duration), (5, 200))
        self.assertEqual((revealer.transition_type, revealer.transition_duration), (3, 150))

    def test_widget_created_while_reduced_is_suppressed(self):
        '''切换偏好后新建的控件（如新文档的搜索栏）当场生效，无需重开偏好窗口。'''
        settings = self.install(False)
        settings.toggle_reduce(True)
        widget = FakeWidget()
        ap.set_transition(widget, 7, 250)
        self.assertEqual((widget.transition_type, widget.transition_duration), (ap._NONE, 0))

    def test_reregistering_updates_desired_transition(self):
        '''include_bibtex_file 按样式切换方向重复登记同一个 stack。'''
        settings = self.install(False)
        widget = FakeWidget()
        ap.set_transition(widget, 4, 200)
        ap.set_transition(widget, 6, 200)
        self.assertEqual(widget.transition_type, 6)
        settings.toggle_reduce(True)
        settings.toggle_reduce(False)
        self.assertEqual(widget.transition_type, 6)

    def test_unrelated_settings_changes_are_ignored(self):
        settings = self.install(False)
        widget = FakeWidget()
        ap.set_transition(widget, 5, 200)
        settings.fire(('window_state', 'show_preview', True))
        settings.fire(('preferences', 'tab_width', 8))
        self.assertFalse(ap.is_reduced())
        self.assertEqual(widget.transition_type, 5)

    def test_bulk_preferences_reset_is_picked_up(self):
        '''reset_preferences 广播的是 ('preferences', None, None)。'''
        settings = self.install(True)
        widget = FakeWidget()
        ap.set_transition(widget, 5, 200)
        self.assertEqual(widget.transition_type, ap._NONE)
        settings.set_value('preferences', 'reduce_animations', False)
        settings.fire(('preferences', None, None))
        self.assertEqual(widget.transition_type, 5)

    def test_destroyed_widgets_are_not_retained(self):
        '''登记表必须是弱引用：每个文档都有自己的搜索栏 revealer。'''
        self.install(False)
        widget = FakeWidget()
        ap.set_transition(widget, 5, 200)
        ref = weakref.ref(widget)
        del widget
        gc.collect()
        self.assertIsNone(ref())
        ap.apply_mode()  # 不应因死引用抛异常

    def test_broken_widget_does_not_abort_apply_mode(self):
        self.install(False)
        good = FakeWidget()
        ap.set_transition(good, 5, 200)

        class Broken(FakeWidget):
            def set_transition_type(self, value):
                raise RuntimeError('widget already destroyed')

        broken = Broken()
        ap.set_transition(broken, 3, 150)
        ap.apply_mode()
        self.assertEqual(good.transition_type, 5)


class TestLazyBootstrap(AnimationPrefsTestCase):
    '''惰性自举必须显式注入 Settings：ServiceLocator 兜底会去读开发者机器上
    真实的 ~/.config/setzer/settings.json，测试结果会随本机偏好漂移。'''

    def test_without_settings_animations_stay_enabled(self):
        '''取不到 Settings（极早期/headless）时保持既有动画行为，不得抛异常。'''
        with mock.patch.object(ServiceLocator, 'get_settings', return_value=None):
            self.assertFalse(ap.is_reduced())
            widget = FakeWidget()
            ap.set_transition(widget, 5, 200)
            self.assertEqual(widget.transition_type, 5)

    def test_bootstraps_from_the_service_locator_when_never_installed(self):
        '''入口脚本陈旧、setzer.in 的 install() 没跑时，偏好仍要生效。'''
        settings = FakeSettings(True)
        with mock.patch.object(ServiceLocator, 'get_settings',
                               return_value=settings):
            self.assertTrue(ap.is_reduced())
            widget = FakeWidget()
            ap.set_transition(widget, 5, 200)
            self.assertEqual(
                (widget.transition_type, widget.transition_duration), (ap._NONE, 0))
            # 自举同时接上了观察者，之后切换偏好仍能被感知。
            settings.toggle_reduce(False)
            self.assertEqual(widget.transition_type, 5)

    def test_unreadable_settings_do_not_wedge_the_helper(self):
        '''读偏好失败时不能留下「_settings 已置但没接观察者」的半初始化状态。'''
        class Broken(FakeSettings):
            def get_value(self, section, item):
                raise KeyError(item)

        with mock.patch.object(ServiceLocator, 'get_settings',
                               return_value=Broken(True)):
            self.assertFalse(ap.is_reduced())
        good = FakeSettings(True)
        with mock.patch.object(ServiceLocator, 'get_settings', return_value=good):
            self.assertTrue(ap.is_reduced())


class TestNoTransitionBracket(AnimationPrefsTestCase):
    '''Adw.*SplitView 没有 transition 属性，只能临时翻转 gtk-enable-animations。'''

    def install_gtk(self, enable_animations=True):
        gtk_settings = FakeGtkSettings(enable_animations)
        original = ap.Gtk
        ap.Gtk = SimpleNamespace(Settings=SimpleNamespace(
            get_default=lambda: gtk_settings))
        self.addCleanup(setattr, ap, 'Gtk', original)
        return gtk_settings

    def test_not_reduced_never_touches_global_setting(self):
        '''这是保留窗口/对话框动画的关键：未开启偏好时绝不碰全局开关。'''
        self.install(False)
        gtk_settings = self.install_gtk()
        with ap.no_transition():
            pass
        self.assertEqual(gtk_settings.history, [])

    def test_reduced_disables_inside_and_restores_after(self):
        self.install(True)
        gtk_settings = self.install_gtk()
        with ap.no_transition():
            self.assertFalse(gtk_settings.props['gtk-enable-animations'])
        self.assertTrue(gtk_settings.props['gtk-enable-animations'])
        self.assertEqual(gtk_settings.history, [False, True])

    def test_reduced_restores_even_when_body_raises(self):
        self.install(True)
        gtk_settings = self.install_gtk()
        with self.assertRaises(RuntimeError):
            with ap.no_transition():
                raise RuntimeError('split view blew up')
        self.assertTrue(gtk_settings.props['gtk-enable-animations'])

    def test_already_disabled_globally_is_left_alone(self):
        '''系统层面已关动画时不要多此一举，更不能在退出时把它打开。'''
        self.install(True)
        gtk_settings = self.install_gtk(enable_animations=False)
        with ap.no_transition():
            pass
        self.assertEqual(gtk_settings.history, [])
        self.assertFalse(gtk_settings.props['gtk-enable-animations'])

    def test_missing_gtk_settings_is_tolerated(self):
        self.install(True)
        original = ap.Gtk
        ap.Gtk = SimpleNamespace(Settings=SimpleNamespace(get_default=lambda: None))
        self.addCleanup(setattr, ap, 'Gtk', original)
        with ap.no_transition():
            pass

    def test_set_show_sidebar_forwards_and_brackets(self):
        self.install(True)
        gtk_settings = self.install_gtk()
        calls = []
        split_view = SimpleNamespace(
            set_show_sidebar=lambda show: calls.append(
                (show, gtk_settings.props['gtk-enable-animations'])))
        ap.set_show_sidebar(split_view, True)
        self.assertEqual(calls, [(True, False)])
        self.assertTrue(gtk_settings.props['gtk-enable-animations'])

    def test_set_show_sidebar_passes_value_through_when_not_reduced(self):
        self.install(False)
        gtk_settings = self.install_gtk()
        calls = []
        split_view = SimpleNamespace(
            set_show_sidebar=lambda show: calls.append(
                (show, gtk_settings.props['gtk-enable-animations'])))
        ap.set_show_sidebar(split_view, False)
        self.assertEqual(calls, [(False, True)])


RAW_TRANSITION_CALL = re.compile(r'\.set_transition_(?:type|duration)\(')


class TestNoRawTransitionCalls(AnimationPrefsTestCase):
    '''所有过渡都必须经 animation_prefs.set_transition 登记，否则该控件永远
    不受「减弱动画」影响。'''

    def test_only_the_helper_calls_gtk_directly(self):
        offenders = []
        for path in sorted((ROOT / 'setzer').rglob('*.py')):
            if path.name == 'animation_prefs.py':
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), start=1):
                if line.lstrip().startswith('#'):
                    continue
                if RAW_TRANSITION_CALL.search(line):
                    offenders.append('{}:{}'.format(path.relative_to(ROOT), lineno))
        self.assertEqual(offenders, [])


class TestPreferenceIsDeclared(unittest.TestCase):

    def test_reduce_animations_has_a_default(self):
        source = (ROOT / 'setzer/settings/settings.py').read_text(encoding='utf-8')
        self.assertIn("['reduce_animations'] = False", source)

    def test_preferences_page_exposes_the_switch(self):
        source = (ROOT / 'setzer/dialogs/preferences/pages/page_appearance.py'
                  ).read_text(encoding='utf-8')
        self.assertIn('option_reduce_animations', source)
        self.assertIn("'reduce_animations'", source)

    def test_entry_script_installs_the_helper(self):
        source = (ROOT / 'setzer.in').read_text(encoding='utf-8')
        self.assertIn('animation_prefs.install(self.settings)', source)


class TestSidebarVisibilityUsesHelper(unittest.TestCase):
    '''workspace_presenter 的运行时显隐必须走 animation_prefs.set_show_sidebar。'''

    def _method_source(self, name):
        source = (ROOT / 'setzer/workspace/workspace_presenter.py'
                  ).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef)
                   and node.name == 'WorkspacePresenter')
        method = next(node for node in cls.body
                      if isinstance(node, ast.FunctionDef) and node.name == name)
        return ast.get_source_segment(source, method)

    def test_update_sidebar_visibility(self):
        body = self._method_source('update_sidebar_visibility')
        self.assertIn('animation_prefs.set_show_sidebar', body)
        self.assertNotIn('sidebar_split.set_show_sidebar', body)

    def test_update_preview_help_visibility(self):
        body = self._method_source('update_preview_help_visibility')
        self.assertIn('animation_prefs.set_show_sidebar', body)
        self.assertNotIn('preview_split.set_show_sidebar', body)


if __name__ == '__main__':
    unittest.main()

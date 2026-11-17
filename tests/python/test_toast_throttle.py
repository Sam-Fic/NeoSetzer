#!/usr/bin/env python3
# coding: utf-8
#
# 重复命中的提示统一经 setzer.helpers.toast 节流。
#
# 理念是「非必要不打扰」：缩放到顶、搜索绕回、普通点击链接、前向同步无 PDF、
# 构建失败回退旧 PDF、构建被取代——这些是同一个动作重复触发同一句话，逐次
# 弹 toast 就把反馈变成了噪音。这里只测节流逻辑本身 + 钉住几个接入点。

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tree(relative_path):
    return ast.parse((ROOT / relative_path).read_text(encoding='utf-8'))


def _class_method(relative_path, class_name, method_name):
    tree = _tree(relative_path)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    return next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == method_name)


def _calls(method, func_name):
    '''方法体内对 func_name 的调用列表。'''
    found = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(
            target, 'id', None)
        if name == func_name:
            found.append(node)
    return found


def _builds_adw_toast(method):
    return bool(_calls(method, 'new'))


_GI_BEFORE = {name for name in sys.modules if name == 'gi' or name.startswith('gi.')}


class ModuleImportTest(unittest.TestCase):

    def test_helper_imports_without_gi(self):
        '''helpers.toast 必须能在无 GTK 的环境里导入（CI 是 headless）。'''
        from setzer.helpers import toast  # noqa: F401
        self.assertNotIn('gi', sys.modules.keys() - _GI_BEFORE)


class ThrottleTest(unittest.TestCase):

    def setUp(self):
        from setzer.helpers import toast
        self.toast = toast
        toast.reset()

    def test_first_time_shows(self):
        self.assertTrue(self.toast.should_show('max zoom', now=100.0))

    def test_repeat_within_window_is_dropped(self):
        self.assertTrue(self.toast.should_show('max zoom', now=100.0))
        self.assertFalse(self.toast.should_show('max zoom', now=100.5))
        self.assertFalse(self.toast.should_show('max zoom', now=102.4))

    def test_repeat_after_window_shows_again(self):
        self.assertTrue(self.toast.should_show('max zoom', now=100.0))
        self.assertTrue(self.toast.should_show('max zoom', now=102.5))

    def test_keys_are_independent(self):
        self.assertTrue(self.toast.should_show('zoom in', now=100.0))
        self.assertTrue(self.toast.should_show('zoom out', now=100.0))

    def test_none_throttle_never_dedupes(self):
        self.assertTrue(self.toast.should_show('copy', throttle_ms=None))
        self.assertTrue(self.toast.should_show('copy', throttle_ms=None))

    def test_registry_is_bounded(self):
        for index in range(200):
            self.toast.should_show(f'message {index}', now=100.0)
        self.assertLessEqual(len(self.toast._last_shown_ms), self.toast._MAX_KEYS)


class WiringTest(unittest.TestCase):
    '''节流接入点：不许退回「每次操作都 Adw.Toast.new」。'''

    SITES = [
        ('setzer/workspace/preview_panel/preview_panel_presenter.py',
         'PreviewPanelPresenter', '_show_zoom_toast'),
        ('setzer/document/search/search.py', 'Search', '_show_wrap_around_toast'),
        ('setzer/document/preview/preview_controller.py',
         'PreviewController', '_show_ctrl_hint_toast'),
        ('setzer/document/preview/preview_controller.py',
         'PreviewController', '_show_url_error_toast'),
        ('setzer/document/document_controller.py', 'DocumentController',
         '_show_sync_unavailable_toast'),
        ('setzer/document/preview/preview_viewgtk.py', 'PreviewView',
         'show_pdf_load_failed'),
    ]

    def test_sites_go_through_helper(self):
        for relative_path, class_name, method_name in self.SITES:
            method = _class_method(relative_path, class_name, method_name)
            self.assertTrue(_calls(method, 'show'),
                            f'{relative_path}:{method_name} 未走 toast.show')
            self.assertFalse(_builds_adw_toast(method),
                             f'{relative_path}:{method_name} 仍在直接建 Toast')

    def test_pdf_load_failed_toast_is_throttled_wider(self):
        '''构建失败回退旧 PDF 时会连着触发，窗口要比默认更宽。'''
        method = _class_method('setzer/document/preview/preview_viewgtk.py',
                               'PreviewView', 'show_pdf_load_failed')
        keywords = {kw.arg for call in _calls(method, 'show') for kw in call.keywords}
        self.assertIn('throttle_ms', keywords)

    def test_build_cancelled_toast_silent_for_phitex_live(self):
        '''实时构建每 150ms 一轮，被新构建取代是常态，不该提示。'''
        method = _class_method('setzer/document/build_system/build_system.py',
                               'BuildSystem', 'add_query')
        self.assertTrue(_calls(method, 'is_phitex_live_build'))
        self.assertFalse(_builds_adw_toast(method))


if __name__ == '__main__':
    unittest.main()

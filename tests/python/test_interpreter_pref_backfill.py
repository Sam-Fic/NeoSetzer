#!/usr/bin/env python3
# coding: utf-8
#
# 偏好设置页回填全局引擎时不能改动已保存的选择。
#
# 检测线程返回后，_apply_interpreter_results 先 set_model 再 set_selected。
# Adw.ComboRow 的 set_model 会把选中项重置到第 0 项并发出 notify::selected，
# 而该回调把"当前选择"写回设置——于是每打开一次偏好页，保存的引擎就被
# 换成第一个检测到的引擎（通常 xelatex），用户看到的是"重启后选择丢了"。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PAGE = 'setzer/dialogs/preferences/pages/page_build_system.py'
DETECTED = ['xelatex', 'pdflatex', 'lualatex', 'phitex']


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class FakeSettings:

    def __init__(self, interpreter):
        self.interpreter = interpreter
        self.writes = []

    def get_value(self, section, item):
        return self.interpreter

    def set_value(self, section, item, value):
        self.writes.append(value)
        self.interpreter = value


class FakeCombo:
    '''模拟 Adw.ComboRow：set_model 把选中项重置为 0 并发出 notify。'''

    def __init__(self, page):
        self.page = page
        self.selected = -1
        self.blocked = False

    def handler_block_by_func(self, callback):
        self.blocked = True

    def handler_unblock_by_func(self, callback):
        self.blocked = False

    def set_visible(self, visible):
        pass

    def set_model(self, model):
        self.selected = 0
        self._notify()

    def set_selected(self, index):
        self.selected = index
        self._notify()

    def _notify(self):
        if not self.blocked:
            self.page.on_interpreter_selected(self, None)


def apply_results(stored, detected=DETECTED):
    namespace = {'Gtk': mock.Mock(INVALID_LIST_POSITION=-1), '_': lambda text: text}
    Page = type('Page', (), {
        'on_interpreter_selected': load_method(
            PAGE, 'PageBuildSystem', 'on_interpreter_selected', namespace),
        '_apply_interpreter_results': load_method(
            PAGE, 'PageBuildSystem', '_apply_interpreter_results', namespace),
    })
    page = Page()
    page.settings = FakeSettings(stored)
    page.view = mock.Mock()
    page.view.option_latex_interpreter = FakeCombo(page)
    page.on_switch_toggled = mock.Mock()
    page.update_output_chain_sensitivity = mock.Mock()
    page.update_engine_specific_visibility = mock.Mock()
    page._apply_interpreter_results(detected, True)
    return page


class InterpreterBackfillTest(unittest.TestCase):

    def test_backfill_keeps_the_saved_interpreter(self):
        page = apply_results('phitex')
        self.assertEqual(page.settings.writes, [])
        self.assertEqual(page.settings.interpreter, 'phitex')
        self.assertEqual(page.view.option_latex_interpreter.selected,
                         DETECTED.index('phitex'))

    def test_backfill_keeps_a_non_first_engine(self):
        page = apply_results('pdflatex')
        self.assertEqual(page.settings.writes, [])
        self.assertEqual(page.view.option_latex_interpreter.selected, 1)

    def test_backfill_falls_back_only_when_the_engine_is_unavailable(self):
        page = apply_results('tectonic')
        self.assertEqual(page.settings.writes, ['xelatex'])
        self.assertEqual(page.view.option_latex_interpreter.selected, 0)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8
#
# 对话框内的提示必须发到对话框自身。
#
# 主窗口在设置窗口 / 模态 Adw.Dialog 之下，往它的 toast_overlay 里塞 toast，
# 或拿它当 AlertDialog 的父窗口，用户看到的只是"没反应"——东西其实弹在了
# 被盖住的那一层。这组测试把宿主钉死。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


def service_locator_class():
    import setzer.app.service_locator as SL
    return SL.ServiceLocator


PAGES = 'setzer/dialogs/preferences/pages/'


class PreferencesPageToastTest(unittest.TestCase):
    '''偏好页的 toast。'''

    def setUp(self):
        self.ns = {'Adw': mock.Mock(),
                   '_': lambda text: text,
                   'ServiceLocator': service_locator_class()}

    def page(self):
        obj = mock.Mock()
        obj.preferences.view = mock.Mock()
        return obj

    def assert_in_preferences(self, path, class_name, obj):
        method = load_method(path, class_name, '_show_toast', self.ns)
        method(obj, 'message')
        obj.preferences.view.add_toast.assert_called_once()
        self.assertFalse(obj.main_window.toast_overlay.add_toast.called)

    def test_shortcuts_page(self):
        self.assert_in_preferences(PAGES + 'page_shortcuts.py',
                                   'PageShortcuts', self.page())

    def test_editor_page(self):
        self.assert_in_preferences(PAGES + 'page_editor.py',
                                   'PageEditor', self.page())

    def test_appearance_page_does_not_prefer_the_main_window(self):
        '''这里原本就是"主窗口 overlay 优先、对话框兜底"，顺序反了。'''
        obj = self.page()
        obj.view.toast_overlay = mock.Mock()
        self.assert_in_preferences(PAGES + 'page_appearance.py',
                                   'PageGeneral', obj)

    def test_falls_back_when_the_dialog_is_gone(self):
        obj = self.page()
        obj.preferences.view = None
        ns = dict(self.ns,
                  ServiceLocator=mock.Mock(get_main_window=lambda: obj.main_window))
        method = load_method(PAGES + 'page_shortcuts.py',
                             'PageShortcuts', '_show_toast', ns)
        method(obj, 'message')
        obj.main_window.toast_overlay.add_toast.assert_called_once()


class WizardConfirmationParentTest(unittest.TestCase):
    '''新建文档向导内的确认框。向导本身是模态的，父窗口不能是主窗口。'''

    def test_validation_error_is_parented_to_the_wizard(self):
        ns = {'Adw': mock.Mock(), '_': lambda text: text}
        method = load_method('setzer/dialogs/document_wizard/document_wizard.py',
                             'DocumentWizard', '_show_validation_error', ns)
        wizard = mock.Mock()
        method(wizard, 'message')
        parent = ns['Adw'].AlertDialog.return_value.choose.call_args[0][0]
        self.assertIs(parent, wizard.view)


class ProjectSearchReplaceToastTest(unittest.TestCase):

    def setUp(self):
        self.ns = {'Adw': mock.Mock()}
        self.method = load_method('setzer/dialogs/project_search_replace.py',
                                  'ProjectSearchReplaceDialog', '_show_toast', self.ns)

    def test_open_dialog_uses_its_own_overlay(self):
        '''应用失败时要留在弹窗里改条件，提示必须看得见。'''
        obj = mock.Mock()
        obj.dialog.get_mapped.return_value = True
        self.method(obj, 'boom')
        obj.toast_overlay.add_toast.assert_called_once()
        self.assertFalse(obj.main_window.toast_overlay.add_toast.called)

    def test_closed_dialog_falls_back_to_the_main_window(self):
        '''"先保存文档"那条提示发出时弹窗还没打开，得走主窗口。'''
        obj = mock.Mock()
        obj.dialog.get_mapped.return_value = False
        self.method(obj, 'save first')
        obj.main_window.toast_overlay.add_toast.assert_called_once()
        self.assertFalse(obj.toast_overlay.add_toast.called)


if __name__ == '__main__':
    unittest.main()

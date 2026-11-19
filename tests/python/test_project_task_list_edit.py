#!/usr/bin/env python3
# coding: utf-8
#
# 项目构建任务列表的最后一项不允许删除。
#
# 空的任务列表存盘时会被 ProjectBuildConfiguration._validate_tasks 还原成
# 默认的 latex 步（写到磁盘的内容随即被重新解析，当前构建也会立刻读回它）。
# 也就是说"删光所有步骤"并不会生效，只是在用户看不出的时候被加了回去。
# 既然不允许为空，就在 UI 上把最后一项的删除按钮置灰。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DIALOG = 'setzer/dialogs/project_build_configuration.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class FakeButton:
    '''只记录 _make_task_row 真正会设的那几个属性。'''

    def __init__(self, icon_name=None):
        self.icon_name = icon_name
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, text):
        pass

    def set_has_frame(self, value):
        pass

    def set_valign(self, value):
        pass

    def add_css_class(self, value):
        pass

    def connect(self, *args):
        pass


class TaskRowRemoveButtonTest(unittest.TestCase):

    def row_buttons(self, tasks):
        buttons = []

        def make_button(**kwargs):
            button = FakeButton(kwargs.get('icon_name'))
            buttons.append(button)
            return button

        namespace = {'Adw': mock.Mock(), 'Gtk': mock.Mock(Button=make_button),
                     'task_type_label': lambda task: task,
                     '_': lambda text: text}
        method = load_method(DIALOG, 'ProjectBuildConfigurationDialog',
                             '_make_task_row', namespace)
        method(mock.Mock(), tasks[0], 0, {'tasks': tasks})
        return {button.icon_name: button for button in buttons}

    def test_last_remaining_task_cannot_be_removed(self):
        buttons = self.row_buttons(['latex'])
        self.assertFalse(buttons['window-close-symbolic'].sensitive)

    def test_remove_stays_available_while_more_than_one_task(self):
        buttons = self.row_buttons(['latex', 'bibtex'])
        self.assertTrue(buttons['window-close-symbolic'].sensitive)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8
#
# 「{name} is already open」toast 的适用范围。
#
# 这个提示原本挂在 open_document_by_filename 里，而该方法是所有编程式打开的
# 公共入口：synctex 反向同步（Ctrl+点击 PDF 跳到 \\input 的子文件）、侧栏
# include 跳转、结构树、build log、AI 批注映射都走它。双向跳转时目标文档
# 通常已经打开，于是每次跳转都弹一次 toast，用户没法连续操作。
#
# 规则：只有用户显式发起的打开（文件选择器 / 最近文档 / 欢迎页 / 重开已关闭
# 文档）才提示；编程式跳转静默——切换标签页本身就是反馈。

import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = 'setzer/workspace/workspace.py'
FLAG = 'notify_if_already_open'


def _module_tree(relative_path):
    return ast.parse((ROOT / relative_path).read_text(encoding='utf-8'))


def _class_methods(relative_path, class_name):
    tree = _module_tree(relative_path)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def _calls_with_flag(fn, func_name, flag):
    '''函数体内对 func_name 的调用，是否传了 flag 关键字。'''
    hits = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(
            target, 'id', None)
        if name != func_name:
            continue
        keywords = {kw.arg for kw in node.keywords}
        hits.append(flag in keywords)
    return hits


class _GLib:
    '''idle_add 立即执行：toast 走的是 idle，测试里不需要真主循环。'''

    @staticmethod
    def idle_add(callback, *args):
        callback(*args)
        return 0


class _ServiceLocator:

    main_window = None

    @classmethod
    def get_main_window(cls):
        return cls.main_window


def _open_document_by_filename():
    tree = _module_tree(WORKSPACE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef)
              and n.name == 'open_document_by_filename')
    namespace = {
        'os': os,
        'GLib': _GLib,
        'ServiceLocator': _ServiceLocator,
        '_': lambda text: text,
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]),
                 '<workspace>', 'exec'), namespace)
    return namespace['open_document_by_filename']


class AlreadyOpenToastTest(unittest.TestCase):

    def setUp(self):
        self.toasts = []
        self.activated = []
        self.main_window = SimpleNamespace(toast_overlay=SimpleNamespace(
            add_toast=self.toasts.append))
        _ServiceLocator.main_window = self.main_window

        document = SimpleNamespace(get_filename=lambda: '/p/chapter.tex')
        self.document = document
        self.workspace = SimpleNamespace(
            get_document_by_filename=lambda filename: document,
            set_active_document=self.activated.append,
            create_document_from_filename=Mock(return_value=None),
            _do_show_toast=lambda main_window, message: main_window
            .toast_overlay.add_toast(message))
        self.open_document_by_filename = _open_document_by_filename()

    def tearDown(self):
        _ServiceLocator.main_window = None

    def test_programmatic_open_is_silent(self):
        '''synctex 反向同步等重复跳转：切过去即可，不弹 toast。'''
        result = self.open_document_by_filename(self.workspace, '/p/chapter.tex')
        self.assertIs(result, self.document)
        self.assertEqual(self.activated, [self.document])
        self.assertEqual(self.toasts, [])

    def test_explicit_opt_in_still_notifies(self):
        self.open_document_by_filename(
            self.workspace, '/p/chapter.tex', notify_if_already_open=True)
        self.assertEqual(self.toasts, ['chapter.tex is already open'])

    def test_flag_defaults_to_false(self):
        fn = _class_methods(WORKSPACE, 'Workspace')['open_document_by_filename']
        names = [arg.arg for arg in fn.args.args]
        self.assertIn(FLAG, names)
        index = names.index(FLAG)
        defaults = [None] * (len(names) - len(fn.args.defaults)) + [
            ast.literal_eval(d) for d in fn.args.defaults]
        self.assertIs(defaults[index], False)


class NotificationSitesTest(unittest.TestCase):
    '''把「哪些入口可以提示」钉在源码上，避免又回到全局提示。'''

    def test_user_open_entries_opt_in(self):
        methods = _class_methods(WORKSPACE, 'Workspace')
        for name in ('open_document_by_filename_with_spinner',
                     '_do_open_document_by_filename'):
            hits = _calls_with_flag(methods[name], 'open_document_by_filename', FLAG)
            self.assertTrue(hits, name)
            self.assertTrue(all(hits), name)

        # 文件选择器是用户显式多选，命中已打开文档要提示。
        controller = _class_methods(
            'setzer/dialogs/open_document/open_document.py', 'OpenDocumentDialog')
        hits = _calls_with_flag(controller['_do_open_files'],
                                'open_document_by_filename', FLAG)
        self.assertTrue(hits and all(hits), '_do_open_files')

    def test_programmatic_jumps_stay_silent(self):
        cases = [
            # 双向跳转：Ctrl+点击 PDF 跳到 \input 的子文件。
            (WORKSPACE, 'Workspace', 'jump_to_synctex_position'),
            # 构建前补开 root 文档，用户没主动点「打开」。
            (WORKSPACE, 'Workspace', '_open_build_root_if_needed'),
            ('setzer/workspace/actions/actions.py', 'Actions',
             '_document_for_mapping'),
            ('setzer/workspace/sidebar/document_structure_page/files.py',
             'FilesSection', 'on_row_activated'),
            ('setzer/dialogs/build_log/build_log_dialog_controller.py',
             'BuildLogDialogController', 'on_row_activated'),
            # 批量拖放常含已打开文件，别堆一串 toast。
            ('setzer/workspace/workspace_viewgtk.py', 'MainWindow',
             '_do_open_dropped_files'),
        ]
        for relative_path, class_name, method_name in cases:
            methods = _class_methods(relative_path, class_name)
            self.assertIn(method_name, methods, f'{relative_path}:{method_name}')
            hits = _calls_with_flag(methods[method_name],
                                    'open_document_by_filename', FLAG)
            self.assertTrue(hits, f'{relative_path}:{method_name}')
            self.assertFalse(any(hits), f'{relative_path}:{method_name}')


if __name__ == '__main__':
    unittest.main()

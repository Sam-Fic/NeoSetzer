#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''DocumentView 的编辑器几何观察者（无界面 AST 测试）。

GTK4 的 GtkWidget 没有 width/height 属性：`connect('notify::width', ...)`
不报错但永不触发（实测 TextView 从 300px 拉到 500px，do_size_allocate 各调
一次、notify 零次），GTK3 的 size-allocate 信号也已移除。gutter / sticky
scroll / multicursor 这些覆盖在编辑器上的 DrawingArea 只能靠
DocumentView.do_size_allocate 拿到「编辑器列宽这一帧变了」的通知——侧栏开合
动画期间每帧一次，正是行号需要与正文同帧重绘的时机。'''

import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
VIEW = 'setzer/document/document_viewgtk.py'


def make_view():
    source = ROOT / VIEW
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'DocumentView')
    names = {'do_size_allocate', 'add_size_observer', 'remove_size_observer'}
    methods = [node for node in cls_node.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'Gtk': SimpleNamespace(Box=SimpleNamespace(
        do_size_allocate=lambda self, w, h, baseline: None))}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
    cls = type('DocumentView', (),
               {node.name: namespace[node.name] for node in methods})

    view = cls()
    view._size_observers = []
    view._last_allocated_size = None
    view._bottom_margin_calls = []
    view._update_bottom_margin = lambda height: view._bottom_margin_calls.append(height)
    view.source_view = SimpleNamespace(get_allocated_height=lambda: 600)
    return view


class TestSizeObservers(unittest.TestCase):
    def test_observer_receives_new_size(self):
        view = make_view()
        seen = []
        view.add_size_observer(lambda w, h: seen.append((w, h)))
        view.do_size_allocate(800, 600, -1)
        self.assertEqual(seen, [(800, 600)])

    def test_same_size_does_not_notify_again(self):
        view = make_view()
        seen = []
        view.add_size_observer(lambda w, h: seen.append((w, h)))
        for _ in range(5):
            view.do_size_allocate(800, 600, -1)
        # do_size_allocate 会因 baseline 变化 / 父级重新分配被反复调用，
        # 尺寸没变就不该把每帧的无意义通知推给观察者。
        self.assertEqual(seen, [(800, 600)])

    def test_animation_frames_notify_every_width_change(self):
        view = make_view()
        seen = []
        view.add_size_observer(lambda w, h: seen.append((w, h)))
        for width in (800, 780, 760, 740):
            view.do_size_allocate(width, 600, -1)
        self.assertEqual(seen, [(800, 600), (780, 600), (760, 600), (740, 600)])

    def test_removed_observer_is_not_called(self):
        view = make_view()
        seen = []
        callback = lambda w, h: seen.append((w, h))
        view.add_size_observer(callback)
        view.remove_size_observer(callback)
        view.do_size_allocate(800, 600, -1)
        self.assertEqual(seen, [])

    def test_remove_unknown_observer_is_silent(self):
        view = make_view()
        view.remove_size_observer(lambda w, h: None)

    def test_bottom_margin_still_updated_on_every_allocate(self):
        view = make_view()
        view.do_size_allocate(800, 600, -1)
        view.do_size_allocate(800, 600, -1)
        self.assertEqual(view._bottom_margin_calls, [600, 600])


class TestNoDeadResizeConnections(unittest.TestCase):
    '''notify::width / notify::height 在 GTK4 上永不触发：源码里不允许再出现。

    连接本身不报错，坏掉的是功能（重绘/重算静默不发生），所以用源码扫描把
    这个坑钉死，而不是依赖某次人工发现。'''

    def test_no_widget_size_notify_connections(self):
        pattern = re.compile(r"""connect\(\s*['"]notify::(width|height)['"]""")
        offenders = []
        for path in sorted((ROOT / 'setzer').rglob('*.py')):
            if '__pycache__' in path.parts:
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding='utf-8').splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                if pattern.search(line):
                    offenders.append(f'{path.relative_to(ROOT)}:{lineno}')
        self.assertEqual(offenders, [])


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''PDF 预览弹出窗口的面板 reparent 契约。

面板带父控件时 Gtk.Box.append / Gtk.Stack.add_named 只会打一条警告并
静默丢弃 —— 独立窗口就整片空白。本测试锁住：
- set_panel 无论面板是否已有父控件，都要真正挂到窗口内容区并可见/可扩展；
- take_panel 之后面板完全脱离父控件，可被侧栏重新 add_named。
无显示环境整体跳过（GTK 无 display 时实例化自定义子类会段错误）。
'''

import builtins
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s
builtins.ngettext = lambda s, p, n: s if n == 1 else p

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

if gtk_ready:
    from setzer.workspace.pdf_preview_window import pdf_preview_window


class FakeWorkspace:
    def __init__(self, main_window):
        self.main_window = main_window

    def connect(self, *args):
        pass

    def get_preview_target_document(self):
        return None

    def is_preview_popped_out(self):
        return False


@unittest.skipUnless(gtk_ready, 'needs a real GDK display')
class PanelReparentTest(unittest.TestCase):

    def setUp(self):
        self.parent_window = Adw.Window()
        self.workspace = FakeWorkspace(self.parent_window)
        self.window = pdf_preview_window.PdfPreviewWindow(self.workspace)

    def tearDown(self):
        self.window.destroy()
        self.parent_window.destroy()

    def test_set_panel_reparents_from_stack(self):
        stack = Gtk.Stack()
        panel = Gtk.Box()
        stack.add_named(panel, 'preview')
        self.window.set_panel(panel)

        self.assertIs(panel.get_parent(), self.window.content_box)
        self.assertTrue(panel.get_visible())
        self.assertTrue(panel.get_vexpand())

    def test_set_panel_handles_widget_that_still_has_a_parent(self):
        holder = Gtk.Box()
        panel = Gtk.Box()
        holder.append(panel)

        self.window.set_panel(panel)

        self.assertIs(panel.get_parent(), self.window.content_box)
        self.assertIsNone(holder.get_first_child())

    def test_take_panel_unparents_for_sidebar_reuse(self):
        panel = Gtk.Box()
        self.window.set_panel(panel)
        returned = self.window.take_panel()

        self.assertIs(returned, panel)
        self.assertIsNone(panel.get_parent())
        # 侧栏可以重新挂载（父控件残留时 add_named 会被静默丢弃）。
        stack = Gtk.Stack()
        stack.add_named(panel, 'preview')
        self.assertIs(panel.get_parent(), stack)


if __name__ == '__main__':
    unittest.main()

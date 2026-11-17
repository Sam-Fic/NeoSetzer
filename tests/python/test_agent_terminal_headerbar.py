#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''独立 Agent 终端窗口的单层标题栏测试。

契约：弹出独立窗口时，终端视图的迷你工具栏不再单独占一行——
agent 名 / 会话状态写进 Adw.WindowTitle（标题 / 副标题），三个快捷按钮
reparent 到标题栏右侧；收回侧栏后工具栏复原且按钮顺序不变。

无显示环境时整体跳过。注意 GTK ≥4.14 起 Gtk.init() 无显示也能成功，
但此时实例化 Python 定义的 widget 子类会在 GTK 内部段错误，所以守卫
必须查 Gdk.Display.get_default() 是否真的拿到显示。
'''

import builtins
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

if gtk_ready:
    from setzer.ai_fix.agent_terminal_viewgtk import AgentTerminalView


class FakeSettings:
    def __init__(self, tool='pi'):
        self.tool = tool

    def get_value(self, section, key):
        if key == 'ai_fix_active_tool':
            return self.tool
        return None


class FakeSession(GObject.Object):
    __gsignals__ = {'state_changed': (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, state='running'):
        super().__init__()
        self.settings = FakeSettings()
        self.state = state
        self.terminal = None


def toolbar_icon_names(toolbar):
    names = []
    child = toolbar.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            names.append(child.get_icon_name())
        child = child.get_next_sibling()
    return names


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class AgentTerminalHeaderbarTest(unittest.TestCase):

    def setUp(self):
        self.session = FakeSession()
        self.view = AgentTerminalView(self.session)
        self.headerbar = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='Agent Terminal')
        self.headerbar.set_title_widget(self.title)

    def tearDown(self):
        self.view.detach_headerbar()

    def test_attach_hides_toolbar_and_fills_title(self):
        self.view.attach_headerbar(self.headerbar, self.title)

        self.assertFalse(self.view.toolbar.get_visible())
        self.assertEqual(self.title.get_title(), 'pi')
        self.assertEqual(self.title.get_subtitle(), 'Session active')
        for button in (self.view.close_session_button,
                       self.view.new_session_button,
                       self.view.pop_button):
            self.assertIsNot(button.get_parent(), None)
            self.assertIs(button.get_parent(), self.view.pop_button.get_parent())

    def test_buttons_keep_sidebar_left_to_right_order(self):
        '''标题栏 pack_end 是栈式插入，顺序必须与侧栏工具栏一致。'''
        window = Adw.Window(default_width=600, default_height=400)
        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(self.headerbar)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar_view.set_content(content)
        window.set_content(toolbar_view)
        content.append(self.view)
        window.present()

        self.view.attach_headerbar(self.headerbar, self.title)
        while GLib.MainContext.default().iteration(False):
            pass

        positions = []
        for name in ('close_session_button', 'new_session_button', 'pop_button'):
            button = getattr(self.view, name)
            ok, rect = button.compute_bounds(window)
            self.assertTrue(ok)
            positions.append(rect.get_x())
        self.assertEqual(positions, sorted(positions))
        self.view.detach_headerbar()
        window.destroy()

    def test_title_follows_session_state(self):
        self.view.attach_headerbar(self.headerbar, self.title)
        self.session.state = 'exited'
        self.session.emit('state_changed', 'exited')
        self.assertIn('Session ended', self.title.get_subtitle())

    def test_detach_restores_toolbar_and_order(self):
        self.view.attach_headerbar(self.headerbar, self.title)
        self.view.detach_headerbar()

        self.assertTrue(self.view.toolbar.get_visible())
        self.assertEqual(toolbar_icon_names(self.view.toolbar), [
            'process-stop-symbolic',
            'chat-message-new-symbolic',
            'window-new-symbolic',
        ])


if __name__ == '__main__':
    unittest.main()

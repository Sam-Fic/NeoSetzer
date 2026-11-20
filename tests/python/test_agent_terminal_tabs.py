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

'''Agent 会话标签页测试。

契约：一个 AgentSession 对应一个 Adw.TabPage；会话新增/移除同步标签页；
切换标签同步 AgentSessions.active；标签关闭 = 结束并移除该会话；单个会话
也保留标签（关闭入口在标签上）；弹出按钮图标随弹出态切换。

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
from gi.repository import Gdk, GLib, GObject, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

if gtk_ready:
    from setzer.ai_fix.agent_terminal_tabs import AgentTerminalTabs
    from setzer.helpers.observable import Observable


class FakeSettings:
    def __init__(self, tool='pi'):
        self.tool = tool

    def get_value(self, section, key):
        if key == 'ai_fix_active_tool':
            return self.tool
        return None


class FakeSession(GObject.Object):
    __gsignals__ = {'state_changed': (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, state='running', cwd='/tmp/project'):
        super().__init__()
        self.settings = FakeSettings()
        self.state = state
        self.terminal = None
        self.session_cwd = cwd

    def end_session(self):
        self.state = 'idle'


class FakeSessions(Observable):
    def __init__(self, sessions):
        Observable.__init__(self)
        self.sessions = list(sessions)
        self.active = self.sessions[0] if self.sessions else None

    def new_session(self):
        session = FakeSession(state='idle', cwd='/tmp/another')
        self.sessions.append(session)
        self.active = session
        self.add_change_code('session_added', session)
        self.add_change_code('active_changed', session)
        return session

    def set_active(self, session):
        if session in self.sessions and session is not self.active:
            self.active = session
            self.add_change_code('active_changed', session)

    def remove(self, session):
        if session not in self.sessions:
            return
        index = self.sessions.index(session)
        self.sessions.remove(session)
        session.end_session()
        if self.active is session:
            self.active = (self.sessions[min(index, len(self.sessions) - 1)]
                           if self.sessions else None)
            self.add_change_code('active_changed', self.active)
        self.add_change_code('session_removed', session)


def settle():
    while GLib.MainContext.default().iteration(False):
        pass


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class AgentTerminalTabsTest(unittest.TestCase):

    def setUp(self):
        self.session = FakeSession()
        self.manager = FakeSessions([self.session])
        self.tabs = AgentTerminalTabs(self.manager)

    def test_one_tab_per_session(self):
        self.assertEqual(self.tabs.tab_view.get_n_pages(), 1)
        page = self.tabs.tab_view.get_nth_page(0)
        # 标题 = 完整工作目录（与编辑器标签页显示完整文件路径一致）。
        self.assertEqual(page.get_title(), '/tmp/project')
        tooltip = page.get_tooltip()
        self.assertIn('pi', tooltip)
        self.assertIn('/tmp/project', tooltip)
        self.assertIn('Session active', tooltip)

    def test_zero_tabs_shows_placeholder_card(self):
        self.manager.remove(self.session)
        settle()
        self.assertEqual(self.tabs.tab_view.get_n_pages(), 0)
        self.assertEqual(self.tabs.content_stack.get_visible_child_name(), 'empty')

    def test_placeholder_returns_to_tabs_after_new_session(self):
        self.manager.remove(self.session)
        settle()
        self.assertEqual(self.tabs.content_stack.get_visible_child_name(), 'empty')
        self.manager.new_session()
        settle()
        self.assertEqual(self.tabs.content_stack.get_visible_child_name(), 'tabs')

    def test_new_session_adds_and_selects_tab(self):
        session = self.manager.new_session()
        settle()
        self.assertEqual(self.tabs.tab_view.get_n_pages(), 2)
        page = self.tabs.tab_view.get_selected_page()
        self.assertIs(self.tabs._page_sessions.get(page), session)

    def test_selecting_tab_updates_active_session(self):
        other = self.manager.new_session()
        settle()
        first_page = self.tabs.tab_view.get_nth_page(0)
        self.tabs.tab_view.set_selected_page(first_page)
        settle()
        self.assertIs(self.manager.active, self.session)
        self.assertIsNot(self.manager.active, other)

    def test_closing_tab_ends_and_removes_session(self):
        self.manager.new_session()
        settle()
        page = self.tabs.tab_view.get_nth_page(0)
        self.tabs.tab_view.close_page(page)
        settle()
        self.assertEqual(len(self.manager.sessions), 1)
        self.assertEqual(self.tabs.tab_view.get_n_pages(), 1)
        self.assertEqual(self.session.state, 'idle')

    def test_single_session_keeps_tab_visible(self):
        self.assertFalse(self.tabs.tab_bar.get_autohide())

    def test_popped_out_flips_pop_button(self):
        self.tabs.set_popped_out(True)
        self.assertEqual(self.tabs.pop_button.get_icon_name(), 'view-restore-symbolic')
        self.tabs.set_popped_out(False)
        self.assertEqual(self.tabs.pop_button.get_icon_name(), 'window-new-symbolic')


if __name__ == '__main__':
    unittest.main()

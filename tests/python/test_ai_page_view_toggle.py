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

'''AI 页视图档位（批注 / Agent 终端标签页）接线测试。

契约：工具栏 .round ToggleGroup 是单一事实源，控制器 notify::active 回调
驱动内容栈；弹出独立窗口时会话标签区从栈里整体摘下、档位弹回批注，
收回时装回并落在终端档；弹出态程序化切到终端档 → 抬起独立窗口并弹回
批注档；reveal_terminal 仅在未弹出时切档。

无显示环境时整体跳过（守卫原因见 test_agent_terminal_tabs.py docstring）。
'''

import builtins
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, GObject, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

# Observable 是纯 Python 辅助类，不依赖显示服务，无头环境也必须能导入：
# 下面的 FakeSessions 在模块级继承它，若放进 gtk_ready 分支会在 CI 无头
# 环境下抛 NameError，导致整个测试模块加载失败。
from setzer.helpers.observable import Observable

if gtk_ready:
    from setzer.workspace.sidebar.ai_page.ai_page import AiPage


class FakeSettings:
    def get_value(self, section, key):
        return None


class FakeSession(GObject.Object):
    __gsignals__ = {'state_changed': (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__()
        self.settings = FakeSettings()
        self.state = 'idle'
        self.terminal = None
        self.session_cwd = None


class FakeSessions(Observable):
    '''AgentSessions 的最小替身（只覆盖 AiPage / 标签页用到的部分）。'''

    def __init__(self, session=None):
        Observable.__init__(self)
        self.sessions = [session] if session is not None else []
        self.active = session

    def ensure_default(self):
        if not self.sessions:
            return self.new_session()
        return self.active

    def new_session(self):
        session = FakeSession()
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
        self.sessions.remove(session)
        self.add_change_code('session_removed', session)


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class AiPageViewToggleTest(unittest.TestCase):

    def setUp(self):
        self.session = FakeSession()
        self.manager = FakeSessions(self.session)
        self.workspace = mock.Mock()
        self.workspace.get_agent_sessions.return_value = self.manager
        self.workspace.open_documents = []
        self.ai_page = AiPage(self.workspace)
        self.view = self.ai_page.view

    def test_toggle_construction_and_initial_state(self):
        group = self.view.view_toggle
        self.assertTrue(group.has_css_class('round'))
        self.assertEqual(
            [group.get_toggle_by_name(n).get_name() for n in ('annotations', 'agent')],
            ['annotations', 'agent'])
        self.assertEqual(group.get_active_name(), 'annotations')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'annotations')

    def test_toggle_switch_updates_content_stack(self):
        self.view.set_active_view('agent')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'agent')
        self.view.set_active_view('annotations')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'annotations')

    def test_detach_agent_panel_removes_terminal_and_reverts(self):
        self.view.set_active_view('agent')
        self.view.detach_agent_panel()
        self.assertIsNone(self.view.content_stack.get_child_by_name('agent'))
        self.assertEqual(self.view.view_toggle.get_active_name(), 'annotations')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'annotations')

    def test_attach_agent_panel_restores_on_agent_view(self):
        self.view.detach_agent_panel()
        self.view.attach_agent_panel()
        self.assertIsNotNone(self.view.content_stack.get_child_by_name('agent'))
        self.assertEqual(self.view.view_toggle.get_active_name(), 'agent')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'agent')

    def test_view_toggle_while_popped_out_presents_and_reverts(self):
        self.ai_page.terminal_tabs._popped_out = True
        # set_active_view('agent') 经真实 notify::active 链进入 on_view_toggle。
        self.view.set_active_view('agent')
        self.workspace.pop_out_agent_terminal.assert_called_once()
        self.assertEqual(self.view.view_toggle.get_active_name(), 'annotations')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'annotations')

    def test_reveal_terminal_switches_only_when_docked(self):
        self.ai_page.reveal_terminal()
        self.assertEqual(self.view.view_toggle.get_active_name(), 'agent')
        self.assertEqual(self.view.content_stack.get_visible_child_name(), 'agent')
        self.view.set_active_view('annotations')
        self.ai_page.terminal_tabs._popped_out = True
        self.ai_page.reveal_terminal()
        self.assertEqual(self.view.view_toggle.get_active_name(), 'annotations')

    def test_starts_without_tab_when_no_session(self):
        '''初次启动不预建会话：零标签 + 占位卡片。'''
        manager = FakeSessions()
        workspace = mock.Mock()
        workspace.get_agent_sessions.return_value = manager
        workspace.open_documents = []
        ai_page = AiPage(workspace)
        self.assertEqual(ai_page.terminal_tabs.tab_view.get_n_pages(), 0)
        self.assertEqual(
            ai_page.terminal_tabs.content_stack.get_visible_child_name(), 'empty')

    def test_new_session_button_adds_tab(self):
        before = len(self.manager.sessions)
        self.manager.new_session()
        self.assertEqual(len(self.manager.sessions), before + 1)
        # 标签页随 session_added 同步新增。
        self.assertEqual(self.ai_page.terminal_tabs.tab_view.get_n_pages(), before + 1)


if __name__ == '__main__':
    unittest.main()

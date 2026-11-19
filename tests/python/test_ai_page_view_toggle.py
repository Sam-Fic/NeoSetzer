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

'''AI 页视图档位（批注 / Agent 终端）接线测试。

契约：工具栏 .round ToggleGroup 是单一事实源，控制器 notify::active 回调
驱动内容栈；弹出独立窗口时终端从栈里整体摘下、档位弹回批注，收回时装回
并落在终端档；弹出态程序化切到终端档 → 抬起独立窗口并弹回批注档；
reveal_terminal 仅在未弹出时切档。

无显示环境时整体跳过（守卫原因见 test_agent_terminal_headerbar.py docstring）。
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


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class AiPageViewToggleTest(unittest.TestCase):

    def setUp(self):
        self.session = FakeSession()
        self.workspace = mock.Mock()
        self.workspace.get_agent_session.return_value = self.session
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
        self.ai_page.terminal_view._popped_out = True
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
        self.ai_page.terminal_view._popped_out = True
        self.ai_page.reveal_terminal()
        self.assertEqual(self.view.view_toggle.get_active_name(), 'annotations')


if __name__ == '__main__':
    unittest.main()

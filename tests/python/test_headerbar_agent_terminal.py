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

'''顶栏「Open AI agent in terminal」按钮落点测试。

契约：内嵌终端路径下，侧栏开启 → 侧栏 AI 页启动/复用会话；
侧栏未开 → 默认弹出独立窗口，不强行展开侧栏。
'''

import builtins
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

from setzer.ai_fix import agent_runner, agent_session
from setzer.workspace.headerbar.headerbar import HeaderBar


class FakeSettings:
    def get_value(self, section, key):
        return {
            'ai_fix_enabled': True,
            'ai_fix_embedded_terminal': True,
            'ai_fix_active_tool': 'opencode',
            'ai_fix_tools': [{'name': 'opencode', 'executable': 'opencode'}],
        }.get(key)


class FakeDocument:
    def get_dirname(self):
        return '/tmp'


def make_headerbar(sidebar_open):
    bar = HeaderBar.__new__(HeaderBar)  # 绕过重构造，直测 handler
    workspace = mock.Mock()
    workspace.settings = FakeSettings()
    workspace.show_symbols = False
    workspace.show_document_structure = sidebar_open
    workspace.show_ai_agent = False
    # 第 4 个侧栏页布尔：不显式置 False 的话，MagicMock 会自动生成一个
    # 恒真的 Mock 属性，让 headerbar 的 sidebar_open 判断恒为 True。
    workspace.show_git = False
    workspace.get_active_document.return_value = FakeDocument()
    workspace.agent_terminal_window = None
    session = mock.Mock()
    session.ensure_started.return_value = (True, None)
    workspace.get_agent_session.return_value = session
    bar.workspace = workspace
    bar._toast = mock.Mock()
    return bar, workspace


class AgentTerminalPlacementTest(unittest.TestCase):

    def _click(self, bar):
        with mock.patch.object(agent_session, 'VTE_AVAILABLE', True), \
                mock.patch.object(agent_runner, 'resolve_tool_config',
                                  return_value={'name': 'opencode'}):
            bar.on_agent_terminal_clicked(None)

    def test_sidebar_closed_pops_out_window(self):
        bar, workspace = make_headerbar(sidebar_open=False)
        self._click(bar)
        workspace.pop_out_agent_terminal.assert_called_once()
        workspace.set_show_ai_agent.assert_not_called()

    def test_sidebar_open_stays_in_sidebar(self):
        bar, workspace = make_headerbar(sidebar_open=True)
        self._click(bar)
        workspace.set_show_ai_agent.assert_called_once_with(True)
        workspace.pop_out_agent_terminal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
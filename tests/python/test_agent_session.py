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

'''AgentSession 关闭会话测试。

契约：end_session 杀子进程、清空待发队列、清屏与滚回、回 idle
（恢复未打开状态）；「复制最近 prompt」已下沉到批注行，会话不再持有。
'''

import builtins
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

from setzer.ai_fix.agent_session import AgentSession


class FakeSettings:
    def get_value(self, section, key):
        return None


class FakeWorkspace:
    settings = FakeSettings()


class EndSessionTest(unittest.TestCase):

    def test_end_session_returns_to_idle_and_clears_pending(self):
        session = AgentSession(FakeWorkspace())
        session._set_state('running')
        session._pending.append('queued prompt')
        session.end_session()
        self.assertEqual(session.state, 'idle')
        self.assertEqual(session._pending, [])

    def test_end_session_resets_terminal(self):
        '''清屏 + 清滚回，下次 spawn是全新终端。'''
        session = AgentSession(FakeWorkspace())
        terminal = mock.Mock()
        session.terminal = terminal
        session.end_session()
        terminal.reset.assert_called_once_with(True, True)

    def test_copy_last_prompt_removed(self):
        '''复制 prompt 下沉到批注行（sender.copy_prompt），会话不再持有。'''
        session = AgentSession(FakeWorkspace())
        self.assertFalse(hasattr(session, 'copy_last_prompt'))
        self.assertFalse(hasattr(session, 'last_prompt'))


if __name__ == '__main__':
    unittest.main()
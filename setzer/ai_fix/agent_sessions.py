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

"""Workspace 级 Agent 会话集合：一个会话对应一个标签页。

单个 AgentSession 仍然负责一个 VTE 终端 + 一个 Agent CLI 子进程；本类只
管理「有哪几个会话、哪个是当前会话」，不做任何 GTK 视图操作。视图层
（AgentTerminalTabs）订阅以下变更码维护 Adw.TabView：

  - session_added(session)：新增会话（追加标签页）
  - session_removed(session)：会话被关闭（移除标签页）
  - active_changed(session)：当前会话变化（选中对应标签页）

设计约束：
  - `ensure_default()` 保证至少有一个会话（首次发送/打开终端时的兜底）。
  - `remove()` 先结束子进程再通知视图，避免移除标签后仍有后台进程。
  - 关闭标签页 = 结束该会话；不再提供单独的「结束会话」按钮。
"""

from setzer.helpers.observable import Observable


class AgentSessions(Observable):
    '''单个 workspace 的全部 Agent 会话（标签页的事实之源）。'''

    def __init__(self, workspace):
        Observable.__init__(self)
        self.workspace = workspace
        self.sessions = []
        self.active = None

    # ---- 查询 ----

    def ensure_default(self):
        '''返回当前会话；一个都没有时创建一个（不主动 spawn 子进程）。'''
        if not self.sessions:
            return self.new_session()
        if self.active not in self.sessions:
            self.active = self.sessions[0]
        return self.active

    def is_empty(self):
        return not self.sessions

    # ---- 增删 / 激活 ----

    def new_session(self):
        '''创建一个新会话（空终端，首次 send/ensure_started 才 spawn）。'''
        from setzer.ai_fix.agent_session import AgentSession
        session = AgentSession(self.workspace)
        self.sessions.append(session)
        self.active = session
        self.add_change_code('session_added', session)
        self.add_change_code('active_changed', session)
        return session

    def set_active(self, session):
        '''由标签页选中事件驱动；重复/未知会话直接忽略。'''
        if session is self.active or session not in self.sessions:
            return
        self.active = session
        self.add_change_code('active_changed', session)

    def remove(self, session):
        '''结束会话（杀子进程）并从集合移除；激活相邻会话。'''
        if session not in self.sessions:
            return
        index = self.sessions.index(session)
        self.sessions.remove(session)
        session.end_session()
        if self.active is session:
            if self.sessions:
                self.active = self.sessions[min(index, len(self.sessions) - 1)]
            else:
                self.active = None
            self.add_change_code('active_changed', self.active)
        self.add_change_code('session_removed', session)

    def shutdown_all(self):
        '''应用退出：结束所有子进程（不通知视图）。'''
        for session in list(self.sessions):
            session.shutdown()

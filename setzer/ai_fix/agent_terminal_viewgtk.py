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

"""单个 Agent 会话的终端内容：圆角卡片 + VTE / 状态页。

会话自身的打开、切换、关闭全部由 AgentTerminalTabs 的标签页管理，
本视图只负责「这一个会话」的终端画面：

  - idle：黑屏 VTE 没有信息量，显示「No active session」状态页；
  - starting / running / exited：显示终端（exited 保留滚回供读最后输出）；
  - VTE 不可用：显示说明页，发送会回退到外部终端。

卡片规范与 .editor-card / .preview-card 一致（style_gtk.css 的
.agent-terminal-card）：8px 圆角 + 6px 边距 + @card_bg_color，容器
set_overflow(HIDDEN) 把 VTE 内容裁剪进圆角。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.ai_fix.agent_session import VTE_AVAILABLE


def make_status_page(icon_name, title, description):
    '''与 document structure 空状态同款：.compact + .sidebar-empty-state
    （64px 图标 / 0.95em 标题 / 0.85em 描述），内部 ScrolledWindow
    propagate-natural-height 避免小高度时自滚动。'''
    status = Adw.StatusPage()
    status.set_icon_name(icon_name)
    status.set_title(title)
    status.set_description(description)
    status.add_css_class('compact')
    status.add_css_class('sidebar-empty-state')
    child = status.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.ScrolledWindow):
            child.set_propagate_natural_height(True)
            break
        child = child.get_next_sibling()
    return status


def make_status_card(icon_name, title, description):
    '''空会话占位：与终端卡片同款圆角底 + 状态页。

    零标签时 AgentTerminalTabs 用这块卡片占位（与单会话 idle 时的
    「No active session」完全一致），而不是露出一块空白。
    '''
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class('agent-terminal-card')
    card.set_overflow(Gtk.Overflow.HIDDEN)
    card.set_vexpand(True)
    status = make_status_page(icon_name, title, description)
    status.set_vexpand(True)
    card.append(status)
    return card


class AgentTerminalView(Gtk.Box):

    def __init__(self, session):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.session = session

        # ---- 圆角卡片（VTE / 无会话 status page / 回退说明）----
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class('agent-terminal-card')
        self.card.set_overflow(Gtk.Overflow.HIDDEN)
        self.card.set_vexpand(True)
        # 周边留白由 .agent-terminal-card 的 CSS margin 提供（0 6px 6px）。
        self.append(self.card)

        self.card_stack = Gtk.Stack()
        self.card_stack.set_vexpand(True)
        self.card.append(self.card_stack)

        self.idle_status = None
        if VTE_AVAILABLE and session.terminal is not None:
            session.terminal.set_vexpand(True)
            self.card_stack.add_named(session.terminal, 'terminal')
            self.idle_status = make_status_page(
                'utilities-terminal-symbolic', _('No active session'),
                _('Send an annotation or a selection to start the agent in this terminal.'))
            self.idle_status.set_vexpand(True)
            self.card_stack.add_named(self.idle_status, 'idle')
        else:
            status = make_status_page(
                'utilities-terminal-symbolic', _('Embedded terminal unavailable'),
                _('VTE is not available on this system. Sending will open the agent '
                  'in an external terminal instead; the prompt is also copied to '
                  'the clipboard.'))
            status.set_vexpand(True)
            self.card_stack.add_named(status, 'unavailable')

        session.connect('state_changed', self.on_session_state_changed)
        self.update_state()

    def on_session_state_changed(self, session, state):
        self.update_state()

    def update_state(self):
        '''卡片内容随会话状态切换：idle 显状态页，其余显终端。'''
        if self.idle_status is None:
            self.card_stack.set_visible_child_name('unavailable')
        elif self.session.state in ('starting', 'running', 'exited'):
            self.card_stack.set_visible_child_name('terminal')
        else:
            self.card_stack.set_visible_child_name('idle')

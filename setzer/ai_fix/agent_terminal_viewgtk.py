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

"""Agent 终端视图：迷你工具栏（卡片外）+ 圆角卡片（仅 VTE）。

卡片规范与 .editor-card / .preview-card 一致（style_gtk.css 的
.agent-terminal-card）：8px 圆角 + 6px 边距 + @card_bg_color，容器
set_overflow(HIDDEN) 把 VTE 内容裁剪进圆角。

整体（工具栏 + 卡片）是一个 Gtk.Box，弹出独立窗口时整体 reparent
（VTE 与 PTY 随 widget 搬移不中断，pdf_preview_window 先例）。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango

from setzer.ai_fix.agent_session import VTE_AVAILABLE


class AgentTerminalView(Gtk.Box):

    def __init__(self, session):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.session = session
        self._popped_out = False

        # ---- 迷你工具栏（卡片外上方，同 preview 工具栏布局）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')

        self.tool_label = Gtk.Label(label='')
        self.tool_label.add_css_class('dim-label')
        self.toolbar.append(self.tool_label)

        self.status_label = Gtk.Label(label='')
        self.status_label.add_css_class('dim-label')
        self.status_label.set_xalign(0.0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.toolbar.append(self.status_label)

        self.copy_button = Gtk.Button(icon_name='edit-copy-symbolic')
        self.copy_button.add_css_class('flat')
        self.copy_button.set_tooltip_text(_('Copy last prompt'))
        self.toolbar.append(self.copy_button)

        self.new_session_button = Gtk.Button(icon_name='view-refresh-symbolic')
        self.new_session_button.add_css_class('flat')
        self.new_session_button.set_tooltip_text(_('New session (in the active document’s directory)'))
        self.toolbar.append(self.new_session_button)

        self.pop_button = Gtk.Button(icon_name='window-new-symbolic')
        self.pop_button.add_css_class('flat')
        self.pop_button.set_tooltip_text(_('Open in a separate window'))
        self.toolbar.append(self.pop_button)

        self.append(self.toolbar)

        # ---- 圆角卡片（仅 VTE / 回退说明）----
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class('agent-terminal-card')
        self.card.set_overflow(Gtk.Overflow.HIDDEN)
        self.card.set_vexpand(True)
        self.append(self.card)

        if VTE_AVAILABLE and session.terminal is not None:
            session.terminal.set_vexpand(True)
            self.card.append(session.terminal)
        else:
            status = Adw.StatusPage()
            status.set_icon_name('utilities-terminal-symbolic')
            status.set_title(_('Embedded terminal unavailable'))
            status.set_description(_(
                'VTE is not available on this system. Sending will open the agent '
                'in an external terminal instead; the prompt is also copied to '
                'the clipboard.'))
            self.card.append(status)

        session.connect('state_changed', self.on_session_state_changed)
        self.update_labels()

    def on_session_state_changed(self, session, state):
        self.update_labels()

    def update_labels(self):
        settings = self.session.settings
        tool_name = settings.get_value('preferences', 'ai_fix_active_tool') or 'agent'
        self.tool_label.set_text(tool_name)
        state = self.session.state
        if state == 'running':
            text = _('Session active')
        elif state == 'starting':
            text = _('Starting…')
        elif state == 'exited':
            text = _('Session ended — next send starts a new one')
        else:
            text = _('No active session')
        self.status_label.set_text(text)

    def set_popped_out(self, popped_out):
        '''弹出/收回时切换按钮图标与提示（收回不杀会话）。'''
        self._popped_out = popped_out
        if popped_out:
            self.pop_button.set_icon_name('view-restore-symbolic')
            self.pop_button.set_tooltip_text(_('Dock back into the sidebar'))
        else:
            self.pop_button.set_icon_name('window-new-symbolic')
            self.pop_button.set_tooltip_text(_('Open in a separate window'))
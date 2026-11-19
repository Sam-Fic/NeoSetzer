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

弹出时迷你工具栏不跟着露出来：`attach_headerbar` 把 agent 名/会话状态
写进窗口标题栏的 Adw.WindowTitle，三个快捷按钮 reparent 进标题栏右侧，
于是独立窗口只有一层标题（收回时 `detach_headerbar` 原样复原）。
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
        # 独立窗口的标题栏接管状态（attach_headerbar / detach_headerbar）。
        self._headerbar = None
        self._header_title = None

        # ---- 迷你工具栏（卡片外上方，同 preview 工具栏布局）----
        # .subview-row 顶距归零：本视图只嵌入 AI 页工具栏之下，行间空隙由
        # 主工具栏的 7px 底距单独承担（见 style_gtk.css .subview-row）。
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.add_css_class('subview-row')

        self.tool_label = Gtk.Label(label='')
        self.tool_label.add_css_class('dimmed')
        self.toolbar.append(self.tool_label)

        self.status_label = Gtk.Label(label='')
        self.status_label.add_css_class('dimmed')
        self.status_label.set_xalign(0.0)
        self.status_label.set_hexpand(True)
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.toolbar.append(self.status_label)

        self.close_session_button = Gtk.Button(icon_name='process-stop-symbolic')
        self.close_session_button.add_css_class('flat')
        self.close_session_button.set_tooltip_text(_('End session and stop the agent'))
        self.close_session_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('End session and stop the agent')])
        self.toolbar.append(self.close_session_button)

        # 图标用 chat-message-new-symbolic（气泡+加号）：view-refresh 是"刷新"
        # 语义，容易被误读成重载当前会话；本按钮实际是开一个全新的 agent 会话。
        self.new_session_button = Gtk.Button(icon_name='chat-message-new-symbolic')
        self.new_session_button.add_css_class('flat')
        self.new_session_button.set_tooltip_text(_('New session (in the active document’s directory)'))
        self.new_session_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('New session (in the active document’s directory)')])
        self.toolbar.append(self.new_session_button)

        self.pop_button = Gtk.Button(icon_name='window-new-symbolic')
        self.pop_button.add_css_class('flat')
        self.pop_button.set_tooltip_text(_('Open in a separate window'))
        self.toolbar.append(self.pop_button)

        self.append(self.toolbar)

        # ---- 圆角卡片（VTE / 无会话 status page / 回退说明）----
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class('agent-terminal-card')
        self.card.set_overflow(Gtk.Overflow.HIDDEN)
        self.card.set_vexpand(True)
        self.append(self.card)

        # 卡片内用 Stack 切换：idle 时黑屏 VTE 无信息量，改显 status page
        # （与 document structure 空状态同款外观）；starting/running/exited
        # 显终端（exited 保留滚回供读最后输出）。
        self.card_stack = Gtk.Stack()
        self.card_stack.set_vexpand(True)
        self.card.append(self.card_stack)

        self.idle_status = None
        if VTE_AVAILABLE and session.terminal is not None:
            session.terminal.set_vexpand(True)
            self.card_stack.add_named(session.terminal, 'terminal')
            self.idle_status = self._make_status_page(
                'utilities-terminal-symbolic', _('No active session'),
                _('Send an annotation or a selection to start the agent in this terminal.'))
            self.idle_status.set_vexpand(True)
            self.card_stack.add_named(self.idle_status, 'idle')
        else:
            status = self._make_status_page(
                'utilities-terminal-symbolic', _('Embedded terminal unavailable'),
                _('VTE is not available on this system. Sending will open the agent '
                  'in an external terminal instead; the prompt is also copied to '
                  'the clipboard.'))
            status.set_vexpand(True)
            self.card_stack.add_named(status, 'unavailable')

        session.connect('state_changed', self.on_session_state_changed)
        self.update_labels()

    @staticmethod
    def _make_status_page(icon_name, title, description):
        '''与 document structure 空状态同款：.compact + .sidebar-empty-state
        （48px 图标 / 0.95em 标题 / 0.85em 描述），内部 ScrolledWindow
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
        # 关闭会话仅在非 idle 时有意义（idle 已是未打开状态）。
        self.close_session_button.set_sensitive(state != 'idle')
        # 卡片内容随状态切换：无会话（idle）显 status page，其余显终端。
        if self.idle_status is None:
            self.card_stack.set_visible_child_name('unavailable')
        elif state in ('starting', 'running', 'exited'):
            self.card_stack.set_visible_child_name('terminal')
        else:
            self.card_stack.set_visible_child_name('idle')
        self._sync_header_title()

    # ---- 独立窗口：单层标题栏集成 ----

    # 搬进标题栏的快捷按钮，顺序 = 侧栏工具栏里的显示顺序。
    _HEADER_BUTTONS = ('close_session_button', 'new_session_button', 'pop_button')

    def attach_headerbar(self, headerbar, title_widget):
        '''把迷你工具栏并进独立窗口的标题栏（窗口只剩一层标题）。

        - 工具栏隐藏（不再占一行）；
        - agent 名 / 会话状态写进 title_widget（Adw.WindowTitle 的
          标题与副标题），随 state_changed 更新；
        - 三个快捷按钮 reparent 到标题栏右侧（pack_end 要求无父，
          故先 unparent）。
        '''
        self._headerbar = headerbar
        self._header_title = title_widget
        self.toolbar.set_visible(False)
        # pack_end 是栈式插入（后 pack 的排在更左），反向遍历才能让标题栏
        # 里的左右顺序与侧栏工具栏一致：结束会话 / 新会话 / 收回侧栏。
        for name in reversed(self._HEADER_BUTTONS):
            button = getattr(self, name)
            button.unparent()
            headerbar.pack_end(button)
        self._sync_header_title()

    def detach_headerbar(self):
        '''收回侧栏：按钮搬回迷你工具栏（顺序复原）并恢复工具栏显示。'''
        headerbar = self._headerbar
        self._headerbar = None
        self._header_title = None
        if headerbar is not None:
            for name in self._HEADER_BUTTONS:
                button = getattr(self, name)
                if button.get_parent() is not None:
                    button.unparent()
                self.toolbar.append(button)
        self.toolbar.set_visible(True)

    def _sync_header_title(self):
        '''标题栏文字 = agent 名 + 会话状态（未接管时零开销）。'''
        title_widget = self._header_title
        if title_widget is None:
            return
        status = self.status_label.get_text()
        title_widget.set_title(self.tool_label.get_text() or _('Agent Terminal'))
        title_widget.set_subtitle(status)
        title_widget.set_tooltip_text(status or None)

    def set_popped_out(self, popped_out):
        '''弹出/收回时切换按钮图标与提示（收回不杀会话）。'''
        self._popped_out = popped_out
        if popped_out:
            self.pop_button.set_icon_name('view-restore-symbolic')
            self.pop_button.set_tooltip_text(_('Dock back into the sidebar'))
            self.pop_button.update_property(
                [Gtk.AccessibleProperty.LABEL], [_('Dock back into the sidebar')])
        else:
            self.pop_button.set_icon_name('window-new-symbolic')
            self.pop_button.set_tooltip_text(_('Open in a separate window'))
            self.pop_button.update_property(
                [Gtk.AccessibleProperty.LABEL], [_('Open in a separate window')])
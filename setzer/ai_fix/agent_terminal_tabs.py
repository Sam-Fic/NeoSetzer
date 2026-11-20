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

"""Agent 会话标签页视图：Adw.TabBar + Adw.TabView，一个会话一个标签。

会话的生命周期由标签页管理（同主文档编辑区）：
  - 「+」新建一个会话标签；
  - 标签上的关闭按钮结束并移除该会话（不再有单独的「结束会话」按钮）；
  - 切换标签 → AgentSessions.set_active；
  - AgentSessions 新增/移除会话（例如 AI 提交在后台新开会话）→ 同步标签页。

标签条右侧固定两个动作按钮：新建会话、弹出独立窗口。弹出时整个
TabBar + TabView 一起 reparent 到 AgentTerminalWindow，VTE 与 PTY 随
widget 搬移不中断。

Adw.TabPage 是 GObject（不是 Widget），成员判断用
`tab_view.get_page_position(page)`，不能用 `get_parent()`。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.ai_fix.agent_session import VTE_AVAILABLE
from setzer.ai_fix.agent_terminal_viewgtk import AgentTerminalView, make_status_card


def _status_text(state):
    if state == 'running':
        return _('Session active')
    if state == 'starting':
        return _('Starting…')
    if state == 'exited':
        return _('Session ended — next send starts a new one')
    return _('No active session')


class AgentTerminalTabs(Gtk.Box):

    def __init__(self, sessions):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.sessions = sessions
        self._popped_out = False
        # 程序化 set_selected_page / close_page_finish 会发 selected-page
        # notify，抑制计数器防止「视图 ↔ 管理器」互相回环。
        self._selecting = 0
        self._page_sessions = dict()
        self._session_pages = dict()
        # 由用户关闭（close-page 已发起）的页面：session_removed 时直接
        # close_page_finish；程序化移除的页面要先过 close_page 协议。
        self._closing_pages = set()

        # ---- 标签页 ----
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        self.tab_view.connect('close-page', self._on_close_page)
        self.tab_view.connect('notify::selected-page',
                              self._on_selected_page_changed)

        self.tab_bar = Adw.TabBar()
        # .inline：标签条走普通底色（与侧栏其它控件同一条视觉带），
        # libadwaita 文档对「非 headerbar 上下文」的推荐用法。
        self.tab_bar.add_css_class('inline')
        # 单标签时 libadwaita 会取消选中背景（正常 TabBar 会 autohide）。
        # 会话标签 autohide=False（关闭入口在标签上），用专属类补回高亮。
        self.tab_bar.add_css_class('agent-session-tabs')
        self.tab_bar.set_view(self.tab_view)
        # 单个会话也要保留标签（关闭入口在标签上），因此不自动隐藏。
        self.tab_bar.set_autohide(False)

        # ---- 标签条 + 右侧动作（同一行）----
        # 动作按钮不放进 TabBar 的 end_action_widget：塞进 tabbox 会把
        # .inline 标签条从 40px 撑到 46px（按钮 34px + 上下 padding），
        # 标签上方凭空多一条 6px 空隙。放到行内与 TabBar 并排，高度与
        # 编辑器标签条一致。
        self.tab_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        # 与 .sidebar-toolbar 的 10px 边缘线对齐：TabBar 自带 6px 内边距，
        # 左侧补 4px；右侧动作按钮直接贴 10px（--setzer-spacing-edge）。
        self.tab_row.set_margin_start(4)
        self.tab_row.set_margin_end(10)
        self.tab_bar.set_hexpand(True)

        self.new_session_button = Gtk.Button(icon_name='chat-message-new-symbolic')
        self.new_session_button.add_css_class('flat')
        # START：AdwTab 本体在 40px 标签条里是顶部对齐（34px，y=0），
        # 居中会低 3px，与标签顶边错位。
        self.new_session_button.set_valign(Gtk.Align.START)
        self.new_session_button.set_tooltip_text(
            _('New session (in the active document’s directory)'))
        self.new_session_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('New session (in the active document’s directory)')])

        self.pop_button = Gtk.Button(icon_name='window-new-symbolic')
        self.pop_button.add_css_class('flat')
        self.pop_button.set_valign(Gtk.Align.START)
        self.pop_button.set_tooltip_text(_('Open in a separate window'))
        self.pop_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Open in a separate window')])

        self.tab_row.append(self.tab_bar)
        self.tab_row.append(self.new_session_button)
        self.tab_row.append(self.pop_button)
        self.append(self.tab_row)

        # ---- 内容：标签页 / 零标签占位卡片 ----
        # 所有标签都关掉后不露空白：显示与单会话 idle 时同款的
        # 「No active session」卡片（VTE 不可用时显示回退说明）。
        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        self.content_stack.add_named(self.tab_view, 'tabs')
        if VTE_AVAILABLE:
            self.empty_card = make_status_card(
                'utilities-terminal-symbolic', _('No active session'),
                _('Send an annotation or a selection to start the agent in this terminal.'))
        else:
            self.empty_card = make_status_card(
                'utilities-terminal-symbolic', _('Embedded terminal unavailable'),
                _('VTE is not available on this system. Sending will open the agent '
                  'in an external terminal instead; the prompt is also copied to '
                  'the clipboard.'))
        self.content_stack.add_named(self.empty_card, 'empty')
        self.append(self.content_stack)

        # ---- 会话集合 → 标签页 ----
        sessions.connect('session_added', self._on_session_added)
        sessions.connect('session_removed', self._on_session_removed)
        sessions.connect('active_changed', self._on_active_changed)
        for session in list(sessions.sessions):
            self._add_page(session)
        if sessions.active is not None:
            self._select_session(sessions.active)
        self._sync_empty_state()

    # ---- 标签页同步 ----

    def _add_page(self, session):
        if session in self._session_pages:
            return
        view = AgentTerminalView(session)
        page = self.tab_view.append(view)
        self._page_sessions[page] = session
        self._session_pages[session] = page
        session.connect('state_changed', self._on_session_state_changed)
        self._refresh_page_label(session, page)
        self._sync_empty_state()

    def _sync_empty_state(self):
        '''零标签 → 占位卡片；否则显示标签页。'''
        self.content_stack.set_visible_child_name(
            'tabs' if self._session_pages else 'empty')

    def _refresh_page_label(self, session, page=None):
        page = page or self._session_pages.get(session)
        if page is None:
            return
        settings = session.settings
        tool = settings.get_value('preferences', 'ai_fix_active_tool') or 'agent'
        # 标题与主编辑器标签页一致：显示完整工作目录（编辑器显示完整文件
        # 路径）；尚未 spawn 时退回工具名。工具名 / 状态放 tooltip。
        page.set_title(session.session_cwd or tool)
        parts = [tool]
        if session.session_cwd:
            parts.append(session.session_cwd)
        parts.append(_status_text(session.state))
        page.set_tooltip('\n'.join(parts))

    def _on_session_state_changed(self, session, state):
        self._refresh_page_label(session)

    def _on_session_added(self, sessions, session):
        self._add_page(session)

    def _on_session_removed(self, sessions, session):
        page = self._session_pages.pop(session, None)
        if page is None:
            return
        self._page_sessions.pop(page, None)
        try:
            # Observable 的 disconnect(code, callback)；GObject 版本只收
            # handler id，回退到 disconnect_by_func（测试替身走这条路）。
            session.disconnect('state_changed', self._on_session_state_changed)
        except TypeError:
            try:
                session.disconnect_by_func(self._on_session_state_changed)
            except (AttributeError, TypeError):
                pass
        self._sync_empty_state()
        if page in self._closing_pages:
            # 用户点标签关闭：close-page 已发起，补 finish 即完成移除。
            self._closing_pages.discard(page)
            self._finish_page(page)
        else:
            # 程序化移除（例如 AI 提交失败回滚）：先走 close_page 协议，
            # 由 close-page 回调负责 finish（Adw 要求 finish 前处于 closing）。
            self.tab_view.close_page(page)

    def _finish_page(self, page):
        self._selecting += 1
        try:
            position = self.tab_view.get_page_position(page)
            if position != Gtk.INVALID_LIST_POSITION:
                self.tab_view.close_page_finish(page, True)
        finally:
            self._selecting -= 1

    def _on_active_changed(self, sessions, session=None):
        if session is not None:
            self._select_session(session)

    def _select_session(self, session):
        page = self._session_pages.get(session)
        if page is None or self.tab_view.get_selected_page() is page:
            return
        self._selecting += 1
        try:
            self.tab_view.set_selected_page(page)
        finally:
            self._selecting -= 1

    # ---- 标签操作 ----

    def _on_selected_page_changed(self, tab_view, pspec=None):
        if self._selecting:
            return
        page = tab_view.get_selected_page()
        session = self._page_sessions.get(page) if page is not None else None
        if session is not None:
            self.sessions.set_active(session)

    def _on_close_page(self, tab_view, page):
        '''标签关闭 = 结束该会话；页面实际移除交给 session_removed。'''
        session = self._page_sessions.get(page)
        if session is None:
            # 程序化移除路径发起的 close_page：会话已不在映射里，直接 finish。
            self._finish_page(page)
            return True
        self._closing_pages.add(page)
        self.sessions.remove(session)
        return True

    # ---- 弹出 / 收回 ----

    def set_popped_out(self, popped_out):
        '''切换弹出按钮的图标与提示；收回不杀任何会话。'''
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

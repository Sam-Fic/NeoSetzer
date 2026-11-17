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

'''Agent 终端独立窗口（弹出/收回）。

仿 pdf_preview_window 的「整体 reparent」模式：把 AI 页的终端区
（迷你工具栏 + 圆角卡片 + VTE）整体搬进本窗口内容区，VTE 与 PTY 随
widget 搬移不中断会话。

行为：
- set_transient_for(main_window) + 非 modal：仅相对 NeoSetzer 置顶
  （多数 WM 下 transient 窗口保持在父窗口之上），不是系统级置顶。
- 关窗（X）= 结束会话（kill 子进程）+ 收回隐藏；下次发送自动开新会话。
- 工具栏「收回侧栏」按钮 = reparent 回 AI 页且不杀会话。

标题栏只有一层：内容区里的迷你工具栏被并进 HeaderBar —— agent 名与
会话状态填进 Adw.WindowTitle（标题 / 副标题），三个快捷按钮
（结束会话 / 新会话 / 收回侧栏）pack_end 到标题栏右侧。
'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.app.service_locator import ServiceLocator


class AgentTerminalWindow(Adw.Window):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()

        Adw.Window.__init__(self)
        if self.main_window is not None:
            self.set_transient_for(self.main_window)
        self.set_modal(False)
        self.set_title(_('Agent Terminal'))
        self.set_default_size(720, 520)

        self.toolbar_view = Adw.ToolbarView()
        self.headerbar = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title=_('Agent Terminal'))
        self.headerbar.set_title_widget(self.window_title)
        self.toolbar_view.add_top_bar(self.headerbar)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.content_box)
        self.set_content(self.toolbar_view)

        self._panel = None
        # 关窗 = 结束会话 + 收回（不销毁窗口对象，保留几何状态）。
        self.connect('close-request', self._on_close_request)

    def set_panel(self, panel):
        self._panel = panel
        self.content_box.append(panel)
        # 终端视图把状态/按钮交给标题栏，避免标题栏 + 迷你工具栏两层。
        attach = getattr(panel, 'attach_headerbar', None)
        if attach is not None:
            attach(self.headerbar, self.window_title)

    def take_panel(self):
        if self._panel is not None:
            try:
                detach = getattr(self._panel, 'detach_headerbar', None)
                if detach is not None:
                    detach()
                self.content_box.remove(self._panel)
            except Exception:
                pass
            self._panel = None

    def _on_close_request(self, window):
        self.workspace.pop_in_agent_terminal(end_session=True)
        return True
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

"""侧栏 AI 页视图：批注列表（上）+ Agent 终端卡片（下）。

上半部普通平铺 Gtk.ListBox（不做文件分组头）；下半部终端区由
AgentTerminalView 提供（迷你工具栏在卡片外、圆角卡片仅 VTE）。
两半用垂直 Gtk.Paned 分隔，用户可拖动调整占比。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.workspace.sidebar.sidebar_viewgtk import build_panel_switch_group


class AiPageView(Gtk.Box):

    def __init__(self, terminal_view):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        # ---- 顶部工具栏（与其他侧栏页同款 .sidebar-toolbar）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)

        # 直发选区时使用的默认预制提示词（批注各自携带自己的 preset）。
        self.preset_dropdown = Gtk.DropDown()
        self.preset_dropdown.set_hexpand(True)
        self.preset_dropdown.set_tooltip_text(_('Preset used when sending a selection directly'))
        self.toolbar.append(self.preset_dropdown)

        self.send_all_button = Gtk.Button(label=_('Send All'))
        self.send_all_button.add_css_class('suggested-action')
        self.send_all_button.set_tooltip_text(_('Send all annotations to the agent'))
        self.toolbar.append(self.send_all_button)

        # 与其他页互为镜像的面板切换器（sidebar_viewgtk 统一同步）。
        self.switch_group = build_panel_switch_group()
        self.toolbar.append(self.switch_group)

        self.append(self.toolbar)

        # ---- 垂直 Paned：批注列表 / 终端 ----
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.paned.set_vexpand(True)
        self.paned.set_wide_handle(True)

        top_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.list_stack = Gtk.Stack()
        self.list_stack.set_vexpand(True)

        self.list_scrolled = Gtk.ScrolledWindow()
        self.list_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_scrolled.set_vexpand(True)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')
        self.list_box.set_margin_top(6)
        self.list_box.set_margin_bottom(6)
        self.list_box.set_margin_start(6)
        self.list_box.set_margin_end(6)
        self.list_scrolled.set_child(self.list_box)
        self.list_stack.add_named(self.list_scrolled, 'list')

        self.empty_status = Adw.StatusPage()
        self.empty_status.set_icon_name('ai-slop-symbolic')
        self.empty_status.set_title(_('No Annotations'))
        self.empty_status.set_description(_(
            'Select text in the editor and use the right-click menu or shortcut '
            'to add an AI annotation.'))
        self.list_stack.add_named(self.empty_status, 'empty')
        self.list_stack.set_visible_child_name('empty')

        top_box.append(self.list_stack)
        self.paned.set_start_child(top_box)
        self.paned.set_resize_start_child(True)
        self.paned.set_shrink_start_child(False)

        self.terminal_view = terminal_view
        self.paned.set_end_child(terminal_view)
        self.paned.set_resize_end_child(True)
        self.paned.set_shrink_end_child(False)

        self.append(self.paned)

        # 初始占比：列表约 40%。Paned 未分配尺寸前 set_position 无效，
        # 延迟到首次 size allocate 后按比例设置（仅一次）。
        self._position_initialized = False
        self.paned.connect('notify::position', self._on_position_notify)
        self.paned.connect('map', self._on_paned_map)

    def _on_paned_map(self, paned):
        if self._position_initialized:
            return
        height = paned.get_allocated_height()
        if height > 0:
            self._position_initialized = True
            paned.set_position(int(height * 0.4))

    def _on_position_notify(self, paned, pspec):
        # 用户拖动后不再自动重置。
        self._position_initialized = True

    def set_has_annotations(self, has_annotations):
        self.list_stack.set_visible_child_name('list' if has_annotations else 'empty')
        self.send_all_button.set_sensitive(has_annotations)
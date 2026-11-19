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

上半部批注列表用与 document structure 页同款组件栈：
Adw.PreferencesPage → PreferencesGroup(.sidebar-section-group) →
ListBox(.boxed-list)。列表不设 vexpand，只占内容自然高度，空白处
露出页面背景；到侧栏两边的间距由 Adw 样式表提供，与结构页一致。
唯一例外：PreferencesPage 默认的 24px 上外边距在 style_gtk.css 里对
`.ai-page` 清零，让首条批注顶边与工具栏下沿（= 右侧标签条 / 预览卡片顶边）
齐平——本页分组没有标题，不需要那点呼吸空间。
下半部终端区由 AgentTerminalView 提供（迷你工具栏在卡片外、圆角卡片仅 VTE）。
两半用垂直 Gtk.Paned 分隔，用户可拖动调整占比。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango

from setzer.workspace.sidebar.sidebar_viewgtk import build_panel_switch_group


class AiPageView(Gtk.Box):

    def __init__(self, terminal_view):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        # 供 style_gtk.css 精确作用到本页（去掉批注列表默认的 24px 上外边距，
        # 让首条批注顶边与工具栏下沿 / 标签条顶边对齐）。
        self.add_css_class('ai-page')

        # ---- 顶部工具栏（与其他侧栏页同款 .sidebar-toolbar）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)

        # 直发选区时使用的默认预制提示词（批注各自携带自己的 preset）。
        # 与 document structure 的 section 菜单同款：Gtk.MenuButton + .flat +
        # label + 右箭头，popover 用 .menu 样式（控制器侧接 Gio.Menu）。
        self.preset_menu_button = Gtk.MenuButton()
        self.preset_menu_button.add_css_class('flat')
        self.preset_menu_button.set_can_focus(True)
        self.preset_menu_button.set_halign(Gtk.Align.START)
        self.preset_menu_button.set_hexpand(True)
        self.preset_menu_button.set_tooltip_text(_('Preset used when sending a selection directly'))
        self.preset_menu_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Preset used when sending a selection directly')])

        menu_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.preset_menu_label = Gtk.Label(label='')
        # 正常正文颜色（不用 dim-label 灰字）。
        self.preset_menu_label.set_xalign(0.0)
        self.preset_menu_label.set_ellipsize(Pango.EllipsizeMode.END)
        menu_box.append(self.preset_menu_label)

        # 箭头与 Gtk.DropDown 内置箭头同款：16px 正常亮度（结构页旧版
        # 12px+dim 偏小偏灰，已统一修正）。
        arrow_icon = Gtk.Image(icon_name='pan-down-symbolic')
        arrow_icon.set_pixel_size(16)
        menu_box.append(arrow_icon)

        self.preset_menu_button.set_child(menu_box)
        self.toolbar.append(self.preset_menu_button)

        # Send All：纯图标按钮（名称由 tooltip 展示），图标与批注行的
        # 「发送单条」一致（mail-send-symbolic）。
        self.send_all_button = Gtk.Button()
        self.send_all_button.add_css_class('suggested-action')
        self.send_all_button.set_icon_name('mail-send-symbolic')
        self.send_all_button.set_tooltip_text(_('Send All'))
        self.send_all_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Send All')])
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

        # 与 document structure 页同款：PreferencesPage 自带滚动与边距，
        # ListBox 不 vexpand → 只占内容高度，下方空白是背景而非列表延伸。
        self.list_page = Adw.PreferencesPage()
        self.list_group = Adw.PreferencesGroup()
        self.list_group.add_css_class('sidebar-section-group')
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')
        self.list_group.add(self.list_box)
        self.list_page.add(self.list_group)
        self.list_stack.add_named(self.list_page, 'list')

        self.empty_status = Adw.StatusPage()
        self.empty_status.set_icon_name('ai-slop-symbolic')
        self.empty_status.set_title(_('No Annotations'))
        self.empty_status.set_description(_(
            'Select text in the editor and use the right-click menu or shortcut '
            'to add an AI annotation.'))
        # 与 document structure 空状态同款外观（48px 图标 / 紧凑字号）。
        self.empty_status.add_css_class('compact')
        self.empty_status.add_css_class('sidebar-empty-state')
        self.empty_status.set_vexpand(True)
        child = self.empty_status.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ScrolledWindow):
                child.set_propagate_natural_height(True)
                break
            child = child.get_next_sibling()
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
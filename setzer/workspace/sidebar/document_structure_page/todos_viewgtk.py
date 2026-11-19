#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
# along with this program. If not to, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Gio

import setzer.workspace.sidebar.document_structure_page.sidebar_list_viewgtk as sidebar_list_view
from setzer.app.service_locator import ServiceLocator


class TodosSectionView(sidebar_list_view.SidebarListView):

    def __init__(self, model):
        sidebar_list_view.SidebarListView.__init__(self, model)
        self.set_placeholder_info(
            'starred-symbolic',
            _('No to-dos'),
            _('Add \\todo{...} to keep track of tasks.')
        )
        # 右键菜单上下文：被右键的 todo，win.todo-ctx-* action 激活时读取。
        self._ctx_todo = None
        # 菜单为静态三项（Jump to / Copy / Mark as Done），常驻挂到
        # sidebar.menu_model；被右键的 todo 由 setup-menu 信号更新。
        self.sidebar.set_menu_model(self._build_menu_model())
        # 把上下文 action 注册到主窗口而非 PopoverMenu（见 _register_context_actions）。
        self._register_context_actions()

        # 「只看当前文档 / 汇总全部打开文档」两档切换：原为硬插 ListBox
        # 第 0 位的 SwitchRow（Adw.Sidebar 无任意 header 行概念），切换式
        # 重构后外置为页顶工具行。现按 ui-guidelines §4.4 用 Adw.ToggleGroup
        # 表达互斥模式（§6.3 恒有一档激活），替代原 ToggleButton 的
        # 「按下/未按下」模糊态——孤立开关看不出当前处于哪一档。
        # 初始档先设再连回调，避免程序化激活触发一次多余的通知。
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        tools.add_css_class('sidebar-subview-tools')
        self.show_all_group = Adw.ToggleGroup()
        self.show_all_group.set_valign(Gtk.Align.CENTER)
        # 全宽等宽分段：与上方子视图切换器同款布局语言。
        self.show_all_group.set_hexpand(True)
        self.show_all_group.set_homogeneous(True)
        self.show_all_group.add(Adw.Toggle(name='current', label=_('This document')))
        self.show_all_group.add(Adw.Toggle(name='all', label=_('All documents')))
        self.show_all_group.set_active_name(
            'all' if self.model._show_all else 'current')
        self.show_all_group.connect('notify::active', self._on_show_all_activated)
        tools.append(self.show_all_group)
        self.header_box.append(tools)

    def _on_show_all_activated(self, group, pspec):
        self.model.toggle_show_all(group.get_active_name() == 'all')

    def populate(self):
        # 签名 = id(document) + 全部 todo 文本元组。按键不动 \todo 时签名命中。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple(todo[0] for todo in self.model.todos))
        if not self.populate_if_changed(signature):
            return
        self.clear_items()
        # 按来源文档分组（组内保持 offset 排序）；show_all 关闭时只有当前
        # 文档一组。
        for todo in self.model.todos:
            document = todo[2]
            key = id(document)
            if key not in self._sections:
                self.add_section(key, document.get_displayname())
            item = self.make_item(todo[0], icon_name='starred-symbolic')
            item.item_data = todo
            self.append_item(key, item)
        self.select_first()

    def _build_menu_model(self):
        menu = Gio.Menu()

        # Navigation section
        nav_section = Gio.Menu()
        nav_section.append_item(Gio.MenuItem.new(_('Jump to'), 'win.todo-ctx-jump-to'))
        menu.append_section(None, nav_section)

        # Actions section
        actions_section = Gio.Menu()
        actions_section.append_item(Gio.MenuItem.new(_('Copy'), 'win.todo-ctx-copy'))
        actions_section.append_item(Gio.MenuItem.new(_('Mark as Done'), 'win.todo-ctx-delete'))
        menu.append_section(None, actions_section)

        return menu

    def _register_context_actions(self):
        '''在 main_window 上注册带 win. 前缀的上下文 action。

        把 action 注册到窗口而非 PopoverMenu，可避免 Gtk.PopoverMenu 基于
        menu model 渲染的菜单项在点击时无法解析到 action group 的问题
        （与 files_viewgtk 的修复相同，见 commit 9c8e0c23）。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        if main_window.lookup_action('todo-ctx-jump-to') is not None:
            return  # 已经注册过

        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            main_window.add_action(action)

        add('todo-ctx-jump-to', self._on_action_jump_to)
        add('todo-ctx-copy', self._on_action_copy)
        add('todo-ctx-delete', self._on_action_delete)

    def _on_setup_menu(self, sidebar, item):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()
        # 记录被右键的 todo（点空白处时 item 为 None）。
        self._ctx_todo = getattr(item, 'item_data', None) if item is not None else None

    def _on_action_jump_to(self, action, parameter):
        self.model.jump_to_todo(self._ctx_todo)

    def _on_action_copy(self, action, parameter):
        self.model.copy_todo(self._ctx_todo)

    def _on_action_delete(self, action, parameter):
        self.model.delete_todo(self._ctx_todo)

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
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Gio, Adw

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget
from setzer.app.service_locator import ServiceLocator


class TodosSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'starred-symbolic',
            _('No to-dos'),
            _('Add \\todo{...} to keep track of tasks.')
        )
        # 右键菜单上下文：被右键的 todo，win.todo-ctx-* action 激活时读取。
        self._ctx_todo = None
        # 把上下文 action 注册到主窗口而非 PopoverMenu（见 _register_context_actions）。
        self._register_context_actions()

        # 添加「Show all open documents」toggle
        self.show_all_row = Adw.SwitchRow()
        self.show_all_row.set_title(_('Show all open documents'))
        self.show_all_row.set_active(self.model._show_all)
        self.show_all_row.connect('notify::active', self._on_show_all_toggled)

        # 将 toggle 行插入到列表顶部（在 populate 的 rows 之前）
        # 子类化 StructureWidget 的 ListBox，在 populate 顶部插入
        listbox = self._get_listbox()
        if listbox is not None:
            listbox.insert(self.show_all_row, 0)

    def _get_listbox(self):
        '''从父类 StructureWidget 取得内部 ListBox。
        StructureWidget 自建 ListBox 作为 self 的第一个子控件。'''
        child = self.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ListBox):
                return child
            child = child.get_next_sibling()
        return None

    def _on_show_all_toggled(self, switch, gparam):
        active = switch.get_active()
        self.model.toggle_show_all(active)

    def populate(self):
        # 签名 = id(document) + 全部 todo 文本元组。按键不动 \todo 时签名命中。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple(todo[0] for todo in self.model.todos))
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()
        for todo in self.model.todos:
            row = self.make_row('starred-symbolic', todo[0], 0)
            row.item_data = todo
            self.append_row(row)
        self.set_empty_state_visible(len(self.model.todos) == 0)
        self._sync_selection_to_accent_row()

    def make_row(self, icon_name, text, indent):
        row = super().make_row(icon_name, text, indent)
        # Right-click context menu
        row.right_click_gesture = Gtk.GestureClick()
        row.right_click_gesture.set_button(3)
        row.right_click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        row.right_click_gesture.connect('pressed', self._on_row_right_click_pressed, row)
        row.right_click_gesture.connect('released', self._on_row_right_click_released, row)
        row.add_controller(row.right_click_gesture)
        return row

    def _on_row_right_click_pressed(self, gesture, n_press, x, y, row):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_row_right_click_released(self, gesture, n_press, x, y, row):
        todo = getattr(row, 'item_data', None)
        if todo is None:
            return
        self._show_context_menu(row, todo, x, y)

    def _build_menu_model(self, todo):
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

    def _show_context_menu(self, row, todo, x, y):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()

        menu_model = self._build_menu_model(todo)

        # 记录被右键的 todo，win.todo-ctx-* action 激活时使用。
        self._ctx_todo = todo

        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)

        popover.set_offset(144, 0)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _on_action_jump_to(self, action, parameter):
        self.model.jump_to_todo(self._ctx_todo)

    def _on_action_copy(self, action, parameter):
        self.model.copy_todo(self._ctx_todo)

    def _on_action_delete(self, action, parameter):
        self.model.delete_todo(self._ctx_todo)

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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


class TodosSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'starred-symbolic',
            _('No to-dos'),
            _('Add \\todo{...} to keep track of tasks.')
        )

        # 添加「Show all open documents」toggle
        self.show_all_row = Adw.ActionRow()
        self.show_all_row.set_title(_('Show all open documents'))
        self.show_all_switch = Gtk.Switch()
        self.show_all_switch.set_valign(Gtk.Align.CENTER)
        self.show_all_switch.set_active(self.model._show_all)
        self.show_all_switch.connect('notify::active', self._on_show_all_toggled)
        self.show_all_row.add_suffix(self.show_all_switch)
        self.show_all_row.set_activatable_widget(self.show_all_switch)

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
        nav_section.append_item(Gio.MenuItem.new(_('Jump to'), 'todo.jump-to'))
        menu.append_section(None, nav_section)

        # Actions section
        actions_section = Gio.Menu()
        actions_section.append_item(Gio.MenuItem.new(_('Copy'), 'todo.copy'))
        actions_section.append_item(Gio.MenuItem.new(_('Mark as Done'), 'todo.delete'))
        menu.append_section(None, actions_section)

        return menu

    def _build_action_group(self, todo):
        action_group = Gio.SimpleActionGroup()

        jump_action = Gio.SimpleAction.new('jump-to', None)
        jump_action.connect('activate', lambda a, p: self.model.jump_to_todo(todo))
        action_group.add_action(jump_action)

        copy_action = Gio.SimpleAction.new('copy', None)
        copy_action.connect('activate', lambda a, p: self.model.copy_todo(todo))
        action_group.add_action(copy_action)

        delete_action = Gio.SimpleAction.new('delete', None)
        delete_action.connect('activate', lambda a, p: self.model.delete_todo(todo))
        action_group.add_action(delete_action)

        return action_group

    def _show_context_menu(self, row, todo, x, y):
        menu_model = self._build_menu_model(todo)
        action_group = self._build_action_group(todo)

        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)
        popover.insert_action_group('todo', action_group)

        popover.set_offset(144, 0)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

        popover.connect('closed', lambda p: p.insert_action_group('todo', None))

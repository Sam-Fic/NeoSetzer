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
from gi.repository import Gtk, Gdk, Gio

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget
from setzer.app.service_locator import ServiceLocator


class LabelsSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'tag-symbolic',
            _('No labels'),
            _('Add \\label{...} to create references to figures, sections, and more.')
        )
        # 右键菜单上下文：被右键的 label，win.label-ctx-* action 激活时读取。
        self._ctx_label = None
        # 把上下文 action 注册到主窗口而非 PopoverMenu（见 _register_context_actions）。
        self._register_context_actions()

    def populate(self):
        # 签名 = id(document) + 全部 label 名称元组。按键不动 \label 时签名命中。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple(label[0] for label in self.model.labels))
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()
        for label in self.model.labels:
            row = self.make_row('tag-symbolic', label[0], 0)
            row.item_data = label
            self.append_row(row)
        self.set_empty_state_visible(len(self.model.labels) == 0)
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
        label = getattr(row, 'item_data', None)
        if label is None:
            return
        self._show_context_menu(row, label, x, y)

    def _build_menu_model(self, label):
        menu = Gio.Menu()

        # Navigation section
        nav_section = Gio.Menu()
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\ref{...}'), 'win.label-ctx-copy-ref'))
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\pageref{...}'), 'win.label-ctx-copy-pageref'))
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\autoref{...}'), 'win.label-ctx-copy-autoref'))
        menu.append_section(None, nav_section)

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
        if main_window.lookup_action('label-ctx-copy-ref') is not None:
            return  # 已经注册过

        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            main_window.add_action(action)

        add('label-ctx-copy-ref', self._copy_ref)
        add('label-ctx-copy-pageref', self._copy_pageref)
        add('label-ctx-copy-autoref', self._copy_autoref)

    def _show_context_menu(self, row, label, x, y):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()

        menu_model = self._build_menu_model(label)

        # 记录被右键的 label，win.label-ctx-* action 激活时使用。
        self._ctx_label = label

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

    def _copy_ref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\ref{' + label_name + '}')

    def _copy_pageref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\pageref{' + label_name + '}')

    def _copy_autoref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\autoref{' + label_name + '}')

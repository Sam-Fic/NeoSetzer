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
from gi.repository import Gtk, Gdk, Gio

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget


class LabelsSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'tag-symbolic',
            _('No labels'),
            _('Add \\label{...} to create references to figures, sections, and more.')
        )

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
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\ref{...}'), 'label.copy-ref'))
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\pageref{...}'), 'label.copy-pageref'))
        nav_section.append_item(Gio.MenuItem.new(_('Copy \\autoref{...}'), 'label.copy-autoref'))
        menu.append_section(None, nav_section)

        return menu

    def _build_action_group(self, label):
        action_group = Gio.SimpleActionGroup()

        label_name = label[0]

        copy_ref_action = Gio.SimpleAction.new('copy-ref', None)
        copy_ref_action.connect('activate', lambda a, p: self._copy_ref(label))
        action_group.add_action(copy_ref_action)

        copy_pageref_action = Gio.SimpleAction.new('copy-pageref', None)
        copy_pageref_action.connect('activate', lambda a, p: self._copy_pageref(label))
        action_group.add_action(copy_pageref_action)

        copy_autoref_action = Gio.SimpleAction.new('copy-autoref', None)
        copy_autoref_action.connect('activate', lambda a, p: self._copy_autoref(label))
        action_group.add_action(copy_autoref_action)

        return action_group

    def _show_context_menu(self, row, label, x, y):
        menu_model = self._build_menu_model(label)
        action_group = self._build_action_group(label)

        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)
        popover.insert_action_group('label', action_group)

        popover.set_offset(144, 0)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

        popover.connect('closed', lambda p: p.insert_action_group('label', None))

    def _copy_ref(self, label):
        label_name = label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\ref{' + label_name + '}')

    def _copy_pageref(self, label):
        label_name = label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\pageref{' + label_name + '}')

    def _copy_autoref(self, label):
        label_name = label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\autoref{' + label_name + '}')

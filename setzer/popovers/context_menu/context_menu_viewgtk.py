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
from gi.repository import Gtk, Gio, GLib


def _action_item(label, detailed_action, accel=None):
    '''A Gio.MenuItem for an action, optionally with a parseable accel.

    The ``accel`` attribute is rendered natively by Gtk.PopoverMenu as the
    shortcut label on the right (e.g. ``<Control>z`` -> "Ctrl+Z").'''
    item = Gio.MenuItem.new(label, detailed_action)
    if accel is not None:
        item.set_attribute_value('accel', GLib.Variant('s', accel))
    return item


class ContextMenuView(Gtk.PopoverMenu):
    '''Shortcutsbar "more" popover (the F12 context menu).

    Built from a ``Gio.Menu`` model on a native ``Gtk.PopoverMenu`` — the same
    form as the hamburger menu — instead of the former hand-built
    ListBox-in-popover. Action items use real GAction targets and parseable
    accelerators. The LaTeX-only items (Toggle Comment / Show in Preview) live
    in a section rebuilt on active-document changes. The Zoom controls are a
    custom child widget (so the buttons can trigger actions without closing
    the popover and the reset label can be updated dynamically).
    '''

    def __init__(self, popover_manager):
        Gtk.PopoverMenu.__init__(self)
        self.set_size_request(288, -1)

        self.latex_section = Gio.Menu()
        # 暴露 model 引用供共享：编辑器右键菜单（workspace ContextMenu.popover_pointer）
        # 用同一份 Gio.Menu model，使两者样式与内容完全一致（LaTeX section 重建对两者同效）。
        self.model = self._build_model()
        self.set_menu_model(self.model)
        self.add_child(self._build_zoom_widget(), 'zoom-controls')

    def _build_model(self):
        model = Gio.Menu()

        section_edit = Gio.Menu()
        section_edit.append_item(_action_item(_('Undo'), 'win.undo', '<Control>z'))
        section_edit.append_item(_action_item(_('Redo'), 'win.redo', '<Control><Shift>z'))
        model.append_section(None, section_edit)

        section_clip = Gio.Menu()
        section_clip.append_item(_action_item(_('Cut'), 'win.cut', '<Control>x'))
        section_clip.append_item(_action_item(_('Copy'), 'win.copy', '<Control>c'))
        section_clip.append_item(_action_item(_('Paste'), 'win.paste', '<Control>v'))
        section_clip.append_item(_action_item(_('Delete'), 'win.delete-selection'))
        model.append_section(None, section_clip)

        section_select = Gio.Menu()
        section_select.append_item(_action_item(_('Select All'), 'win.select-all', '<Control>a'))
        model.append_section(None, section_select)

        # LaTeX-only section: rebuilt via rebuild_latex_section().
        model.append_section(None, self.latex_section)

        # Zoom controls as a custom child row.
        zoom_section = Gio.Menu()
        zoom_item = Gio.MenuItem()
        zoom_item.set_attribute_value('custom', GLib.Variant('s', 'zoom-controls'))
        zoom_section.append_item(zoom_item)
        model.append_section(None, zoom_section)

        return model

    def _build_zoom_widget(self):
        box = Gtk.CenterBox()
        box.set_orientation(Gtk.Orientation.HORIZONTAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        zoom_label = Gtk.Label(label=_('Zoom'))
        box.set_start_widget(zoom_label)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        button_zoom_out = Gtk.Button()
        button_zoom_out.set_icon_name('value-decrease-symbolic')
        button_zoom_out.set_has_frame(False)
        button_zoom_out.set_action_name('win.zoom-out')
        inner_box.append(button_zoom_out)

        self.reset_zoom_button = Gtk.Button.new_with_label('100%')
        self.reset_zoom_button.set_has_frame(False)
        self.reset_zoom_button.set_action_name('win.reset-zoom')
        inner_box.append(self.reset_zoom_button)

        button_zoom_in = Gtk.Button()
        button_zoom_in.set_icon_name('value-increase-symbolic')
        button_zoom_in.set_has_frame(False)
        button_zoom_in.set_action_name('win.zoom-in')
        inner_box.append(button_zoom_in)

        box.set_end_widget(inner_box)
        return box

    def rebuild_latex_section(self, visible):
        '''Populate (or clear) the LaTeX-only section for the active document.'''
        self.latex_section.remove_all()
        if visible:
            self.latex_section.append_item(_action_item(_('Toggle Comment'), 'win.toggle-comment', '<Control>slash'))
            self.latex_section.append(_('Show in Preview'), 'win.forward-sync')

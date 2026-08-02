#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from gi.repository import Gtk, Adw


class AdwPopoverMenu(Gtk.Popover):
    '''Libadwaita-style popover menu: Gtk.Popover + Gtk.ListBox + Adw.ActionRow.

    This is the standard replacement for the hand-built StandardMenuPopover.
    Items are Adw.ActionRow rows inside a Gtk.ListBox with the ``boxed-list``
    CSS class, giving native keyboard navigation (Up/Down/Enter/Esc via
    Gtk.ListBox/Popover) and standard libadwaita row styling.

    Row kinds:
      - ``add_item``: action row. ``set_action_name`` is set so the GAction
        auto-triggers on activation (Adw.ActionRow's activate vfunc fires the
        action); the popover then closes.
      - ``add_callback_item`` / ``set_callback``: callback row. The stored
        Python callback is invoked on activation, then the popover closes
        (unless the row is flagged ``_no_close``).
      - ``add_separator``: a standard separator row (non-activatable).
      - ``add_custom``: an arbitrary non-activatable row (e.g. zoom controls
        whose inner buttons keep the popover open while triggering actions).

    All native Gtk.Popover methods (set_has_arrow, set_offset, set_parent,
    set_pointing_to, popup, popdown, set_position, set_can_focus, ...) are
    inherited unchanged.
    '''

    def __init__(self):
        Gtk.Popover.__init__(self)
        self.set_size_request(288, -1)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class('boxed-list')
        self.listbox.set_activate_on_single_click(True)
        self.set_child(self.listbox)

        self.listbox.connect('row-activated', self.on_row_activated)
        self.connect('map', self.on_map)

    def on_map(self, popover):
        # Give the listbox focus so arrow-key navigation works.
        self.listbox.grab_focus()

    def on_row_activated(self, listbox, row):
        callback = getattr(row, '_callback', None)
        if callback is not None:
            user_data = getattr(row, '_callback_data', ())
            callback(row, *user_data)
        else:
            # Action rows: trigger the GAction by emitting the row's 'activate'
            # signal, which runs the GtkActionHelper set up by set_action_name.
            # We emit directly because Adw.ActionRow overrides the activate
            # vfunc (so row.activate() / gtk_list_box_row_activate() does not
            # dispatch the action). The helper also tracks the action's enabled
            # state, so insensitive rows are not activatable in the first place.
            action_name = row.get_action_name() if hasattr(row, 'get_action_name') else None
            if action_name is not None:
                row.emit('activate')
        if not getattr(row, '_no_close', False):
            self.popdown()

    def add_item(self, title, action_name=None, target=None, icon_name=None, shortcut=None):
        '''Add an action menu item. Returns the Adw.ActionRow.

        Clicking the row (or pressing Enter) triggers ``action_name`` (with
        optional ``target`` GVariant) and closes the popover. The row tracks
        the action's enabled state automatically.
        '''
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_activatable(True)
        if icon_name not in (None, 'placeholder'):
            icon = Gtk.Image(icon_name=icon_name)
            icon.set_pixel_size(16)
            row.add_prefix(icon)
        if shortcut is not None:
            shortcut_label = Gtk.Label(label=shortcut)
            shortcut_label.add_css_class('dim-label')
            row.add_suffix(shortcut_label)
        if action_name is not None:
            row.set_action_name(action_name)
            if target is not None:
                row.set_action_target_value(target)
        self.listbox.append(row)
        return row

    def add_callback_item(self, title, callback=None, *user_data, icon_name=None, check=False):
        '''Add a callback menu item. Returns the Adw.ActionRow.

        On activation ``callback(row, *user_data)`` is invoked and the popover
        closes. The callback can be supplied now or later via ``set_callback``.

        ``icon_name`` adds a leading icon; ``check`` adds a trailing checkmark
        suffix (used for toggle items such as "Invert Colors").
        '''
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_activatable(True)
        if icon_name not in (None, 'placeholder'):
            icon = Gtk.Image(icon_name=icon_name)
            icon.set_pixel_size(16)
            row.add_prefix(icon)
        if check:
            check_img = Gtk.Image(icon_name='object-select-symbolic')
            check_img.set_pixel_size(16)
            check_img.add_css_class('dim-label')
            row.add_suffix(check_img)
        row._callback = callback
        row._callback_data = user_data
        self.listbox.append(row)
        return row

    def set_callback(self, row, callback, *user_data):
        '''Wire (or rewire) a callback to an existing callback row.'''
        row._callback = callback
        row._callback_data = user_data

    def clear(self):
        '''Remove all rows so the menu can be rebuilt with fresh context.'''
        child = self.listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.listbox.remove(child)
            child = next_child

    def add_separator(self):
        '''Add a standard separator row (non-activatable).'''
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        row = Gtk.ListBoxRow()
        row.set_child(separator)
        row.set_activatable(False)
        row.set_selectable(False)
        self.listbox.append(row)
        return row

    def add_custom(self, widget):
        '''Add an arbitrary non-activatable row (e.g. zoom controls).'''
        row = Gtk.ListBoxRow()
        row.set_child(widget)
        row.set_activatable(False)
        row.set_selectable(False)
        self.listbox.append(row)
        return row

    def set_width(self, width):
        self.set_size_request(width, -1)

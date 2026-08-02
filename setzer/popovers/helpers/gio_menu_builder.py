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

'''Gio.Menu model builder — the standard libadwaita way to build menus.

This class mirrors the API of StandardMenuViewBase (add_page / add_menu_button /
add_before_after_item / add_insert_symbol_item / add_action_button / add_widget /
add_separator / set_width) but produces a Gio.Menu model instead of a hand-built
Gtk.Popover + Gtk.ListBox.

The resulting Gio.Menu is consumed by Gtk.MenuButton.set_menu_model(), which
creates a native Gtk.PopoverMenu — exactly like the hamburger menu. This gives
us: native menu styling, keyboard navigation, submenu slide transitions,
accessible roles, and accelerator display — all for free.

Separator handling: Gio.Menu renders separators between sections. Each call to
add_separator() or add_widget(Gtk.Separator(...)) finalises the current section
and starts a new one, producing a visual divider.
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio


class GioMenuBuilder(object):

    def __init__(self):
        self.pages = {}
        self.model = Gio.Menu()
        self.pages['main'] = self.model
        self._sections = {}          # pagename -> current Gio.Menu section (or None)
        self._sections['main'] = None
        self._submenu_labels = {}    # pagename -> label (for titled sections)

    # ------------------------------------------------------------------
    # public API (mirrors StandardMenuViewBase)
    # ------------------------------------------------------------------

    def set_width(self, width):
        '''No-op: Gtk.PopoverMenu auto-sizes to content.'''
        pass

    def add_page(self, pagename, label=None):
        if pagename not in self.pages:
            page = Gio.Menu()
            self.pages[pagename] = page
            self._sections[pagename] = None
            if label is not None:
                self._submenu_labels[pagename] = label
        return self.pages[pagename]

    def add_menu_button(self, title, menu_name):
        '''Add a submenu entry on the main page linking to *menu_name*.'''
        submenu = self.pages.get(menu_name)
        if submenu is None:
            submenu = self.add_page(menu_name, title)
        self._get_or_create_section('main').append_submenu(title, submenu)

    def add_before_after_item(self, pagename, title, commands, icon=None, shortcut=None):
        item = Gio.MenuItem.new(title, 'win.insert-before-after')
        item.set_action_and_target_value('win.insert-before-after', GLib.Variant('as', commands))
        self._apply_icon_and_accel(item, icon, shortcut)
        self._get_or_create_section(pagename).append_item(item)

    def add_insert_symbol_item(self, pagename, title, command, icon=None, shortcut=None):
        item = Gio.MenuItem.new(title, 'win.insert-symbol')
        item.set_action_and_target_value('win.insert-symbol', GLib.Variant('as', command))
        self._apply_icon_and_accel(item, icon, shortcut)
        self._get_or_create_section(pagename).append_item(item)

    def add_action_button(self, pagename, title, action_name, parameter=None, icon=None, shortcut=None):
        item = Gio.MenuItem.new(title, action_name)
        if parameter is not None:
            item.set_action_and_target_value(action_name, parameter)
        self._apply_icon_and_accel(item, icon, shortcut)
        self._get_or_create_section(pagename).append_item(item)
        return item

    def add_widget(self, widget, pagename='main'):
        '''Gtk.Separator → section boundary; other widgets are not supported
        in Gio.Menu and should be replaced with add_*_item calls.'''
        if isinstance(widget, Gtk.Separator):
            self._flush_section(pagename)

    def add_separator(self, pagename='main'):
        self._flush_section(pagename)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _get_or_create_section(self, pagename):
        if self._sections.get(pagename) is None:
            self._sections[pagename] = Gio.Menu()
        return self._sections[pagename]

    def _flush_section(self, pagename):
        section = self._sections.get(pagename)
        if section is not None and section.get_n_items() > 0:
            self.pages[pagename].append_section(None, section)
        self._sections[pagename] = None

    def _apply_icon_and_accel(self, item, icon, shortcut):
        if icon is not None and icon != 'placeholder':
            item.set_icon(Gio.ThemedIcon.new(icon))
        if shortcut is not None:
            item.set_attribute_value('accel', GLib.Variant('s', self._convert_accel(shortcut)))

    @staticmethod
    def _convert_accel(shortcut):
        '''Convert a display shortcut string like "Ctrl+I" or "Shift+Ctrl+M"
        to GTK accelerator parse format like "<Ctrl>i" or "<Shift><Ctrl>m".
        Uses _() to match localised modifier names.'''
        parts = shortcut.split('+')
        mods = ''
        key = ''
        for part in parts:
            part = part.strip()
            if part == _('Ctrl'):
                mods += '<Ctrl>'
            elif part == _('Shift'):
                mods += '<Shift>'
            elif part == _('Alt'):
                mods += '<Alt>'
            else:
                key = part
        if len(key) == 1:
            key = key.lower()
        return mods + key

    def finalize(self):
        '''Flush any pending sections on all pages. Call after all items
        have been added.'''
        for pagename in list(self._sections.keys()):
            self._flush_section(pagename)

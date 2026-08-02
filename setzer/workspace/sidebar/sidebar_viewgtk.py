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
from gi.repository import Gtk


class Sidebar(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)
        self.append(self.stack)

        self._is_symbols = False
        self._doc_structure_page = None
        self._symbols_page = None

    def set_pages(self, doc_structure_page, symbols_page):
        self._doc_structure_page = doc_structure_page
        self._symbols_page = symbols_page

    def add_named(self, child, name):
        self.stack.add_named(child, name)

    def set_visible_child_name(self, name):
        self.stack.set_visible_child_name(name)
        if name == 'symbols':
            self._is_symbols = True
            self._update_icons()
        else:
            self._is_symbols = False
            self._update_icons()

    def get_visible_child(self):
        return self.stack.get_visible_child()

    def switch_page(self):
        if self._is_symbols:
            self.set_visible_child_name('document_structure')
        else:
            self.set_visible_child_name('symbols')

    def _update_icons(self):
        if self._is_symbols:
            if self._doc_structure_page:
                self._doc_structure_page.switch_button.get_child().set_from_icon_name('view-list-symbolic')
            if self._symbols_page:
                self._symbols_page.switch_button.get_child().set_from_icon_name('view-list-symbolic')
        else:
            if self._doc_structure_page:
                self._doc_structure_page.switch_button.get_child().set_from_icon_name('emoji-symbols-symbolic')
            if self._symbols_page:
                self._symbols_page.switch_button.get_child().set_from_icon_name('emoji-symbols-symbolic')



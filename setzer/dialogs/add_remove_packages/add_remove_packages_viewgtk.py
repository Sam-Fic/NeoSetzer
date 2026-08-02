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

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class AddRemovePackagesDialogView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        self.set_content_width(650)
        self.set_can_focus(False)
        self.headerbar.set_title_widget(Gtk.Label(label=_('Add / Remove Packages')))

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.content_box.set_margin_top(18)
        self.content_box.set_margin_bottom(18)
        self.content_box.set_spacing(24)
        self.topbox.append(self.content_box)

        self.create_add_section()
        self.create_remove_section()

    def create_add_section(self):
        self.add_group = Adw.PreferencesGroup()
        self.add_group.set_title(_('Available Packages'))
        self.add_group.set_description(_('Packages you can add to this document.'))
        self.content_box.append(self.add_group)

        self.add_list = Gtk.ListBox()
        self.add_list.add_css_class('boxed-list')
        self.add_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.add_list.set_sort_func(self.sort_function)

        self.add_scrolled_window = Gtk.ScrolledWindow()
        self.add_scrolled_window.set_size_request(-1, 200)
        self.add_scrolled_window.set_vexpand(False)
        self.add_list.set_margin_bottom(18)
        self.add_scrolled_window.set_child(self.add_list)
        self.add_group.add(self.add_scrolled_window)

        self.add_button = Gtk.Button(label=_('Add Package'))
        self.add_button.add_css_class('suggested-action')
        self.add_button.set_halign(Gtk.Align.END)
        self.add_group.add(self.add_button)

    def create_remove_section(self):
        self.remove_group = Adw.PreferencesGroup()
        self.remove_group.set_title(_('Installed Packages'))
        self.remove_group.set_description(_('Packages currently used by this document.'))
        self.content_box.append(self.remove_group)

        self.remove_list = Gtk.ListBox()
        self.remove_list.add_css_class('boxed-list')
        self.remove_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.remove_list.set_sort_func(self.sort_function)

        self.remove_scrolled_window = Gtk.ScrolledWindow()
        self.remove_scrolled_window.set_size_request(-1, 200)
        self.remove_scrolled_window.set_vexpand(False)
        self.remove_list.set_margin_bottom(18)
        self.remove_scrolled_window.set_child(self.remove_list)
        self.remove_group.add(self.remove_scrolled_window)

        self.remove_button = Gtk.Button(label=_('Remove Package'))
        self.remove_button.add_css_class('destructive-action')
        self.remove_button.set_halign(Gtk.Align.END)
        self.remove_group.add(self.remove_button)

    def sort_function(self, row1, row2, user_data=None):
        val1 = row1.get_title().lower()
        val2 = row2.get_title().lower()

        if val1 > val2:
            return 1
        elif val1 == val2:
            return 0
        elif val1 < val2:
            return -1

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
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf

import os

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class DocumentWizardView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        self.set_content_width(750)
        self.set_content_height(650)
        self.headerbar.set_title_widget(Gtk.Label(label=_('Create a template document')))
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)
        self.topbox.set_size_request(750, -1)

        self.center_box = Gtk.CenterBox()
        self.center_box.set_orientation(Gtk.Orientation.HORIZONTAL)
        # Fill the topbox vertically so the page_stack (and therefore the
        # ScrolledWindow inside each page) gets the dialog's full content
        # height to scroll within, instead of only the natural height of
        # the visible page.
        self.center_box.set_vexpand(True)
        self.pages = list()

        self.title_label = Gtk.Label(label=_('Create a template document'))
        self.title_label.add_css_class('title')
        self.subtitle_label = Gtk.Label(label='')
        self.subtitle_label.add_css_class('subtitle')

        self.title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.title_box.append(self.title_label)
        self.title_box.append(self.subtitle_label)

        self.title_widget = Gtk.CenterBox()
        self.title_widget.set_orientation(Gtk.Orientation.VERTICAL)
        self.title_widget.set_center_widget(self.title_box)

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))

        # 报告 #5：将当前设置另存为命名模板。
        self.save_template_button = Gtk.Button.new_with_mnemonic(_('Save as _Template'))

        self.back_button = Gtk.Button.new_with_mnemonic(_('_Back'))
        
        self.next_button = Gtk.Button.new_with_mnemonic(_('_Next'))
        self.next_button.add_css_class('suggested-action')

        self.create_button = Gtk.Button.new_with_mnemonic(_('_Create'))
        self.create_button.add_css_class('suggested-action')

        self.headerbar.set_title_widget(self.title_widget)
        self.headerbar.pack_start(self.cancel_button)
        self.headerbar.pack_start(self.save_template_button)
        self.headerbar.pack_start(self.back_button)
        self.headerbar.pack_end(self.create_button)
        self.headerbar.pack_end(self.next_button)

        self.page_stack = Gtk.Stack()
        # Let each page use its own natural height instead of being stretched
        # to the tallest page (which left a large blank area on short pages like
        # the document-class chooser).
        self.page_stack.set_vhomogeneous(False)
        # Fill the dialog's content area so a long page (e.g. the Packages
        # list on General settings) can scroll inside its ScrolledWindow
        # instead of overflowing past the dialog.
        self.page_stack.set_vexpand(True)
        self.center_box.set_center_widget(self.page_stack)
        self.topbox.append(self.center_box)



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
from gi.repository import Gtk


class DocumentView(Gtk.Box):

    def __init__(self, document):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_size_request(200, -1)
        self.add_css_class('document')

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.vbox.set_hexpand(True)

        self.source_view = document.source_view
        self.source_view.set_monospace(True)
        self.source_view.set_smart_home_end(True)
        self.source_view.set_auto_indent(True)
        self.source_view.set_bottom_margin(120)
        self.source_view.set_left_margin(12)
        self.source_view.set_right_margin(12)

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_child(self.source_view)
        self.scrolled_window.set_hexpand(True)

        # margin: 左侧空白容器，gutter（行号等）通过 set_size_request 调整宽度
        self.margin = Gtk.Box()
        self.margin.set_hexpand(False)

        self.hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.hbox.append(self.margin)
        self.hbox.append(self.scrolled_window)

        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.overlay.set_child(self.hbox)
        self.overlay.add_css_class('editor-card')
        self.overlay.set_overflow(Gtk.Overflow.HIDDEN)

        self.vbox.append(self.overlay)
        self.append(self.vbox)

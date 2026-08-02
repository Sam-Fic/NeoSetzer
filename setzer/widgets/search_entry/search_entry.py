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


class SearchEntry(Gtk.SearchEntry):
    '''Thin wrapper around the standard ``Gtk.SearchEntry``.

    A plain ``Gtk.SearchEntry`` already provides the search icon, a built-in
    clear button, the immediate ``changed`` signal (from ``GtkEditable``) and
    the canonical ``next-match`` / ``previous-match`` / ``stop-search`` key
    bindings (Down / Ctrl+G, Up / Shift+Ctrl+G, Escape). The former custom
    icon handling, key controller and underscore signals are therefore no
    longer needed. The class is kept as a subclass so existing import sites
    continue to work.
    '''

    def __init__(self):
        Gtk.SearchEntry.__init__(self)
        # GTK 4 的 Gtk.SearchEntry 默认 focusable=False，必须显式开启，
        # 否则光标/焦点无法进入搜索框，无法输入。
        self.set_focusable(True)

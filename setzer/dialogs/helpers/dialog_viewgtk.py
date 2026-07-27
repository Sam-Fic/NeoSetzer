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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk


class DialogView(Adw.Dialog):

    def __init__(self, main_window):
        Adw.Dialog.__init__(self)

        self.headerbar = Adw.HeaderBar()
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.headerbar)

        self.topbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.topbox)
        self.set_child(self.toolbar_view)

        # Esc 关闭弹窗：用 EventControllerKey 显式监听，比 ShortcutController 可靠。
        # 与 Gtk.SearchBar 的 Esc 不冲突（SearchBar 仅在搜索模式开启时消费 Esc）。
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

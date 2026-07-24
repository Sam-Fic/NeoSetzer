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


class PreviewPanelView(Gtk.Box):
    '''PDF 预览面板的视图层。

    Pass-11 重构：原 ActionBar（zoom_out / zoom_level / zoom_in / recolor /
    external_viewer / paging_label）已全部迁移到 headerbar，由
    HeaderBar.preview_buttons 持有，并通过 HeaderBar.panel_buttons_stack
    在预览展开时显示。本视图仅保留 PDF stack 与 empty_placeholder。
    "page xx of xx" 由 MainWindow.paging_label（preview_paned_overlay 的
    overlay）显示，居中在预览侧栏区域内。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_size_request(300, -1)
        self.add_css_class('preview')

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.empty_placeholder = Gtk.Box()
        self.stack.add_named(self.empty_placeholder, 'empty')

        self.append(self.stack)

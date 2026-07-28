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
from gi.repository import Gtk, Adw


class DialogView(Adw.Dialog):

    def __init__(self, main_window):
        Adw.Dialog.__init__(self)

        self.headerbar = Adw.HeaderBar()
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.headerbar)

        self.topbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toolbar_view.set_content(self.topbox)
        self.set_child(self.toolbar_view)

        # Adw.Dialog 原生处理 Escape 关闭（libadwaita 内建 Escape shortcut，
        # 在 CAPTURE 阶段作用整个 dialog 子树），无需手动监听。曾尝试在 CAPTURE
        # 阶段加 EventControllerKey 拦截 Escape，会干扰 Adw.Dialog 内部关闭状态，
        # 导致弹窗第二次 present 后内建 Escape shortcut 失效——故不拦截，依赖原生。



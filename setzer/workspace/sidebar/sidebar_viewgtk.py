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


class Sidebar(Gtk.Stack):

    def __init__(self):
        Gtk.Stack.__init__(self)
        # symbols↔document_structure 互斥切换时加 CROSSFADE 过渡（200ms 与
        # libadwaita 默认动画时长一致）。整体侧栏的滑入/滑出已由外层
        # Adw.OverlaySplitView 的 set_show_sidebar() 提供，这里只补页面间
        # 切换的过渡，避免硬切。与 preview_help_stack 行为对称。
        self.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.set_transition_duration(200)



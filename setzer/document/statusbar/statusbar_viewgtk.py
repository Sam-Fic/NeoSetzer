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

'''编辑器底部状态栏视图。

显示：行号/列号、语法语言、编码、缩进设置、选区行数。
位于编辑器圆角卡片下方，与窗口底边之间。

布局：左侧信息标签组 + 右侧选区行数（仅有选区时可见）。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


class StatusBarView(Gtk.Box):
    '''状态栏：水平 Gtk.Box，嵌入 editor-card 底部。

    各字段为独立 Gtk.Label，由 StatusBar presenter 在光标移动/
    设置变化/选区变化时更新内容与可见性。
    '''

    def __init__(self):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.HORIZONTAL)
        self.add_css_class('editor-statusbar')

        # 左侧信息组：行/列、语言、编码、缩进。
        # 用 spacing 而非分隔符点：点在窄窗口下浪费横向空间，spacing 更紧凑。
        # spacing=12 对应 --setzer-spacing-md（Gtk.Box.spacing 无法用 CSS 设置）。
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.info_box.set_halign(Gtk.Align.START)
        self.info_box.set_hexpand(False)

        self.line_col_label = Gtk.Label(label='')
        self.language_label = Gtk.Label(label='')
        self.encoding_label = Gtk.Label(label='')
        self.indent_label = Gtk.Label(label='')
        self.labels_count_label = Gtk.Label(label='')
        self.todos_count_label = Gtk.Label(label='')
        # 编辑器缩放指示器：缩放是全局（FontManager 类级）设置，所有文档共享，
        # 故每个文档状态栏都显示同一百分比，在缩放变化时由 actions 统一刷新。
        self.zoom_label = Gtk.Label(label='')

        for label in (self.line_col_label, self.language_label,
                      self.encoding_label, self.indent_label,
                      self.labels_count_label, self.todos_count_label,
                      self.zoom_label):
            label.add_css_class('caption')
            label.add_css_class('dim-label')
            self.info_box.append(label)

        # 弹性间隔把选区词数推到右侧
        self.spacer = Gtk.Box()
        self.spacer.set_hexpand(True)

        # 右侧选区词数：仅有选区时可见（无选区时 set_visible(False)）
        self.selection_label = Gtk.Label(label='')
        self.selection_label.add_css_class('caption')
        self.selection_label.add_css_class('dim-label')
        self.selection_label.set_halign(Gtk.Align.END)
        self.selection_label.set_visible(False)

        self.append(self.info_box)
        self.append(self.spacer)
        self.append(self.selection_label)

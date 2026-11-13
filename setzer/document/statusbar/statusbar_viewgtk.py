#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
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

显示：行号/列号、语法语言、编码、缩进设置、选区行数、构建结果指示。
位于编辑器圆角卡片下方，与窗口底边之间。

布局：左侧信息标签组 + 右侧构建结果与选区行数。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Pango


class StatusBarView(Gtk.Box):
    '''状态栏：水平 Gtk.Box，嵌入 editor-card 底部。

    各字段为独立 Gtk.Label，由 StatusBar presenter 在光标移动/
    设置变化/选区变化/构建完成时更新内容与可见性。
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
            # 省略号模式：标签的最小宽度固定为「…」的宽度，不再随文本长度变化。
            # 否则状态栏会参与编辑器列的最小宽度计算（Gtk.Box 的横向 min 是
            # 各子控件 min 之和），而 Adw.OverlaySplitView 用「总宽 − 内容最小宽」
            # 给侧栏封顶：选中文字时行/列文本变长、选区标签出现，编辑器列 min
            # 随之变大，预览侧栏就被压得窄于设置里的占比（表现为宽度跳变）。
            label.set_ellipsize(Pango.EllipsizeMode.END)
            self.info_box.append(label)

        # 弹性间隔把右侧字段推到窗口右端
        self.spacer = Gtk.Box()
        self.spacer.set_hexpand(True)

        # 构建结果指示：根文档最近一次构建的错误/警告数，纯文字无符号。
        # 无构建记录时隐藏；flat 按钮承载点击打开构建日志（连接在控制器）。
        self.build_status_button = Gtk.Button()
        self.build_status_button.add_css_class('flat')
        self.build_status_button.set_tooltip_text(_('Show build log'))
        self.build_status_button.set_visible(False)
        self.build_status_label = Gtk.Label(label='')
        self.build_status_label.add_css_class('caption')
        self.build_status_label.add_css_class('dim-label')
        # 同上：构建结果文本（错误/警告数）变化不得改变状态栏最小宽度。
        self.build_status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.build_status_button.set_child(self.build_status_label)

        # 右侧选区词数：仅有选区时可见（无选区时 set_visible(False)）
        self.selection_label = Gtk.Label(label='')
        self.selection_label.add_css_class('caption')
        self.selection_label.add_css_class('dim-label')
        self.selection_label.set_halign(Gtk.Align.END)
        # 省略号把 text="" → "7 lines selected" 的最小宽度变化压到「…」宽度，
        # 选区出现/消失不会再撑大编辑器列的最小宽度（见 info_box 的注释）。
        self.selection_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.selection_label.set_visible(False)

        self.append(self.info_box)
        self.append(self.spacer)
        self.append(self.build_status_button)
        self.append(self.selection_label)

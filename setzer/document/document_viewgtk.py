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
from gi.repository import Gtk, GLib
from setzer.settings.document_settings import DocumentSettings


class DocumentView(Gtk.Box):

    def __init__(self, document):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_size_request(200, -1)
        self.add_css_class('document')

        # 外层垂直容器：卡片 + 状态栏上下排列。
        self.outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.outer_vbox.set_hexpand(True)

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.vbox.set_hexpand(True)
        # editor-card：圆角卡片包裹编辑器内容。
        # overflow=HIDDEN 让 source_view 被裁剪到圆角内。
        self.vbox.add_css_class('editor-card')
        self.vbox.set_overflow(Gtk.Overflow.HIDDEN)

        self.source_view = document.source_view
        self.document = document
        self.source_view.set_monospace(True)
        self.source_view.set_smart_home_end(True)
        self.source_view.set_auto_indent(True)

        # LaTeX 感知自动缩进：监听插入文本，在用户按回车时根据上一行
        # 的 \begin / \end 调整缩进。
        self.source_buffer = self.source_view.get_buffer()
        self.source_buffer.connect('insert-text', self.on_insert_text)

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

        self.vbox.append(self.overlay)
        # 状态栏由 StatusBar presenter 创建，document.py 在构造 view 后注入。
        # 此处先占位，document.py 设 self.view.statusbar 后 append。
        self.statusbar = None
        self.outer_vbox.append(self.vbox)
        self.append(self.outer_vbox)

    def set_statusbar(self, statusbar_widget):
        '''由 document.py 在构造 StatusBar 后调用，将状态栏放在卡片下方。'''
        self.statusbar = statusbar_widget
        self.outer_vbox.append(statusbar_widget)

    def do_size_allocate(self, width, height, baseline):
        # GTK4 没有 GTK3 的 size-allocate 信号；改为覆写虚方法以在
        # 控件实际分配高度变化时更新 source_view 的底部留白。
        # shortcutsbar 与 document_stack 同处一个垂直 Gtk.Box
        # （workspace_viewgtk.py:65-67），shortcutsbar reflow 改变自身高度时
        # document_stack 分配高度随之变化，本方法会被调用——因此 shortcutsbar
        # 高度变化已覆盖。headerbar 高度变化经 document_stack_wrapper 的
        # margin_top 调整后同样传导到此。
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        # 用 source_view 实际分配高度而非 DocumentView 外层 height：外层
        # 包含状态栏，状态栏高度变化（如换行）会改变编辑区可视高度但外层
        # 不变。取 source_view 高度更准确。
        self._update_bottom_margin(self.source_view.get_allocated_height())

    def on_insert_text(self, buffer, location_iter, text, text_length):
        '''处理 LaTeX 感知的自动缩进。'''
        if '\n' not in text:
            return
        if not self.document.is_latex_document():
            return

        new_line = location_iter.get_line() + 1
        GLib.idle_add(self._adjust_latex_indent, new_line)

    def _adjust_latex_indent(self, line_number):
        buffer = self.source_buffer
        document = self.document

        prev_line = line_number - 1
        if prev_line < 0:
            return False

        prev_text = document.get_line(prev_line)
        stripped = prev_text.strip()

        current_line_text = document.get_line(line_number)
        ws_len = len(current_line_text) - len(current_line_text.lstrip())

        use_spaces = DocumentSettings.get_effective_value(document, document.settings, 'spaces_instead_of_tabs')
        tab_width = DocumentSettings.get_effective_value(document, document.settings, 'tab_width')

        if stripped.startswith('\\begin{'):
            ws_len = ws_len + tab_width if use_spaces else ws_len + 1
        elif stripped.startswith('\\end{'):
            ws_len = max(0, ws_len - tab_width) if use_spaces else max(0, ws_len - 1)
        else:
            return False

        if ws_len != len(current_line_text) - len(current_line_text.lstrip()):
            self._set_line_indent(line_number, ws_len)

        return False

    def _set_line_indent(self, line_number, ws_len):
        buffer = self.source_buffer
        found, line_start = buffer.get_iter_at_line(line_number)
        if not found:
            return

        iter_after_ws = line_start.copy()
        while not iter_after_ws.ends_line() and iter_after_ws.get_char() in (' ', '\t'):
            iter_after_ws.forward_chars(1)

        use_spaces = DocumentSettings.get_effective_value(self.document, self.document.settings, 'spaces_instead_of_tabs')
        if use_spaces:
            new_ws = ' ' * ws_len
        else:
            new_ws = '\t' * ws_len

        buffer.begin_user_action()
        buffer.delete(line_start, iter_after_ws)
        buffer.insert(line_start, new_ws)
        buffer.end_user_action()

    def _update_bottom_margin(self, height):
        # 底部留白 = 编辑区高度的 30%，夹在 [60, 200] px 之间。
        # 30%：短文档（如 300px 高）得到 90px 余量足以滚动，长文档不会
        # 占据过多空间；200px 上限避免超大窗口留白浪费；60px 下限保证
        # 极小窗口也有最小可滚动区域。与 gedit/VS Code 底部留白经验对齐。
        margin = max(60, min(int(height * 0.3), 200))
        current = self.source_view.get_bottom_margin()
        if current != margin:
            self.source_view.set_bottom_margin(margin)

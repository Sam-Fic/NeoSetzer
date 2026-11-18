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

'''单文件统一 diff 只读弹窗（侧栏 Changes 文件行点击打开）。

数据来自 GitRepository.diff() 的 `git diff --no-color -- <path>` 输出
（untracked 文件为 --no-index 对 /dev/null 的全 + 文本）。行首字符着色：
+ 绿 / - 红 / @@ hunk 头与 diff --git、index、---、+++ 元信息灰。
只读展示、无动作按钮——丢弃改动走文件行内联按钮，不在这里。'''

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango


class DiffDialog(Adw.Dialog):

    def __init__(self, path, diff_text):
        super().__init__()
        self.set_title(os.path.basename(path))
        self.set_content_width(620)
        self.set_content_height(520)

        # 文件相对路径作为副标题（headerbar 下方一行 dim 文本）。
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        path_label = Gtk.Label(label=path)
        path_label.add_css_class('dim-label')
        path_label.add_css_class('caption')
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.set_margin_top(6)
        path_label.set_margin_bottom(6)
        path_label.set_margin_start(12)
        path_label.set_margin_end(12)
        box.append(path_label)

        self.buffer = Gtk.TextBuffer()
        self._populate(diff_text)

        view = Gtk.TextView(buffer=self.buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.NONE)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.set_top_margin(6)
        view.set_bottom_margin(6)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(view)
        box.append(scrolled)

        self.set_child(box)

    def _populate(self, diff_text):
        '''整段写入并逐行着色。着色用固定前景色（GNOME 调色板 3 号绿/红，
        深浅主题下都可读）：TextTag 不支持 CSS 类，从主题动态取色得不偿失。'''
        text = diff_text if diff_text else _('(no diff)')
        self.buffer.set_text(text)

        tag_added = self.buffer.create_tag('added', foreground='#26a269')
        tag_removed = self.buffer.create_tag('removed', foreground='#c01c28')
        tag_meta = self.buffer.create_tag('meta', foreground='#9a9a9a')

        iter_line = self.buffer.get_start_iter()
        while True:
            line_start = iter_line.copy()
            line_end = line_start.copy()
            line_end.forward_to_line_end()
            first = line_start.get_char()
            if first == '+':
                self.buffer.apply_tag(tag_added, line_start, line_end)
            elif first == '-':
                self.buffer.apply_tag(tag_removed, line_start, line_end)
            elif first == '@':
                self.buffer.apply_tag(tag_meta, line_start, line_end)
            else:
                text_slice = self.buffer.get_text(line_start, line_end, False)
                if text_slice.startswith(('diff --git', 'index ', '--- ', '+++ ')):
                    self.buffer.apply_tag(tag_meta, line_start, line_end)
            if not iter_line.forward_line():
                break

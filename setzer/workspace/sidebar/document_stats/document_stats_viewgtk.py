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
from gi.repository import Gtk, Pango


class DocumentStatsView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        description = Gtk.Label(label=_('These counts are updated after the document is saved.'))
        description.set_wrap(True)
        description.set_xalign(0)
        description.add_css_class('dim-label')
        description.add_css_class('caption')
        self.append(description)

        # Word counts (texcount) — 整篇文档 + 当前文件。texcount 缺失时由
        # controller 隐藏这两行，并显示 label_texcount_missing 提示。
        self.label_whole_document = Gtk.Label()
        self.label_whole_document.set_wrap(True)
        self.label_whole_document.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_whole_document.set_xalign(0)
        self.label_whole_document.set_margin_top(12)
        self.append(self.label_whole_document)

        self.label_current_file = Gtk.Label()
        self.label_current_file.set_wrap(True)
        self.label_current_file.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_current_file.set_xalign(0)
        self.label_current_file.set_margin_top(12)
        self.append(self.label_current_file)

        # 字符数/行数（纯 Python，不依赖 texcount）— 对 CJK 用户尤其有用。
        # texcount 缺失时仍可显示，作为 word count 的 fallback。
        self.label_chars_lines = Gtk.Label()
        self.label_chars_lines.set_wrap(True)
        self.label_chars_lines.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_chars_lines.set_xalign(0)
        self.label_chars_lines.set_margin_top(12)
        self.label_chars_lines.set_visible(False)
        self.append(self.label_chars_lines)

        # 选区统计（纯 Python，实时）— 仅在有非空选区时显示。
        self.label_selection = Gtk.Label()
        self.label_selection.set_wrap(True)
        self.label_selection.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_selection.set_xalign(0)
        self.label_selection.set_margin_top(12)
        self.label_selection.set_visible(False)
        self.append(self.label_selection)

        # texcount 未安装提示 — 替代原「静默隐藏整个面板」的行为。
        # 使用 set_markup 以渲染 <a href> 链接和 <tt> 等内联标记。
        self.label_texcount_missing = Gtk.Label()
        self.label_texcount_missing.set_wrap(True)
        self.label_texcount_missing.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_texcount_missing.set_xalign(0)
        self.label_texcount_missing.set_margin_top(12)
        self.label_texcount_missing.add_css_class('dim-label')
        self.label_texcount_missing.add_css_class('caption')
        self.label_texcount_missing.set_visible(False)
        self.append(self.label_texcount_missing)

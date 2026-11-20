#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Pango


class DocumentStatsView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        # 页面外边距（§2.2 sm=6 / md=12）：本视图原先住在 Adw.PreferencesPage
        # （内建 margin 24px 12px），切换式重构后直接进裸 ScrolledWindow，
        # 需要自己补回。水平 10px 与全侧栏内容统一（工具栏/搜索框边缘线）；
        # 顶部 6 使首行与工具栏的间距（工具栏底距 7 + 6）同三个列表页的
        # 首行节奏一致；底部 12 保证滚到底时下沿不贴边。
        self.set_margin_top(6)
        self.set_margin_bottom(12)
        self.set_margin_start(10)  # --setzer-spacing-edge
        self.set_margin_end(10)

        description = Gtk.Label(label=_('These counts are updated after the document is saved.'))
        description.set_wrap(True)
        description.set_xalign(0)
        description.add_css_class('dimmed')
        description.add_css_class('caption')
        self.append(description)

        self.label_whole_document = Gtk.Label()
        self.label_whole_document.set_wrap(True)
        self.label_whole_document.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_whole_document.set_xalign(0)
        self.append(self.label_whole_document)

        self.label_current_file = Gtk.Label()
        self.label_current_file.set_wrap(True)
        self.label_current_file.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_current_file.set_xalign(0)
        self.append(self.label_current_file)

        self.stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.stats_box.set_visible(False)
        self.append(self.stats_box)

        # spacing=2（§2.2 光学例外）：数值大字号（title-1）与其 caption 说明
        # 行之间的紧贴间隙——caption 上行留白已含行距，2px 让两者读作一组；
        # 同规格用于下方 col_lines。
        self.col_chars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.col_chars.set_hexpand(True)
        self.stats_box.append(self.col_chars)

        self.label_chars_value = Gtk.Label()
        self.label_chars_value.set_xalign(0)
        self.label_chars_value.add_css_class('title-1')
        self.col_chars.append(self.label_chars_value)

        self.label_chars_desc = Gtk.Label()
        self.label_chars_desc.set_xalign(0)
        self.label_chars_desc.add_css_class('dimmed')
        self.label_chars_desc.add_css_class('caption')
        self.col_chars.append(self.label_chars_desc)

        # spacing=2 同 col_chars（value + caption 一组）。
        self.col_lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.col_lines.set_hexpand(True)
        self.stats_box.append(self.col_lines)

        self.label_lines_value = Gtk.Label()
        self.label_lines_value.set_xalign(0)
        self.label_lines_value.add_css_class('title-1')
        self.col_lines.append(self.label_lines_value)

        self.label_lines_desc = Gtk.Label()
        self.label_lines_desc.set_xalign(0)
        self.label_lines_desc.add_css_class('dimmed')
        self.label_lines_desc.add_css_class('caption')
        self.col_lines.append(self.label_lines_desc)

        self.label_chars_lines = Gtk.Label()
        self.label_chars_lines.set_wrap(True)
        self.label_chars_lines.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_chars_lines.set_xalign(0)
        self.label_chars_lines.set_visible(False)
        self.append(self.label_chars_lines)

        self.label_selection = Gtk.Label()
        self.label_selection.set_wrap(True)
        self.label_selection.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_selection.set_xalign(0)
        self.label_selection.set_margin_top(12)
        self.label_selection.set_visible(False)
        self.append(self.label_selection)

        self.label_texcount_missing = Gtk.Label()
        self.label_texcount_missing.set_wrap(True)
        self.label_texcount_missing.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_texcount_missing.set_xalign(0)
        self.label_texcount_missing.set_margin_top(12)
        self.label_texcount_missing.add_css_class('dimmed')
        self.label_texcount_missing.add_css_class('caption')
        self.label_texcount_missing.set_visible(False)
        self.append(self.label_texcount_missing)

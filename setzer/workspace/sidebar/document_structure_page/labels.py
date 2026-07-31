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
from gi.repository import Gtk, Gdk, GLib

import sys

import setzer.workspace.sidebar.document_structure_page.labels_viewgtk as labels_section_view


class LabelsSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)

        self.view = labels_section_view.LabelsSectionView(self)

        self.labels = list()

    def on_row_activated(self, row):
        label = row.item_data
        if label is None:
            return

        document = label[2]
        line_number = document.source_buffer.get_iter_at_offset(label[1]).get_line()
        self.data_provider.workspace.set_active_document(document)
        document.place_cursor(line_number)
        document.scroll_cursor_onscreen()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    #@timer
    def update_items(self, *params):
        labels = list()
        document = self.data_provider.document
        for label in document.parser.symbols['labels_with_offset']:
            # 不修改 parser 的 label 列表（原 label.append(document) 会反复追加，
            # 每次 update_items 都让列表增长一份 document 引用，属内存/性能泄漏）。
            # 构造新列表 [name, offset, document]，保持 on_row_activated 的索引不变。
            labels.append([label[0], label[1], document])
        for include_document in self.data_provider.integrated_includes:
            for label in include_document.parser.symbols['labels_with_offset']:
                labels.append([label[0], label[1], include_document])
        # GLib.utf8_collate_key(str, len) 在部分 MSYS2/Windows 的 PyGObject
        # 构建中会段错误（g_convert 断言失败 + SIGSEGV），无法 try/except 捕获。
        # Windows 上退化为纯 Python 大小写折叠排序（locale-naive 但安全）；
        # 其它平台保留 GLib 的区域感知排序行为。
        if sys.platform == 'win32':
            labels.sort(key=lambda label: label[0].casefold())
        else:
            labels.sort(key=lambda label: GLib.utf8_collate_key(label[0].casefold(), len(label[0].casefold())))
        self.labels = labels

        self.view.populate()

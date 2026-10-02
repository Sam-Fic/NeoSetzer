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
from gi.repository import GLib

import sys

import setzer.workspace.sidebar.document_structure_page.labels_viewgtk as labels_section_view
from setzer.document.label_check import analyze_labels


class LabelsSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)

        self.view = labels_section_view.LabelsSectionView(self)

        self.labels = list()
        self.report = None

    def on_row_activated(self, row):
        label = row.item_data
        if label is None:
            return

        document = label[2]
        line_number = document.source_buffer.get_iter_at_offset(label[1]).get_line()
        self.data_provider.workspace.set_active_document(document)
        document.place_cursor(line_number)
        document.scroll_cursor_with_context()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    #@timer
    def update_items(self, *params):
        labels = list()
        document = self.data_provider.document
        # 与 include 文档合并分析：\ref 常出现在根文档而 \label 在子文件，
        # 单看任一份都会误报「未使用」。仅覆盖已打开的文档——与下方列出的
        # label 集合一致（未打开的 include 没有解析结果，两侧同样受限）。
        documents = [document]
        documents.extend(self.data_provider.integrated_includes.keys())
        definitions = list()
        references = list()
        for include_document in documents:
            for label in include_document.parser.symbols['labels_with_offset']:
                # 不修改 parser 的 label 列表（原 label.append(document) 会反复追加，
                # 每次 update_items 都让列表增长一份 document 引用，属内存/性能泄漏）。
                # 构造新列表 [name, offset, document]，保持 on_row_activated 的索引不变。
                labels.append([label[0], label[1], include_document])
                definitions.append(label[0])
            for name, _offset in include_document.parser.symbols.get('refs_with_offset', ()):
                references.append(name)
        report = analyze_labels(definitions, references)
        # 第 4 位为问题标记：duplicate 优先于 unused（与「重复定义更严重」的
        # 直觉一致），供视图加徽章；无问题时为 None。
        for label in labels:
            if label[0] in report.duplicate_names:
                label.append('duplicate')
            elif label[0] in report.unused_names:
                label.append('unused')
            else:
                label.append(None)
        # GLib.utf8_collate_key(str, len) 在部分 MSYS2/Windows 的 PyGObject
        # 构建中会段错误（g_convert 断言失败 + SIGSEGV），无法 try/except 捕获。
        # Windows 上退化为纯 Python 大小写折叠排序（locale-naive 但安全）；
        # 其它平台保留 GLib 的区域感知排序行为。
        if sys.platform == 'win32':
            labels.sort(key=lambda label: label[0].casefold())
        else:
            labels.sort(key=lambda label: GLib.utf8_collate_key(label[0].casefold(), len(label[0].casefold())))
        self.report = report
        self.labels = labels

        self.view.populate()

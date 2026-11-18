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


import setzer.workspace.sidebar.document_structure_page.files_viewgtk as files_section_view


class FilesSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)
        # 活跃文档切换时 accent 行要跟着挪（与 Document Structure 的
        # 光标跟随语义一致）。数据重建由 data_updated 负责，这里只挪高亮。
        data_provider.workspace.connect('new_active_document', self.on_new_active_document)

        self.view = files_section_view.FilesSectionView(self)

        self.includes = list()

    def on_new_active_document(self, workspace, document=None):
        if not self.view.is_visible():
            return
        self.view.sync_accent_to_active_document()
        self.view._sync_selection_to_accent_row()

    def on_row_activated(self, row):
        item_data = row.item_data
        if item_data is None:
            return

        kind, entry = item_data
        workspace = self.data_provider.workspace
        if kind == 'main':
            # 主文档行可能尚未打开（处于子文件时列表来自 magic root 主文档）：
            # 先按路径打开，失败（文件不存在）则不动。
            document = entry['document']
            if document is None and entry['filename'] is not None:
                document = workspace.open_document_by_filename(entry['filename'])
            if document is None:
                return
            workspace.set_active_document(document)
        else:
            # open_document_by_filename 内部已调用 set_active_document，
            # 此处不再重复调用——重复调用会再次触发 new_inactive_document /
            # new_active_document 信号，导致 headerbar/shortcutsbar 等做
            # 无谓的重复刷新。
            document = workspace.open_document_by_filename(entry['filename'])
        # document 可能为 None（文件不存在/无法打开），此时不继续操作。
        if document is None:
            return
        document.scroll_cursor_onscreen()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    def update_items(self, *params):
        self.includes = self.data_provider.get_includes()
        self.view.populate()

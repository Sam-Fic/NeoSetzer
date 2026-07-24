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
from gi.repository import Gtk, Gdk

import os.path

import setzer.workspace.sidebar.document_structure_page.files_viewgtk as files_section_view


class FilesSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)

        self.view = files_section_view.FilesSectionView(self)

        self.includes = list()

    def on_row_activated(self, row):
        item_data = row.item_data
        if item_data is None:
            return

        kind, include = item_data
        if kind == 'main':
            document = self.data_provider.document
            self.data_provider.workspace.set_active_document(document)
        else:
            filename = include['filename']
            # open_document_by_filename 内部已调用 set_active_document，
            # 此处不再重复调用——重复调用会再次触发 new_inactive_document /
            # new_active_document 信号，导致 headerbar/shortcutsbar 等做
            # 无谓的重复刷新。
            document = self.data_provider.workspace.open_document_by_filename(filename)
        # document 可能为 None（文件不存在/无法打开），此时不继续操作。
        if document is None:
            return
        document.scroll_cursor_onscreen()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    def update_items(self, *params):
        self.includes = self.data_provider.get_includes()
        self.view.populate()

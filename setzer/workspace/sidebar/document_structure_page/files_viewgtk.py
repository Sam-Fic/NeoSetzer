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

import os.path

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget


class FilesSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'folder-symbolic',
            _('No files'),
            _('The main document is always shown. Add \\input{...} or \\include{...} to include other files.')
        )

    def populate(self):
        # 签名 = id(document) + 主文件名 + 各 include 文件名元组。
        # 按键不动 \input/\include 时签名命中，跳过重建。
        doc = self.model.data_provider.document
        signature = (
            id(doc),
            doc.get_displayname(),
            tuple(include['filename'] for include in self.model.includes),
        )
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()

        doc_dir = doc.get_dirname() or ''

        row = self.make_file_row(
            doc.get_displayname(),
            doc_dir,
            'document-open-symbolic',
            0
        )
        row.item_data = ('main', None)
        self.append_row(row)

        for include in self.model.includes:
            row = self.make_file_row(
                include['filename'],
                doc_dir,
                'text-x-generic-symbolic',
                18
            )
            row.item_data = ('include', include)
            self.append_row(row)

        self.set_empty_state_visible(False)
        self._sync_selection_to_accent_row()

    def make_file_row(self, filename, doc_dir, icon_name, indent):
        basename = os.path.basename(filename)
        row = self.make_row(icon_name, basename, indent)
        row.add_css_class('sidebar-file-row')
        row.set_tooltip_text(filename)

        dirname = os.path.dirname(filename)
        if dirname and dirname != doc_dir:
            # 显示相对于文档目录的目录，若无法相对则显示原始目录
            try:
                rel_dir = os.path.relpath(dirname, doc_dir) if doc_dir else dirname
            except ValueError:
                rel_dir = dirname
            if rel_dir and rel_dir != '.':
                row.set_subtitle(rel_dir)

        return row

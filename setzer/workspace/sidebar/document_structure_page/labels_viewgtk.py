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

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget


class LabelsSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'tag-symbolic',
            _('No labels'),
            _('Add \\label{...} to create references to figures, sections, and more.')
        )

    def populate(self):
        # 签名 = id(document) + 全部 label 名称元组。按键不动 \label 时签名命中。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple(label[0] for label in self.model.labels))
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()
        for label in self.model.labels:
            row = self.make_row('tag-symbolic', label[0], 0)
            row.item_data = label
            self.append_row(row)
        self.set_empty_state_visible(len(self.model.labels) == 0)
        self._sync_selection_to_accent_row()

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


class TodosSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'starred-symbolic',
            _('No to-dos'),
            _('Add \\todo{...} to keep track of tasks.')
        )

    def populate(self):
        # 签名 = id(document) + 全部 todo 文本元组。按键不动 \todo 时签名命中。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple(todo[0] for todo in self.model.todos))
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()
        for todo in self.model.todos:
            row = self.make_row('starred-symbolic', todo[0], 0)
            row.item_data = todo
            self.append_row(row)
        self.set_empty_state_visible(len(self.model.todos) == 0)
        self._sync_selection_to_accent_row()

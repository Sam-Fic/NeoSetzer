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

import setzer.workspace.sidebar.document_structure_page.structure_viewgtk as structure_section_view


class StructureSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)

        self.levels = {'part': 0, 'chapter': 1, 'section': 2, 'subsection': 3, 'subsubsection': 4, 'paragraph': 5, 'subparagraph': 6, 'file': 7}

        self.icon_map = {
            'part': 'view-list-symbolic',
            'chapter': 'x-office-document-symbolic',
            'section': 'folder-symbolic',
            'subsection': 'text-x-generic-symbolic',
            'subsubsection': 'accessories-text-editor-symbolic',
            'paragraph': 'format-justify-left-symbolic',
            'subparagraph': 'format-justify-fill-symbolic',
            'file': 'file-symbolic',
        }

        self.view = structure_section_view.StructureSectionView(self)

        self.nodes = list()
        self.nodes_in_line = list()

    def on_row_activated(self, row):
        node = row.item_data
        if node is None:
            return

        item = node['item']
        document = item[0]
        line_number = item[1]
        if document is None:
            filename = item[3]
            document = self.data_provider.workspace.open_document_by_filename(filename)
        # open_document_by_filename 可能返回 None（文件不存在/无法打开），
        # 此时不能继续 set_active_document/place_cursor，否则 AttributeError。
        if document is None:
            return
        self.data_provider.workspace.set_active_document(document)
        document.place_cursor(line_number)
        document.scroll_cursor_onscreen()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    #@timer
    def update_items(self, *params):
        sections = dict()

        # 用游标 include_idx 推进而非 del(includes[0])：后者每次删除需把
        # 剩余元素整体前移，N 个 include 的合并复杂度退化为 O(N²)。
        includes = self.data_provider.get_includes()
        include_idx = 0
        include_count = len(includes)
        blocks = list()
        for block in self.data_provider.document.parser.symbols['blocks']:
            while include_idx < include_count and includes[include_idx]['offset'] < block[0]:
                inc = includes[include_idx]
                if inc['document'] != None:
                    for block_included in inc['document'].parser.symbols['blocks']:
                        if len(block_included) < 7:
                            block_included.append(inc['document'])
                        blocks.append(block_included)
                else:
                    # file_block 用 include 的 offset 作为 block[0]，使每个
                    # include 的 file_block 有唯一 offset。原代码 block[0]=0
                    # 导致下方 sections dict 以 block[2](=0) 为 key 时多个
                    # include 互相覆盖，结构视图只显示最后一个 include。
                    file_block = [inc['offset'], 0, 0, 0, 'file', inc['filename'], inc['document']]
                    blocks.append(file_block)
                include_idx += 1
            if len(block) < 7:
                block.append(self.data_provider.document)
            blocks.append(block)

        while include_idx < include_count:
            inc = includes[include_idx]
            if inc['document'] != None:
                for block in inc['document'].parser.symbols['blocks']:
                    if len(block) < 7:
                        block.append(inc['document'])
                    blocks.append(block)
            else:
                file_block = [inc['offset'], 0, 0, 0, 'file', inc['filename'], inc['document']]
                blocks.append(file_block)
            include_idx += 1

        last_line = -1
        for block in blocks:
            if block[1] != None and block[4] in self.levels and block[2] != last_line:
                # 用 block[0]（offset）作为 key 而非 block[2]（line_number）。
                # section block 的 offset 唯一；file_block 用 include 的 offset
                # 也唯一。原代码用 block[2]，多个 file_block 的 line_number
                # 均为 0，dict key 碰撞导致互相覆盖。
                sections[block[0]] = {'document': block[6], 'offset_start': block[0], 'starting_line': block[2], 'block': block}
                last_line = block[2]

        current_level = 0
        nodes = list()
        nodes_in_line = list()
        predecessor = {0: None, 1: None, 2: None, 3: None, 4: None, 5: None, 6: None, 7: None}
        for section in sections.values():
            section_type = section['block'][4]
            level = self.levels[section_type]
            node = {'item': [section['document'], section['starting_line'], self.icon_map.get(section_type, 'symbolic'), ' '.join(section['block'][5].splitlines())], 'children': list()}
            if predecessor[level] == None:
                nodes.append(node)
            else:
                predecessor[level]['children'].append(node)
            nodes_in_line.append(node)

            for i in range(level + 1, 8):
                predecessor[i] = node

        self.nodes_in_line = nodes_in_line
        self.nodes = nodes
        self.view.populate()

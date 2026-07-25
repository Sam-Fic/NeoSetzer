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
        self.data_provider.connect('cursor_position_changed', self.on_cursor_position_changed)

        self.levels = {'part': 0, 'chapter': 1, 'section': 2, 'subsection': 3, 'subsubsection': 4, 'paragraph': 5, 'subparagraph': 6, 'file': 7}

        self.icon_map = {
            'part': 'part-symbolic',
            'chapter': 'chapter-symbolic',
            'section': 'section-symbolic',
            'subsection': 'subsection-symbolic',
            'subsubsection': 'subsubsection-symbolic',
            'paragraph': 'paragraph-symbolic',
            'subparagraph': 'subparagraph-symbolic',
            'file': 'file-symbolic',
            'figure': 'image-x-generic-symbolic',
            'table': 'view-grid-symbolic',
            'equation': 'insert-math-symbolic',
        }

        self.view = structure_section_view.StructureSectionView(self)

        self.nodes = list()
        self.nodes_in_line = list()
        self._row_map = dict()
        self._current_highlight_row = None
        # 高亮短路缓存：记录上次高亮的节节点及其行区间。cursor_position_changed
        # 在每次光标移动时触发，绝大多数按键光标仍在同一节内，target_node 不变。
        # 原实现每次都线性扫描全部 nodes_in_line 并 remove/add css class。改为：
        # 若新行号仍落在 [last_line, next_line) 区间内，直接跳过。
        self._last_highlight_doc = None
        self._last_highlight_node = None
        self._last_highlight_line = None
        self._next_highlight_line = None

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

    def register_row(self, row, node):
        self._row_map[id(node)] = row

    def on_cursor_position_changed(self, data_provider, document):
        if document is None:
            return
        line_number = document.source_buffer.get_iter_at_offset(
            document.source_buffer.get_property('cursor-position')).get_line()
        self.highlight_current_section(line_number, document)

    def highlight_current_section(self, line_number, document):
        # 同节内移动短路：光标仍在上次高亮节的行区间内时 target_node 不变，
        # 跳过线性扫描与 css class 操作。
        if (self._last_highlight_doc is document and
                self._last_highlight_node is not None and
                self._last_highlight_line <= line_number < self._next_highlight_line):
            return

        target_node = None
        next_line = float('inf')
        for node in self.nodes_in_line:
            node_doc = node['item'][0]
            node_line = node['item'][1]
            if node_doc is document and node_line <= line_number:
                target_node = node
            elif node_doc is not None and node_doc is not document:
                continue
            elif node_doc is document and node_line > line_number:
                next_line = node_line
                break

        if self._current_highlight_row is not None:
            self._current_highlight_row.remove_css_class('accent')
            self._current_highlight_row = None

        if target_node is not None:
            row = self._row_map.get(id(target_node))
            if row is not None:
                row.add_css_class('accent')
                self._current_highlight_row = row
            self._last_highlight_doc = document
            self._last_highlight_node = target_node
            self._last_highlight_line = target_node['item'][1]
            self._next_highlight_line = next_line
        else:
            self._last_highlight_doc = document
            self._last_highlight_node = None
            self._last_highlight_line = None
            self._next_highlight_line = None

    def _append_include_blocks(self, inc, blocks):
        '''将一个 include 的 blocks 追加到 blocks 列表。

        include 有两种情况：
        - ``inc['document']`` 非 None：include 的文档已打开，从其 parser
          取 blocks 并追加。每个 block 追加 document 引用到 block[6]
          （长度 < 7 时 append，幂等防重复追加）。
        - ``inc['document']`` 为 None：include 文件未打开（找不到/未加载），
          创建 file_block 占位。用 inc['offset'] 作为 block[0] 保证唯一性
          （原代码 block[0]=0 导致多个 file_block 在 sections dict 中互相覆盖）。
        '''
        if inc['document'] is not None:
            for block_included in inc['document'].parser.symbols['blocks']:
                if len(block_included) < 7:
                    block_included.append(inc['document'])
                blocks.append(block_included)
        else:
            file_block = [inc['offset'], 0, 0, 0, 'file', inc['filename'], inc['document']]
            blocks.append(file_block)

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
            # 处理 offset 在当前 block 之前的 include（按文档顺序交错合并）。
            while include_idx < include_count and includes[include_idx]['offset'] < block[0]:
                self._append_include_blocks(includes[include_idx], blocks)
                include_idx += 1
            if len(block) < 7:
                block.append(self.data_provider.document)
            blocks.append(block)

        # 处理所有 block 之后的尾部 include（报告曾误判为死代码，实则必要：
        # 没有 block 的 offset 大于这些 include 时，上面的内层 while 不会处理它们）。
        while include_idx < include_count:
            self._append_include_blocks(includes[include_idx], blocks)
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
        # 动态生成 predecessor 字典：levels 有 N 个层级就初始化 N 个 None，
        # 避免硬编码 8 个后新增层级时遗漏更新。
        predecessor = {i: None for i in range(len(self.levels))}
        for section in sections.values():
            section_type = section['block'][4]
            level = self.levels[section_type]
            node = {'item': [section['document'], section['starting_line'], self.icon_map.get(section_type, 'text-x-generic-symbolic'), ' '.join(section['block'][5].splitlines())], 'children': list()}
            if predecessor[level] == None:
                nodes.append(node)
            else:
                predecessor[level]['children'].append(node)
            nodes_in_line.append(node)

            for i in range(level + 1, len(self.levels)):
                predecessor[i] = node

        self.nodes_in_line = nodes_in_line
        self.nodes = nodes
        self._row_map.clear()
        self._current_highlight_row = None
        # 节点已重建，旧的 _last_highlight_node 引用失效，重置缓存强制下次全量扫描。
        self._last_highlight_doc = None
        self._last_highlight_node = None
        self._last_highlight_line = None
        self._next_highlight_line = None
        self.view.populate()
        if self.data_provider.document is not None:
            self.on_cursor_position_changed(self.data_provider, self.data_provider.document)

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
        self.data_provider.connect('document_changed', self.on_document_changed)
        # 跟踪当前文档，用于在切换时保存旧文档的折叠状态
        self._current_document = None

        self.levels = {'part': 0, 'chapter': 1, 'section': 2, 'subsection': 3, 'subsubsection': 4, 'paragraph': 5, 'subparagraph': 6, 'file': 7}

        # 文档结构图标统一改用系统自带（Adwaita）symbolic 图标，
        # 避免应用自带的 section/file 等图标样式不统一且观感较差。
        self.icon_map = {
            'part': 'view-paged-symbolic',
            'chapter': 'bookmark-new-symbolic',
            'section': 'view-list-symbolic',
            'subsection': 'view-list-bullet-symbolic',
            'subsubsection': 'view-list-ordered-symbolic',
            'paragraph': 'format-justify-left-symbolic',
            'subparagraph': 'insert-text-symbolic',
            'file': 'text-x-generic-symbolic',
            'figure': 'image-x-generic-symbolic',
            'table': 'view-grid-symbolic',
            'equation': 'accessories-calculator-symbolic',
        }

        self.view = structure_section_view.StructureSectionView(self)

        self.nodes = list()
        self.nodes_in_line = list()
        # 子树折叠状态：以节节点稳定偏移（block[0]）为 key，记录已折叠（收起
        # 子节点）的节点集合。跨 update_items 重建时节点对象被重建，但偏移稳定，
        # 故折叠状态可保留；节点消失后其偏移残留在集合内无害（体积小、仅作查表）。
        self.collapsed = set()
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

    def on_document_changed(self, data_provider, new_document):
        """文档切换时，保存旧文档的折叠状态并加载新文档的。"""
        # 保存旧文档的折叠状态
        if self._current_document is not None:
            self._current_document.collapsed_sections = set(self.collapsed)
        # 加载新文档的折叠状态
        if new_document is not None:
            self.collapsed = set(new_document.collapsed_sections)
        else:
            self.collapsed = set()
        self._current_document = new_document

    def toggle_node(self, offset):
        '''折叠/展开某个有子节点的节。offset 为节点稳定偏移（block[0]）。

        仅翻转折叠集合并重建可见行；不重建模型树，故节点对象、折叠状态、
        行映射的 id 引用均保持稳定。重建后重置高亮缓存并重新应用当前节高亮，
        使新行获得 accent 类。
        '''
        if offset in self.collapsed:
            self.collapsed.discard(offset)
        else:
            self.collapsed.add(offset)

        # 同步到文档对象的 collapsed_sections，以便持久化
        if self.data_provider.document is not None:
            self.data_provider.document.collapsed_sections = set(self.collapsed)

        # 行将被重建，旧 row 失效：清空高亮缓存，避免向已销毁行 remove/add css。
        self._current_highlight_row = None
        self._last_highlight_doc = None
        self._last_highlight_node = None
        self._last_highlight_line = None
        self._next_highlight_line = None

        self.view.populate()

        if self.data_provider.document is not None:
            self.on_cursor_position_changed(self.data_provider, self.data_provider.document)

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
        # 确保 _current_document 与 data_provider.document 同步
        # 这处理了在 data_updated 信号之前发生的文档切换
        if self._current_document is not self.data_provider.document:
            self.on_document_changed(self.data_provider, self.data_provider.document)

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
            node = {'item': [section['document'], section['starting_line'], self.icon_map.get(section_type, 'text-x-generic-symbolic'), ' '.join(section['block'][5].splitlines())], 'children': list(), 'offset': section['offset_start'], 'block': section['block']}
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

    def _get_document_for_node(self, node):
        document = node['item'][0]
        if document is None:
            filename = node['item'][3]
            document = self.data_provider.workspace.open_document_by_filename(filename)
        return document

    def _copy_to_clipboard(self, text):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def find_label_for_node(self, node):
        block = node.get('block', None)
        if block is None:
            return None
        document = self._get_document_for_node(node)
        if document is None:
            return None
        # Labels are stored as [label_name, offset]
        labels = document.parser.symbols.get('labels_with_offset', [])
        start_offset = block[0]
        end_offset = block[1] if block[1] is not None else document.source_buffer.get_end_iter().get_offset()
        for label_name, label_offset in labels:
            if start_offset <= label_offset < end_offset:
                return label_name
        return None

    def copy_title(self, node):
        block = node.get('block', None)
        if block is None:
            return
        title = block[5] if len(block) > 5 else ''
        self._copy_to_clipboard(title)

    def copy_ref(self, node):
        label_name = self.find_label_for_node(node)
        if label_name is None:
            return
        self._copy_to_clipboard('\\ref{' + label_name + '}')

    def copy_label(self, node):
        label_name = self.find_label_for_node(node)
        if label_name is None:
            return
        self._copy_to_clipboard('\\label{' + label_name + '}')

    def rename_section(self, node):
        block = node.get('block', None)
        if block is None:
            return
        document = self._get_document_for_node(node)
        if document is None:
            return
        old_title = block[5]
        label_name = self.find_label_for_node(node)

        dialog = Gtk.Dialog(title=_('Rename Section'), parent=self.view.get_root())
        dialog.set_modal(True)
        dialog.add_button(_('Cancel'), Gtk.ResponseType.CANCEL)
        dialog.add_button(_('Rename'), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        content_area = dialog.get_content_area()

        title_label = Gtk.Label(label=_('Title:'))
        title_label.set_halign(Gtk.Align.START)
        content_area.append(title_label)

        title_entry = Gtk.Entry()
        title_entry.set_text(old_title)
        title_entry.set_activates_default(True)
        content_area.append(title_entry)

        label_entry = None
        if label_name is not None:
            label_label = Gtk.Label(label=_('Label:'))
            label_label.set_halign(Gtk.Align.START)
            content_area.append(label_label)

            label_entry = Gtk.Entry()
            label_entry.set_text(label_name)
            content_area.append(label_entry)

        dialog.title_entry = title_entry
        dialog.label_entry = label_entry
        dialog.block = block
        dialog.document = document
        dialog.old_title = old_title
        dialog.old_label = label_name

        dialog.connect('response', self._on_rename_response)
        dialog.present()

    def _on_rename_response(self, dialog, response):
        title_entry = dialog.title_entry
        label_entry = dialog.label_entry
        block = dialog.block
        document = dialog.document
        old_title = dialog.old_title
        old_label = dialog.old_label

        new_title = title_entry.get_text().strip()
        new_label = label_entry.get_text().strip() if label_entry is not None else None

        dialog.destroy()

        if response != Gtk.ResponseType.OK:
            return

        # Rename label FIRST (offsets are still valid before title edit)
        if new_label is not None and old_label is not None and new_label != old_label:
            self._rename_label_in_document(document, old_label, new_label)
            self._update_label_references(old_label, new_label)

        # Then update section title
        if new_title != old_title:
            self._update_section_title(document, block, new_title)

    def _update_section_title(self, document, block, new_title):
        start_offset = block[0]
        start_iter = document.source_buffer.get_iter_at_offset(start_offset)
        brace_iter = start_iter.copy()
        result = brace_iter.forward_search('{', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
        if result is not None:
            open_brace = result[0]
            close_brace = open_brace.copy()
            depth = 1
            close_brace.forward_char()
            while depth > 0 and not close_brace.is_end():
                ch = close_brace.get_char()
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                close_brace.forward_char()
            if depth == 0:
                close_brace.backward_char()
                document.source_buffer.begin_user_action()
                document.source_buffer.delete(open_brace, close_brace)
                document.source_buffer.insert(open_brace, new_title)
                document.source_buffer.end_user_action()

    def _rename_label_in_document(self, document, old_label, new_label):
        labels_with_offset = document.parser.symbols.get('labels_with_offset', [])
        for label_name, label_offset in labels_with_offset:
            if label_name == old_label:
                label_start = document.source_buffer.get_iter_at_offset(label_offset)
                open_brace = label_start.copy()
                result = open_brace.forward_search('{', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
                if result is None:
                    continue
                open_brace = result[0]
                close_brace = open_brace.copy()
                depth = 1
                close_brace.forward_char()
                while depth > 0 and not close_brace.is_end():
                    ch = close_brace.get_char()
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                    close_brace.forward_char()
                if depth != 0:
                    continue
                close_brace.backward_char()
                after_close = close_brace.copy()
                after_close.forward_char()
                document.source_buffer.begin_user_action()
                document.source_buffer.delete(label_start, after_close)
                document.source_buffer.insert(label_start, '\\label{' + new_label + '}')
                document.source_buffer.end_user_action()

    def _update_label_references(self, old_label, new_label):
        import re
        pattern = re.compile(r'(\\(?:ref|eqref|pageref|autoref)\{)' + re.escape(old_label) + r'(\})')
        all_docs = self.data_provider.workspace.open_documents
        for doc in all_docs:
            buffer = doc.source_buffer
            text = doc.get_all_text()
            if old_label not in text:
                continue
            buffer.begin_user_action()
            search_iter = buffer.get_start_iter()
            while True:
                result = search_iter.forward_search('{' + old_label + '}', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
                if result is None:
                    break
                match_start, match_end = result
                before_start = match_start.copy()
                before_start.backward_chars(5)
                before_text = buffer.get_text(before_start, match_start, False)
                if before_text.endswith('\\ref{') or before_text.endswith('\\eqref{') or before_text.endswith('\\pageref{') or before_text.endswith('\\autoref{'):
                    full_start = before_start
                    match_end_copy = match_end.copy()
                    full_text = buffer.get_text(full_start, match_end_copy, False)
                    new_full_text = pattern.sub(r'\1' + new_label + r'\2', full_text)
                    buffer.delete(full_start, match_end_copy)
                    buffer.insert(full_start, new_full_text)
                    search_iter = full_start.copy()
                    search_iter.forward_chars(len(new_full_text))
                else:
                    search_iter = match_end
            buffer.end_user_action()

    def delete_section(self, node):
        block = node.get('block', None)
        if block is None:
            return
        document = self._get_document_for_node(node)
        if document is None:
            return

        start_offset = block[0]
        end_offset = block[1] if block[1] is not None else document.source_buffer.get_end_iter().get_offset()

        dialog = Gtk.MessageDialog(
            parent=self.view.get_root(),
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=_('Delete Section?'),
        )
        dialog.set_modal(True)
        dialog.format_secondary_text(_('Remove the entire section including its content. This action cannot be undone.'))

        dialog.connect('response', lambda d, response: self._on_delete_response(d, response, document, start_offset, end_offset))
        dialog.present()

    def _on_delete_response(self, dialog, response, document, start_offset, end_offset):
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        document.source_buffer.begin_user_action()
        start_iter = document.source_buffer.get_iter_at_offset(start_offset)
        end_iter = document.source_buffer.get_iter_at_offset(end_offset + 1)
        document.source_buffer.delete(start_iter, end_iter)
        document.source_buffer.end_user_action()

    def promote_section(self, node):
        block = node.get('block', None)
        if block is None or block[4] not in self.levels:
            return
        document = self._get_document_for_node(node)
        if document is None:
            return

        current_level = self.levels[block[4]]
        if current_level <= 0:
            return

        types_by_level = {v: k for k, v in self.levels.items()}
        new_type = types_by_level[current_level - 1]
        self._change_section_command(document, block, new_type)

    def demote_section(self, node):
        block = node.get('block', None)
        if block is None or block[4] not in self.levels:
            return
        document = self._get_document_for_node(node)
        if document is None:
            return

        current_level = self.levels[block[4]]
        if current_level >= len(self.levels) - 1:
            return

        types_by_level = {v: k for k, v in self.levels.items()}
        new_type = types_by_level[current_level + 1]
        self._change_section_command(document, block, new_type)

    def _change_section_command(self, document, block, new_type):
        old_type = block[4]
        start_offset = block[0]

        start_iter = document.source_buffer.get_iter_at_offset(start_offset)
        end_iter = start_iter.copy()
        # Find the opening brace
        brace_iter = start_iter.copy()
        result = brace_iter.forward_search('{', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
        if result is None:
            return
        end_iter = result[0]

        old_command_text = document.source_buffer.get_text(start_iter, end_iter, True)
        new_command_text = old_command_text.replace('\\' + old_type, '\\' + new_type)
        if new_command_text == old_command_text:
            # Try with different backslash handling
            new_command_text = '\\' + new_type

        document.source_buffer.begin_user_action()
        document.source_buffer.delete(start_iter, end_iter)
        document.source_buffer.insert(start_iter, new_command_text)
        document.source_buffer.end_user_action()

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
from gi.repository import Gtk, Gdk, GLib

from setzer.app.service_locator import ServiceLocator
import setzer.workspace.sidebar.document_structure_page.todos_viewgtk as todos_section_view


class TodosSection(object):

    def __init__(self, data_provider):
        self.data_provider = data_provider
        self.data_provider.connect('data_updated', self.update_items)

        self.todos = list()
        # 从 settings 恢复「显示所有文档 todos」偏好
        self._show_all = ServiceLocator.get_settings().get_value('window_state', 'todos_show_all_documents')

        self.view = todos_section_view.TodosSectionView(self)

    def on_row_activated(self, row):
        self.jump_to_todo(row.item_data)

    def jump_to_todo(self, todo):
        if todo is None:
            return

        document = todo[2]
        line_number = document.source_buffer.get_iter_at_offset(todo[1]).get_line()
        self.data_provider.workspace.set_active_document(document)
        document.place_cursor(line_number)
        document.scroll_cursor_with_context()
        self.data_provider.workspace.active_document.view.source_view.grab_focus()

    def copy_todo(self, todo):
        if todo is None:
            return
        text = todo[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def delete_todo(self, todo):
        if todo is None:
            return

        text, offset, document = todo
        if document is None:
            return

        start_iter = document.source_buffer.get_iter_at_offset(offset)
        end_iter = start_iter.copy()

        # Find the opening brace after \todo
        brace_iter = start_iter.copy()
        result = brace_iter.forward_search('{', Gtk.TextSearchFlags.VISIBLE_ONLY, None)
        if result is None:
            return
        open_brace = result[0]

        # Find the matching closing brace
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
            document.source_buffer.delete(start_iter, close_brace)
            # Delete the closing } and any trailing newline
            end_iter = close_brace.copy()
            after_close = close_brace.copy()
            after_close.forward_char()
            next_char = after_close.get_char()
            if next_char == '\r':
                end_iter.forward_char()
                after_close.forward_char()
                if after_close.get_char() == '\n':
                    end_iter.forward_char()
            elif next_char == '\n':
                end_iter.forward_char()
            else:
                end_iter.forward_char()
            document.source_buffer.delete(close_brace, end_iter)
            document.source_buffer.end_user_action()

    def toggle_show_all(self, show_all):
        '''切换是否显示所有打开文档的 todos。同时持久化到 settings。'''
        settings = ServiceLocator.get_settings()
        settings.set_value('window_state', 'todos_show_all_documents', show_all)
        self._show_all = show_all
        self.update_items()

    #@timer
    def update_items(self, *params):
        todos = list()
        document = self.data_provider.document
        # 始终包含当前文档的 todos
        for todo in document.parser.symbols['todos_with_offset']:
            todos.append([todo[0], todo[1], document])
        # 若开启「显示所有文档」，追加其他已打开 LaTeX 文档的 todos
        if self._show_all and document is not None:
            workspace = self.data_provider.workspace
            for doc in workspace.open_documents:
                if doc is document and doc.is_latex_document():
                    continue
                if not doc.is_latex_document():
                    continue
                for todo in doc.parser.symbols['todos_with_offset']:
                    todos.append([todo[0], todo[1], doc])
        # 包含子文件（\input 等）
        for include_document in self.data_provider.integrated_includes:
            for todo in include_document.parser.symbols['todos_with_offset']:
                # 若已在上一步包含（show_all 且 include_document 也在 open_documents 中），
                # 避免重复追加（已包含的 offset 唯一，set 去重）
                if self._show_all:
                    continue
                todos.append([todo[0], todo[1], include_document])
        todos.sort(key=lambda todo: todo[1])
        self.todos = todos

        self.view.populate()

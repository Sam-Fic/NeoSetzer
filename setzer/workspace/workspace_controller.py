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

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator

import time
from gi.repository import GLib


class WorkspaceController(object):
    ''' Mediator between workspace and view. '''
    
    def __init__(self, workspace):

        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()

        # Toggle 互斥同步守卫：on_preview_toggle_toggled 等会在设置 workspace
        # 状态后交叉调用对方的 set_active(False)（如 preview 激活则 help_toggle
        # 置 False）。这会触发对方的 toggled 信号，当前虽因 set_show_preview_or_help
        # 的幂等性（状态未变则 if 挡掉）恰好收敛，但依赖幂等性脆弱——未来逻辑
        # 变化可能引入冗余调用甚至循环。guard 在程序化 set_active 期间置 True，
        # 让对方回调直接 return，把互斥语义从「靠幂等性收敛」改为「显式跳过」。
        self._syncing_toggles = False

        self.main_window.headerbar.preview_toggle.connect('toggled', self.on_preview_toggle_toggled)
        self.main_window.headerbar.help_toggle.connect('toggled', self.on_help_toggle_toggled)

        self.main_window.headerbar.document_structure_toggle.connect('toggled', self.on_document_structure_toggle_toggled)
        self.main_window.headerbar.symbols_toggle.connect('toggled', self.on_symbols_toggle_toggled)

        # populate workspace
        self.workspace.populate_from_disk()
        open_documents = self.workspace.open_documents
        if len(open_documents) > 0:
            active_filename = getattr(self.workspace, '_restore_active_filename', None)
            if active_filename:
                target = next((d for d in open_documents if d.get_filename() == active_filename), None)
                if target is not None:
                    self.workspace.set_active_document(target)
                else:
                    self.workspace.set_active_document(open_documents[-1])
            else:
                self.workspace.set_active_document(open_documents[-1])
            self.workspace._restore_active_filename = None
        GLib.idle_add(self._restore_document_states)

    def _restore_document_states(self):
        for document in self.workspace.get_all_documents():
            # 懒加载文档：缓冲区尚空，偏移恢复会失败且会清掉 _restore_*_offset
            # （_load_file_content 需要这些值在内容加载后恢复）。跳过，留给
            # _load_content_if_pending / _load_file_content 处理。
            if getattr(document, '_content_pending', False):
                continue
            cursor_offset = getattr(document, '_restore_cursor_offset', None)
            if cursor_offset is not None:
                try:
                    buf = document.source_buffer
                    if cursor_offset <= buf.get_end_iter().get_offset():
                        document.source_buffer.place_cursor(buf.get_iter_at_offset(cursor_offset))
                except Exception:
                    pass
                document._restore_cursor_offset = None
            scroll_offset = getattr(document, '_restore_scroll_offset', None)
            if scroll_offset is not None:
                try:
                    adj = document.view.scrolled_window.get_vadjustment()
                    # GLib idle 回调的返回值：falsy（False/0/None）→ 移除源，
                    # truthy → 重新调度。原实现误写成 lambda 返回元组
                    # `(a.set_value(v), False)`——非空元组恒 truthy，导致 idle
                    # 永久 reschedule，每帧重设同一滚动值，并把用户后续手动滚动
                    # 持续抢回原位置。改为显式函数 return False（perf-15 问题 1 /
                    # perf-18 CRITICAL-1）。
                    def _apply_scroll(adj=adj, v=scroll_offset):
                        adj.set_value(v)
                        return False
                    GLib.idle_add(_apply_scroll)
                except Exception:
                    pass
                document._restore_scroll_offset = None
        return False

    def on_preview_toggle_toggled(self, toggle_button, parameter=None):
        if self._syncing_toggles:
            return
        show_preview = toggle_button.get_active()
        if show_preview:
            show_help = False
        else:
            show_help = self.workspace.show_help
        self.workspace.set_show_preview_or_help(show_preview, show_help)

        if show_preview:
            self._syncing_toggles = True
            self.main_window.headerbar.help_toggle.set_active(False)
            self._syncing_toggles = False

    def on_help_toggle_toggled(self, toggle_button, parameter=None):
        if self._syncing_toggles:
            return
        show_help = toggle_button.get_active()
        if show_help:
            show_preview = False
        else:
            show_preview = self.workspace.show_preview
        self.workspace.set_show_preview_or_help(show_preview, show_help)

        if show_help:
            self._syncing_toggles = True
            self.main_window.headerbar.preview_toggle.set_active(False)
            self._syncing_toggles = False

    def on_document_structure_toggle_toggled(self, toggle_button, parameter=None):
        if self._syncing_toggles:
            return
        show_document_structure = toggle_button.get_active()
        if show_document_structure:
            show_symbols = False
        else:
            show_symbols = self.workspace.show_symbols
        self.workspace.set_show_symbols_or_document_structure(show_symbols, show_document_structure)

        if show_document_structure:
            self._syncing_toggles = True
            self.main_window.headerbar.symbols_toggle.set_active(False)
            self._syncing_toggles = False

    def on_symbols_toggle_toggled(self, toggle_button, parameter=None):
        if self._syncing_toggles:
            return
        show_symbols = toggle_button.get_active()
        if show_symbols:
            show_document_structure = False
        else:
            show_document_structure = self.workspace.show_document_structure
        self.workspace.set_show_symbols_or_document_structure(show_symbols, show_document_structure)

        if show_symbols:
            self._syncing_toggles = True
            self.main_window.headerbar.document_structure_toggle.set_active(False)
            self._syncing_toggles = False



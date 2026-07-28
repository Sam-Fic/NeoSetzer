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

        self._syncing_toggles = False
        self._last_preview_help = ('preview', False)

        self.main_window.headerbar.sidebar_toggle.connect('toggled', self.on_sidebar_toggle_toggled)
        self.main_window.headerbar.preview_help_toggle.connect('toggled', self.on_preview_help_toggle_toggled)

        # populate workspace
        # ③ 启动偏好：on_startup='empty' 时不恢复上次会话，直接显示欢迎屏。
        on_startup = self.workspace.settings.get_value('preferences', 'on_startup')
        if on_startup == 'empty':
            self.workspace.populated = True
        else:
            self.workspace.populate_from_disk()
        open_documents = self.workspace.open_documents
        if len(open_documents) > 0:
            active_filename = getattr(self.workspace, '_restore_active_filename', None)
            if active_filename:
                target = next(
                    (d for d in open_documents
                     if d.get_filename() == active_filename
                     or getattr(d, '_untitled_id', None) == active_filename),
                    None
                )
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

    def on_sidebar_toggle_toggled(self, toggle_button, parameter=None):
        show = toggle_button.get_active()
        self.workspace.set_show_sidebar(show)

    def on_preview_help_toggle_toggled(self, toggle_button, parameter=None):
        show = toggle_button.get_active()
        if self.workspace.is_preview_popped_out():
            # 预览已弹出独立窗口：toggle 控制 help 开关（侧栏只显示帮助）。
            # show_preview 在 popped_out 时被忽略，故 toggle 直接操作 show_help。
            self.workspace.set_show_preview_or_help(False, show)
            return
        if show:
            if not self.workspace.show_preview and not self.workspace.show_help:
                self.workspace.set_show_preview_or_help(*self._last_preview_help)
            else:
                self.workspace.set_show_preview_or_help(self.workspace.show_preview, self.workspace.show_help)
        else:
            if self.workspace.show_help:
                self._last_preview_help = (False, True)
            elif self.workspace.show_preview:
                self._last_preview_help = (True, False)
            self.workspace.set_show_preview_or_help(False, False)

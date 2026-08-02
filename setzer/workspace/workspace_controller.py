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

        # 加载指示器：连接 workspace 的 loading 信号到主窗口 spinner
        self.workspace.connect('loading-started', self.on_loading_started)
        self.workspace.connect('loading-finished', self.on_loading_finished)

        # populate workspace
        # ③ 启动偏好：on_startup='empty' 时不恢复上次会话，直接显示欢迎屏。
        on_startup = self.workspace.settings.get_value('preferences', 'on_startup')
        if on_startup == 'empty':
            self.workspace.populated = True
            GLib.idle_add(self._restore_document_states)
        else:
            # 会话恢复：先显示 spinner，再延迟到 idle 执行 populate_from_disk。
            # 这样 spinner 在首帧渲染出来（覆盖窗口 present 后到文档加载完成
            # 之间的空白/卡顿期），而非在同步调用栈中 show→hide 导致从不渲染。
            # populate_from_disk 内部的 _loading_start/_loading_finish 会再次
            # show/hide，但 hide 被 deferred-hide 机制推迟到 idle，确保至少
            # 渲染一帧。set_active_document 触发的 on_new_active_document 会
            # 重新 show（取消 pending hide），文档激活完成后才真正 hide。
            self.main_window.show_loading_spinner()
            GLib.idle_add(self._deferred_populate_from_disk)

    def _deferred_populate_from_disk(self):
        '''idle 回调：执行会话恢复的文档加载与激活。

        延迟到 idle 确保 spinner 已渲染：__init__ 中先 show_loading_spinner，
        本回调在主循环下次 idle 时执行，此时 spinner 已绘制到屏幕。
        '''
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
        else:
            # 无文档可恢复（首次启动 / workspace.json 为空 / 文件全被移动）：
            # populate_from_disk 可能未触发 _loading_start/finish（data 为 None
            # 时提前返回），显式 hide 兜底，避免 spinner 永久停留。
            self.main_window.hide_loading_spinner()
        GLib.idle_add(self._restore_document_states)
        return False

    def _restore_document_states(self):
        for document in self.workspace.get_all_documents():
            # 懒加载文档：缓冲区尚空，偏移恢复会失败且会清掉 _restore_*_offset
            # （_load_file_content 需要这些值在内容加载后恢复）。跳过，留给
            # _load_content_if_pending / _load_file_content 处理。
            if getattr(document, '_content_pending', False):
                continue
            # 内容就绪后恢复游标位置（及可选选区）。
            document.apply_restored_cursor()
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

    def on_loading_started(self, workspace):
        '''显示加载中 spinner。'''
        if hasattr(self.main_window, 'show_loading_spinner'):
            self.main_window.show_loading_spinner()

    def on_loading_finished(self, workspace):
        '''隐藏加载中 spinner。'''
        if hasattr(self.main_window, 'hide_loading_spinner'):
            self.main_window.hide_loading_spinner()

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

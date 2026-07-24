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

import time
import os.path

from gi.repository import GLib

from setzer.helpers.observable import Observable
import setzer.helpers.path as path_helpers


class DataProvider(Observable):

    def __init__(self, sidebar, workspace):
        Observable.__init__(self)

        self.workspace = workspace
        self.document = None

        self.integrated_includes = dict()

        # 全路径 idle 去抖：on_buffer_changed / on_new_document /
        # on_new_active_document / on_is_root_changed 共用一个 idle，把所有
        # 同帧内的刷新合并为一次 update_data。打开文档时 new_document +
        # new_active_document 先后触发但只重建一遍；打字时 on_document_change
        # + on_cursor_change 两路也只刷新一遍。
        self._update_data_idle_id = None

        self.signal_id = sidebar.view.connect('realize', self.on_realize)
        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)

    def on_new_document(self, workspace, document=None):
        self._schedule_update_data()

    def on_document_removed(self, workspace, document=None):
        self._schedule_update_data()

    def on_new_active_document(self, workspace, document=None):
        self.set_document()

    def on_root_state_change(self, workspace, root_state=None):
        self.set_document()

    def on_buffer_changed(self, document, parameter=None):
        self._schedule_update_data()

    def on_cursor_position_changed(self, document):
        self.add_change_code('cursor_position_changed', document)

    def _schedule_update_data(self):
        '''所有触发侧边栏刷新的路径（文档新增/移除/切换/文本改动）共用一个
        idle 去抖。打开文档时 new_document + new_active_document 会在同一帧
        内先后触发，合并为一次 update_data，避免侧边栏四个 section 连续重建
        两遍。idle 调度发生在文档视图已切换、编辑器已可交互之后，因此用户
        不会感到文档切换本身卡顿——侧边栏在空闲时刻补上即可。'''
        if self._update_data_idle_id is None:
            self._update_data_idle_id = GLib.idle_add(self._update_data_idle)

    def _update_data_idle(self):
        self._update_data_idle_id = None
        self.update_data()
        return False

    def on_is_root_changed(self, document, parameter=None):
        self._schedule_update_data()

    def on_realize(self, view, *parameter):
        view.disconnect(self.signal_id)
        self.update_data()

    def set_document(self):
        document = self.workspace.get_root_or_active_latex_document()
        if document != self.document:
            # 信号断开/重连必须同步完成：若延迟到 idle，期间旧文档的 changed
            # 信号仍会触发 on_buffer_changed，导致侧边栏为旧文档重建。
            if self.document != None:
                self.document.disconnect('changed', self.on_buffer_changed)
                self.document.disconnect('is_root_changed', self.on_is_root_changed)
                self.document.disconnect('cursor_position_changed', self.on_cursor_position_changed)
            self.document = document
            if self.document != None:
                self.document.connect('changed', self.on_buffer_changed)
                self.document.connect('is_root_changed', self.on_is_root_changed)
                self.document.connect('cursor_position_changed', self.on_cursor_position_changed)
            # 通知文档结构页文档已变更（包括 None）。
            self.add_change_code('document_changed', self.document)
            # update_data（触发四个 section 的 clear_rows + 重建 Adw.ActionRow）
            # 延迟到 idle：让 set_visible_child / grab_focus 先返回，编辑器立即可
            # 交互，侧边栏在空闲时刻补上。打开大文档时这是 575ms → ~0ms 感知延迟
            # 的关键——主帧不再被侧边栏重建阻塞。
            self._schedule_update_data()

    def update_data(self, *params):
        if self.document == None: return

        self.update_integrated_includes()
        self.add_change_code('data_updated')

    def update_integrated_includes(self):
        integrated_includes = dict()
        if self.document.get_is_root():
            for filename, offset in self.document.parser.symbols['included_latex_files']:
                filename = path_helpers.get_abspath(filename, self.document.get_dirname())
                document = self.workspace.get_document_by_filename(filename)
                if document:
                    integrated_includes[document] = (document, offset)

        # 仅连接新加入的文档，避免重复 connect（修复信号泄漏：原实现每次调用
        # 都对仍包含的文档叠加连接，导致一次文本改动触发 N 次侧边栏重建）。
        # 单次集合差集替代两次线性扫描，文档数多时省一遍迭代。
        new_docs = integrated_includes.keys() - self.integrated_includes.keys()
        old_docs = self.integrated_includes.keys() - integrated_includes.keys()
        for document in new_docs:
            document.connect('changed', self.on_buffer_changed)
        for document in old_docs:
            try:
                document.disconnect('changed', self.on_buffer_changed)
            except Exception:
                pass
        self.integrated_includes = integrated_includes

    def get_includes(self):
        includes = list()
        for filename, offset in self.document.parser.symbols['included_latex_files']:
            filename = path_helpers.get_abspath(filename, self.document.get_dirname())
            document = self.workspace.get_document_by_filename(filename)
            if document and document in self.integrated_includes:
                includes.append({'filename': filename, 'offset': offset, 'document': document})
            else:
                includes.append({'filename': filename, 'offset': offset, 'document': None})
        return includes



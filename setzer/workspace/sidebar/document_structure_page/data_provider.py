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

import os.path

from gi.repository import GLib

from setzer.helpers.observable import Observable
import setzer.helpers.path as path_helpers
from setzer.project.files import scan_document_dependencies


class DataProvider(Observable):

    def __init__(self, sidebar, workspace):
        Observable.__init__(self)

        self.workspace = workspace
        self.document = None

        self.integrated_includes = dict()
        # includes 缓存：update_integrated_includes 与 get_includes 原各自遍历
        # included_latex_files + get_abspath + get_document_by_filename，同一次
        # data_updated 刷新中被 FilesSection + StructureSection 各调一次 get_includes，
        # 加上 update_integrated_includes 共 3 次重复遍历。改为在
        # update_integrated_includes 中一次性构建，get_includes 直接返回。
        self._includes_cache = list()
        # Files 列表独立缓存：主文档行 + 主文档视角的 includes + bib。
        # 与 _includes_cache 分开——后者（活跃/root 文档自己的 includes）
        # 供 structure/labels/todos 按 offset 合并，语义不能混。主文档解析：
        # 显式 root → root 文档；否则 magic comment / build.json root（只读，
        # 已打开用其增量 parser，未打开读盘离线扫描）；再退回活跃文档自身。
        self._files_cache = {
            'main': None,
            'includes': list(),
            'bibliographies': list(),
        }
        # 未打开主文档的离线扫描结果缓存（按 mtime 失效）。
        self._offline_files_cache = None

        # 解析完成、改名及文档生命周期事件共用一个 idle，光标变化仅转发通知。
        self._update_data_idle_id = None
        self._subscribed_documents = set()
        self._subscribed_parsers = set()

        self.signal_id = sidebar.view.connect('realize', self.on_realize)
        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        self._sync_document_subscriptions()

    def _sync_document_subscriptions(self):
        # 所有文档 TODO 依赖非 include 文档；订阅集合不受当前根文档影响。
        documents = {document for document in self.workspace.get_all_documents()
                     if getattr(document, 'parser', None) is not None}
        parsers = {document.parser for document in documents}
        for document in self._subscribed_documents - documents:
            document.disconnect('filename_change', self.on_filename_changed)
        for parser in self._subscribed_parsers - parsers:
            parser.disconnect('finished_parsing', self.on_finished_parsing)
        for document in documents - self._subscribed_documents:
            document.connect('filename_change', self.on_filename_changed)
        for parser in parsers - self._subscribed_parsers:
            parser.connect('finished_parsing', self.on_finished_parsing)
        self._subscribed_documents = documents
        self._subscribed_parsers = parsers

    def on_new_document(self, workspace, document=None):
        self._sync_document_subscriptions()
        self._schedule_update_data()

    def on_document_removed(self, workspace, document=None):
        self._sync_document_subscriptions()
        self._schedule_update_data()

    def on_new_active_document(self, workspace, document=None):
        self.set_document()

    def on_root_state_change(self, workspace, root_state=None):
        self.set_document()

    def on_finished_parsing(self, parser, parameter=None):
        self._schedule_update_data()

    def on_filename_changed(self, document, filename=None):
        self._schedule_update_data()

    def on_cursor_position_changed(self, document):
        self.add_change_code('cursor_position_changed', document)

    def _schedule_update_data(self):
        '''解析完成、改名及文档生命周期刷新合并到 idle，避免阻塞文档切换。'''
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
            # 当前文档的根状态与光标信号同步切换，parser 订阅仍覆盖全部已打开文档。
            if self.document != None:
                self.document.disconnect('is_root_changed', self.on_is_root_changed)
                self.document.disconnect('cursor_position_changed', self.on_cursor_position_changed)
            self.document = document
            if self.document != None:
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
        self.update_files_cache()
        self.add_change_code('data_updated')

    def update_integrated_includes(self):
        integrated_includes = dict()
        includes_cache = list()
        # 不再要求显式设 root 才构建：self.document 本就是 root-or-active
        # 文档，设了 root 时行为不变，未设 root 时跟随活跃文档，否则直接
        # 打开主文件看不到任何子 tex。
        # 常规 include 文件即使尚未存在也保留占位，维持原有行为；而
        # documentclass/LoadLetterOption 只应显示项目目录中真实存在的
        # .cls/.lco/.loc 文件，避免把系统的 article.cls 等误当项目文件。
        dependencies = [
            (filename, offset, False)
            for filename, offset in self.document.parser.symbols['included_latex_files']
        ]
        dependencies.extend(
            (filename, offset, True)
            for filename, offset in self.document.parser.symbols.get('included_project_files', list())
        )
        for filename, offset, must_be_local in dependencies:
            abs_path = path_helpers.get_abspath(filename, self.document.get_dirname())
            document = self.workspace.get_document_by_filename(abs_path)
            if must_be_local and document is None and not os.path.isfile(abs_path):
                continue
            # 一并构建 includes 缓存：document 仅当确实打开（在
            # integrated_includes 中）时非 None，与原 get_includes 语义一致。
            if document:
                integrated_includes[document] = (document, offset)
                includes_cache.append({'filename': abs_path, 'offset': offset, 'document': document})
            else:
                includes_cache.append({'filename': abs_path, 'offset': offset, 'document': None})
        self._includes_cache = includes_cache
        self.integrated_includes = integrated_includes

    def update_files_cache(self):
        '''Files 列表数据：主文档行 + 主文档视角的 includes + bib。

        处于子文件时仍显示主文件的列表：显式 root 时主文档 = root；否则解析
        活跃文档的 magic comment / build.json root（只读），root 文档已打开用
        其增量 parser 符号，未打开读盘离线扫描；都无则主文档 = 活跃文档自身。
        '''
        main_document = None
        main_filename = None
        offline = None

        if self.document.get_is_root():
            main_document = self.document
            main_filename = self.document.get_filename()
        else:
            root_filename = self.workspace.resolve_project_root_filename(self.document)
            active_filename = self.document.get_filename()
            if root_filename is not None and active_filename is not None \
                    and os.path.abspath(root_filename) != os.path.abspath(active_filename):
                # root 文件已打开 → 直接用其增量 parser 符号（随编辑实时刷新）。
                main_document = self.workspace.get_document_by_filename(root_filename)
                if main_document is not None:
                    main_filename = main_document.get_filename()
                elif os.path.isfile(root_filename):
                    main_filename = os.path.abspath(root_filename)
                    offline = self._get_offline_scan(main_filename)
            if main_document is None and main_filename is None:
                # 无可解析 root（或 root 指向自身/磁盘缺失）：主文档退回活跃文档。
                main_document = self.document
                main_filename = self.document.get_filename()

        if main_document is not None:
            symbols = main_document.parser.symbols
            latex_includes = symbols['included_latex_files']
            project_includes = symbols.get('included_project_files', list())
            bib_entries = symbols.get('bibliographies', set())
        else:
            latex_includes = offline['latex_includes']
            project_includes = offline['project_files']
            bib_entries = offline['bibliographies']

        # 与 update_integrated_includes 相同的过滤语义：常规 include 保留占位，
        # project 文件与 bib 必须磁盘存在，避免死条目。
        main_dir = os.path.dirname(main_filename) if main_filename else ''
        includes_cache = list()
        dependencies = [
            (filename, offset, False)
            for filename, offset in latex_includes
        ]
        dependencies.extend(
            (filename, offset, True)
            for filename, offset in project_includes
        )
        for filename, offset, must_be_local in dependencies:
            abs_path = path_helpers.get_abspath(filename, main_dir)
            document = self.workspace.get_document_by_filename(abs_path)
            if must_be_local and document is None and not os.path.isfile(abs_path):
                continue
            includes_cache.append({'filename': abs_path, 'offset': offset, 'document': document})

        bibliographies_cache = list()
        for filename in sorted(bib_entries):
            abs_path = path_helpers.get_abspath(filename, main_dir)
            if not os.path.isfile(abs_path):
                continue
            bibliographies_cache.append({
                'filename': abs_path,
                'offset': None,
                'document': self.workspace.get_document_by_filename(abs_path),
            })

        self._files_cache = {
            'main': {'filename': main_filename, 'document': main_document},
            'includes': includes_cache,
            'bibliographies': bibliographies_cache,
        }

    def _get_offline_scan(self, filename):
        '''未打开主文档的离线符号扫描，按 mtime 缓存避免每键重读重扫。'''
        cached = self._offline_files_cache
        mtime = os.path.getmtime(filename)
        if cached is not None and cached['filename'] == filename and cached['mtime'] == mtime:
            return cached
        latex_includes, project_files, bibliographies = scan_document_dependencies(filename)
        result = {
            'filename': filename,
            'mtime': mtime,
            'latex_includes': latex_includes,
            'project_files': project_files,
            'bibliographies': bibliographies,
        }
        self._offline_files_cache = result
        return result

    def get_includes(self):
        # 返回 update_integrated_includes 预构建的缓存，避免每次调用重复遍历
        # included_latex_files + get_abspath + get_document_by_filename。
        return self._includes_cache

    def get_files_main(self):
        '''Files 列表主文档行条目：{'filename': 绝对路径或 None, 'document': 已打开文档对象或 None}。'''
        return self._files_cache['main']

    def get_files_includes(self):
        return self._files_cache['includes']

    def get_bibliographies(self):
        return self._files_cache['bibliographies']



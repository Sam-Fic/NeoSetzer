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
from gi.repository import GObject, GLib, Adw

import threading, queue
import time, re, difflib, unicodedata

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.latex_db import LaTeXDB
from setzer.settings.document_settings import DocumentSettings
import setzer.document.build_system.builder.builder_build_latex as builder_build_latex
import setzer.document.build_system.builder.builder_build_bibtex as builder_build_bibtex
import setzer.document.build_system.builder.builder_build_biber as builder_build_biber
import setzer.document.build_system.builder.builder_build_makeindex as builder_build_makeindex
import setzer.document.build_system.builder.builder_build_glossaries as builder_build_glossaries
import setzer.document.build_system.builder.builder_forward_sync as builder_forward_sync
import setzer.document.build_system.builder.builder_backward_sync as builder_backward_sync
import setzer.document.build_system.query.query as query
from setzer.helpers.observable import Observable


class BuildSystem(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.settings = ServiceLocator.get_settings()
        # 每文档 LaTeX 解释器覆盖：None 表示跟随全局 preferences['latex_interpreter']。
        # 由 DocumentSettings 从文档状态文件加载/保存（见 document_settings.py）。
        self.latex_interpreter = None
        self.active_query = None

        # possible states: idle, ready_for_building
        # building_in_progress, building_to_stop
        self.build_state = 'idle'

        # possible values: build, forward_sync, build_and_forward_sync
        self.build_mode = 'build_and_forward_sync'

        # 标记本次构建是否由自动构建（AutoBuild）触发。auto_build.on_timer
        # 在调用 build_and_forward_sync 前置 True；actions.build / save_and_build
        # 等手动入口置 False。build_log.update_items 据此结合
        # auto_build_autoshow_errors 设置决定是否自动弹出日志弹窗——
        # 自动构建时用户可能正在打字，频繁弹窗打扰写作。
        self.is_auto_build = False

        self.document_has_been_built = False
        self.build_time = None
        self.last_build_start_time = None

        self.has_synctex_file = False
        self.backward_sync_data = None
        self.forward_sync_arguments = None
        self.can_sync = False
        self.update_can_sync()

        self.build_log_data = {'items': list(), 'error_count': 0, 'warning_count': 0, 'badbox_count': 0}

        self.builders = dict()
        self.builders['build_latex'] = builder_build_latex.BuilderBuildLaTeX()
        self.builders['build_bibtex'] = builder_build_bibtex.BuilderBuildBibTeX()
        self.builders['build_biber'] = builder_build_biber.BuilderBuildBiber()
        self.builders['build_makeindex'] = builder_build_makeindex.BuilderBuildMakeindex()
        self.builders['build_glossaries'] = builder_build_glossaries.BuilderBuildGlossaries()
        self.builders['forward_sync'] = builder_forward_sync.BuilderForwardSync()
        self.builders['backward_sync'] = builder_backward_sync.BuilderBackwardSync()

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用。
        不再有定时器需移除；置 active_query=None 使任何在途的
        worker 完成回调（_on_query_done）走 `is not query` 守卫被丢弃。'''
        self.active_query = None

    def change_build_state(self, state):
        self.build_state = state

        if self.build_mode in ['build', 'build_and_forward_sync']:
            if state == 'building_in_progress':
                self.last_build_start_time = time.time()
            elif state == 'building_to_stop':
                pass
            elif state == 'idle':
                pass
        self.add_change_code('build_state_change', self.build_state)

    def get_build_state(self):
        return self.build_state

    def show_build_state(self, message):
        self.add_change_code('build_state', message)

    def set_build_mode(self, mode):
        self.build_mode = mode

    def get_build_mode(self):
        return self.build_mode

    def set_has_synctex_file(self, has_synctex_file):
        self.has_synctex_file = has_synctex_file
        self.update_can_sync()

    def update_can_sync(self, *params):
        self.can_sync = False
        if self.has_synctex_file and self.document.preview.poppler_document != None:
            self.can_sync = True
        else:
            self.can_sync = False
        self.add_change_code('can_sync_changed', self.can_sync)

    def forward_sync(self, active_document):
        if not self.can_sync: return

        self.set_forward_sync_arguments(active_document)
        self.set_build_mode('forward_sync')
        self.start_building()

    def backward_sync(self, page, x, y, word, context, pdf_line_offset=None, pdf_line_text=None):
        if not self.can_sync: return

        self.backward_sync_data = {'page': page, 'x': x, 'y': y, 'word': word, 'context': context, 'pdf_line_offset': pdf_line_offset, 'pdf_line_text': pdf_line_text}
        self.set_build_mode('backward_sync')
        self.start_building()

    def build_and_forward_sync(self, active_document):
        self.set_forward_sync_arguments(active_document)
        self.set_build_mode('build_and_forward_sync')
        self.start_building()

    def set_forward_sync_arguments(self, active_document):
        sb = active_document.source_buffer
        self.forward_sync_arguments = dict()
        self.forward_sync_arguments['filename'] = active_document.get_filename()
        self.forward_sync_arguments['line'] = sb.get_iter_at_mark(sb.get_insert()).get_line() + 1
        self.forward_sync_arguments['line_offset'] = sb.get_iter_at_mark(sb.get_insert()).get_line_offset() + 1

    def set_build_log_items(self, log_items):
        # 单次遍历 log_items 完成计数与 (filename, items) 分组，并附加阶段（stage）信息。
        # item 元组格式：item[0]=type, item[1]=stage, item[2]=filename,
        #            item[3]=line_number, item[4]=description
        build_log_items = list()
        error_count = 0
        warning_count = 0
        badbox_count = 0
        main_filename = self.document.filename

        main_items = None
        other_filenames = list()
        for filename, items in log_items.items():
            error_count += len(items['error'])
            warning_count += len(items['warning'])
            badbox_count += len(items['badbox'])
            if filename == main_filename:
                main_items = items
            else:
                other_filenames.append((filename, items))

        type_order = (('Error', 'error'), ('Warning', 'warning'), ('Badbox', 'badbox'))
        for type_name, key in type_order:
            if main_items is not None:
                for item in main_items[key]:
                    build_log_items.append((type_name, 'LaTeX', main_filename, item[1], item[2]))
            for filename, items in other_filenames:
                stage = 'BibTeX' if filename == 'BibTeX' else 'LaTeX'
                for item in items[key]:
                    build_log_items.append((type_name, stage, filename, item[1], item[2]))

        self.build_log_data = {'items': build_log_items, 'error_count': error_count, 'warning_count': warning_count, 'badbox_count': badbox_count}

    def invalidate_build_log(self):
        self.add_change_code('build_log_update')

    def get_error_count(self):
        return self.build_log_data['error_count']

    def get_warning_count(self):
        return self.build_log_data['warning_count']

    def get_badbox_count(self):
        return self.build_log_data['badbox_count']

    def _on_query_done(self, query):
        # 由 worker 线程经 GLib.idle_add 调度到主线程，替代原 50ms 轮询 results_loop。
        # 仅当 query 仍是当前 active_query 时处理结果，否则丢弃——覆盖：
        #   - stop_building 已置 active_query=None（用户中止构建）
        #   - add_query 已替换为新 query（中止旧构建并立刻起新构建）
        #   - shutdown 已置 active_query=None（文档关闭）
        # 三种情形下旧 query 的结果都不应再处理，与原 results_loop 的
        # `if self.active_query != None` 守卫语义一致。
        if self.active_query is not query:
            # 用户手动中止构建后，worker 线程结束时将 building_to_stop → idle。
            # add_query 场景不触发（state 已被设为 building_in_progress）。
            if self.build_state == 'building_to_stop':
                self.change_build_state('idle')
            return False

        build_result = query.get_build_result()
        forward_sync_result = query.get_forward_sync_result()
        backward_sync_result = query.get_backward_sync_result()
        if forward_sync_result != None or backward_sync_result != None or build_result != None:
            self.parse_result({'build': build_result, 'forward_sync': forward_sync_result, 'backward_sync': backward_sync_result})
        self.active_query = None
        return False   # 一次性 idle，不重复

    def parse_result(self, result_blob):
        # 消费一次构建（含 forward/backward sync）的结果：填充构建日志、
        # 切换预览 PDF、置 document_has_been_built=True 并保存文档状态，
        # 使日志在文档切换与会话间持久化。refactor 中该方法被误删，
        # 导致 _on_query_done 每次构建抛 AttributeError、日志永不填充。
        if result_blob['build'] != None or result_blob['forward_sync'] != None:
            if result_blob['build'] != None:
                try:
                    pdf_filename = result_blob['build']['pdf_filename']
                except KeyError:
                    pdf_filename = None
                # 仅当构建确实产出 PDF 才切换预览，避免失败构建把预览
                # 闪成空白；未产出 PDF 且仍在显示旧 PDF 时标记为 stale。
                if pdf_filename != None:
                    self.document.preview.set_pdf_filename(pdf_filename)
                    self.document.add_change_code('pdf_updated')
                else:
                    if self.document.preview.poppler_document != None:
                        self.document.preview.set_pdf_is_stale(True)

            if result_blob['forward_sync'] != None:
                self.document.preview.set_synctex_rectangles(result_blob['forward_sync'])
                self.show_build_state('')

            if result_blob['build'] != None:
                build_blob = result_blob['build']

                if build_blob['error'] == 'interpreter_missing':
                    self.show_build_state('')
                    self.change_build_state('idle')
                    DialogLocator.get_dialog('interpreter_missing').run(build_blob['error_arg'])
                    return

                if build_blob['error'] == 'interpreter_not_working':
                    self.show_build_state('')
                    self.change_build_state('idle')
                    DialogLocator.get_dialog('building_failed').run(build_blob['error_arg'])
                    return

                build_blob['log_messages']['BibTeX'] = build_blob['bibtex_log_messages']
                self.set_build_log_items(build_blob['log_messages'])
                self.build_time = time.time() - self.last_build_start_time

                error_count = self.get_error_count()
                if error_count > 0:
                    self.show_build_state('error')
                else:
                    self.show_build_state('success')

                self.set_has_synctex_file(build_blob['has_synctex_file'])
                self.document_has_been_built = True

        elif result_blob['backward_sync'] != None:
            # 反查（PDF 点击 -> 源文件）。refactor 移除了字符级偏移/词匹配
            # 映射，这里保留行级定位作为最小可用版本。
            if not self.document.root_is_set:
                if result_blob['backward_sync']['filename'] == self.document.get_filename():
                    self.set_synctex_position(self.document, result_blob['backward_sync'])
                    self.document.scroll_cursor_onscreen()
            elif self.document.is_root:
                workspace = ServiceLocator.get_workspace()
                document = workspace.open_document_by_filename(result_blob['backward_sync']['filename'])
                if document != None:
                    self.set_synctex_position(document, result_blob['backward_sync'])
                    document.scroll_cursor_onscreen()

        self.change_build_state('idle')

        if result_blob['build'] != None:
            self.invalidate_build_log()
            # 构建完成后立即保存文档状态，确保日志持久化（即使应用异常退出，
            # 下次启动也能从状态文件恢复上次构建的日志）。
            from setzer.settings.document_settings import DocumentSettings
            try: DocumentSettings.save_document_state(self.document)
            except Exception: pass

        # 构建完成后刷新 LaTeXDB 的 label/bibitem 数据库（事件驱动）。
        LaTeXDB.schedule_parse_included_files()

    def set_synctex_position(self, document, position):
        '''反查光标定位（行级）。refactor 移除了字符级偏移/词匹配映射，
        此处将光标置于 PDF 点击对应的源文件行并高亮整行。'''
        position_found, start = document.source_buffer.get_iter_at_line(position['line'])
        if not position_found:
            return
        end = start.copy()
        if not start.ends_line():
            end.forward_to_line_end()
        document.source_buffer.place_cursor(start)
        document.highlight_section(start, end)
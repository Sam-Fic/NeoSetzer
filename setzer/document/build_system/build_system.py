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
        # 单次遍历 log_items 完成计数与 (filename, items) 分组。
        # 原实现外层 3 次 item_type × 中层 N 次 filename = 3N 次迭代，
        # 且每次都做 3 个 `if item_type == '...'` 字符串比较（共 9N 次），
        # 但只有 1 个分支会累加。优化后 N 次迭代，无字符串比较。
        # 顺序保持与原实现一致：类型优先（Error → Warning → Badbox），
        # 每类内当前文档先于其他文档（其他文档间保持 dict 迭代顺序）。
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
                    build_log_items.append((type_name, item[0], main_filename, item[1], item[2]))
            for filename, items in other_filenames:
                for item in items[key]:
                    build_log_items.append((type_name, item[0], filename, item[1], item[2]))

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
        if result_blob['build'] != None or result_blob['forward_sync'] != None:
            if result_blob['build'] != None:
                try:
                    pdf_filename = result_blob['build']['pdf_filename']
                except KeyError:
                    pdf_filename = None
                # Only swap the preview's PDF when the build actually
                # produced one. When the build failed (no PDF), the
                # previously rendered PDF is kept so the preview does
                # not flicker to blank between builds.
                if pdf_filename != None:
                    self.document.preview.set_pdf_filename(pdf_filename)
                    self.document.add_change_code('pdf_updated')
                else:
                    # 构建未产出 PDF（编译失败）。若预览仍显示上一次成功的 PDF，
                    # 标记为 stale——预览面板据此显示「构建失败，显示的是上一次
                    # 成功的 PDF」横幅，避免用户看到旧 PDF 误以为构建成功。
                    # （set_pdf_filename 在下次构建成功时会自动清除 stale 标记。）
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
            # 构建完成后立即保存文档状态，确保日志持久化。
            # 这样即使应用异常退出，下次启动也能恢复上次构建的日志。
            from setzer.settings.document_settings import DocumentSettings
            try: DocumentSettings.save_document_state(self.document)
            except Exception: pass

        # 构建完成（可能伴随自动保存）后刷新 LaTeXDB 的 label/bibitem
        # 数据库（事件驱动，替代原 3 秒轮询）。
        # 去抖：延迟到 idle 执行，让 parse_result 当前帧的 PDF 切换 /
        # build_log 更新 / build_state 通知先完成，避免 LaTeXDB 的全量
        # stat/read 扫描阻塞用户感知的"构建完成到 UI 就绪"延迟。
        # 连续构建（自动构建）时多次 schedule 也只触发一次实际刷新。
        LaTeXDB.schedule_parse_included_files()

    def add_query(self, query):
        if self.active_query != None:
            # 旧构建被中止：显示 toast 通知用户（手动 F5 触发新构建时可能
            # 上一次构建仍在进行，旧构建被静默丢弃）。
            main_window = ServiceLocator.get_main_window()
            if hasattr(main_window, 'toast_overlay'):
                toast = Adw.Toast.new(_('Previous build cancelled'))
                toast.set_timeout(2)
                main_window.toast_overlay.add_toast(toast)
        self.stop_building(notify=False)
        self.active_query = query
        threading.Thread(target=self.execute_query, args=(query,), daemon=True).start()

        self.change_build_state('building_in_progress')

    def execute_query(self, query):
        while len(query.jobs) > 0:
            if not query.force_building_to_stop:
                self.builders[query.jobs.pop(0)].run(query)
        # worker 线程结束：把结果处理调度到主线程，替代原「设 done 标志 +
        # 主线程 50ms 轮询 is_done()」的 poll-for-completion 模式。
        # GLib.idle_add 线程安全，回调在主线程执行（代码库已有 10+ 处同范式）。
        GLib.idle_add(self._on_query_done, query)

    def start_building(self):
        if self.build_mode == 'forward_sync' and not self.has_synctex_file: return
        if self.build_mode == 'backward_sync' and self.backward_sync_data == None: return
        if self.document.filename == None: return

        self.build_time = None
        mode = self.get_build_mode()
        query_obj = query.Query(self.document.get_filename()[:])

        if mode in ['forward_sync', 'build_and_forward_sync']:
            synctex_arguments = self.forward_sync_arguments

        if mode in ['build', 'build_and_forward_sync']:
            interpreter = self.latex_interpreter or self.settings.get_value('preferences', 'latex_interpreter')
            use_latexmk = self.settings.get_value('preferences', 'use_latexmk')
            build_option_system_commands = self.settings.get_value('preferences', 'build_option_system_commands')
            additional_arguments = ''

            if interpreter == 'tectonic':
                pass
            else:
                lualatex_prefix = ' -' if interpreter == 'lualatex' else ' '
                if build_option_system_commands == 'disable':
                    additional_arguments += lualatex_prefix + '-no-shell-escape'
                elif build_option_system_commands == 'restricted':
                    additional_arguments += lualatex_prefix + '-shell-restricted'
                elif build_option_system_commands == 'enable':
                    additional_arguments += lualatex_prefix + '-shell-escape'

            text = self.document.get_all_text()
            do_cleanup = self.settings.get_value('preferences', 'cleanup_build_files')

        if mode == 'build':
            query_obj.jobs = ['build_latex']
            query_obj.build_data['text'] = text
            query_obj.build_data['latex_interpreter'] = interpreter
            query_obj.build_data['use_latexmk'] = use_latexmk
            query_obj.build_data['additional_arguments'] = additional_arguments
            query_obj.build_data['do_cleanup'] = do_cleanup
        elif mode == 'forward_sync':
            query_obj.jobs = ['forward_sync']
            query_obj.can_sync = True
            query_obj.forward_sync_data['filename'] = synctex_arguments['filename']
            query_obj.forward_sync_data['line'] = synctex_arguments['line']
            query_obj.forward_sync_data['line_offset'] = synctex_arguments['line_offset']
        elif mode == 'backward_sync' and self.backward_sync_data != None:
            query_obj.jobs = ['backward_sync']
            query_obj.can_sync = True
            query_obj.backward_sync_data['page'] = self.backward_sync_data['page']
            query_obj.backward_sync_data['x'] = self.backward_sync_data['x']
            query_obj.backward_sync_data['y'] = self.backward_sync_data['y']
            query_obj.backward_sync_data['word'] = self.backward_sync_data['word']
            query_obj.backward_sync_data['context'] = self.backward_sync_data['context']
            query_obj.backward_sync_data['pdf_line_offset'] = self.backward_sync_data.get('pdf_line_offset')
            query_obj.backward_sync_data['pdf_line_text'] = self.backward_sync_data.get('pdf_line_text')
        else:
            query_obj.jobs = ['build_latex', 'forward_sync']
            query_obj.build_data['text'] = text
            query_obj.build_data['latex_interpreter'] = interpreter
            query_obj.build_data['use_latexmk'] = use_latexmk
            query_obj.build_data['additional_arguments'] = additional_arguments
            query_obj.build_data['do_cleanup'] = do_cleanup
            query_obj.can_sync = False
            query_obj.forward_sync_data['filename'] = synctex_arguments['filename']
            query_obj.forward_sync_data['line'] = synctex_arguments['line']
            query_obj.forward_sync_data['line_offset'] = synctex_arguments['line_offset']

        self.add_query(query_obj)

    def stop_building(self, notify=True):
        if self.active_query != None:
            self.active_query.jobs = []
            self.active_query = None
        for builder in self.builders.values():
            builder.stop_running()
        if notify:
            self.show_build_state('')
            # 使用 building_to_stop 过渡状态：按钮变为不可点击，直到 worker
            # 线程真正退出后 _on_query_done 将状态切回 idle。避免用户在
            # 进程尚未退出时再次点击构建按钮导致冲突。
            self.change_build_state('building_to_stop')

    def set_synctex_position(self, document, position):
        position_found, start = document.source_buffer.get_iter_at_line(position['line'])
        end = start.copy()
        if not start.ends_line():
            end.forward_to_line_end()
        text = document.source_buffer.get_text(start, end, False)

        # Primary: map the clicked PDF character offset to the source line.
        # SequenceMatcher aligns the PDF line text with the source line text
        # (which may differ due to LaTeX commands, ligatures, etc.), giving
        # character-level cursor alignment instead of just line/paragraph.
        pdf_line_offset = position.get('pdf_line_offset')
        pdf_line_text = position.get('pdf_line_text')
        if pdf_line_offset is not None and pdf_line_text:
            src_offset = self._map_pdf_offset_to_source(pdf_line_text, text, pdf_line_offset)
            if src_offset is not None:
                cursor = start.copy()
                cursor.forward_chars(min(src_offset, len(text)))
                # Highlight the word at the cursor for visual feedback.
                hl_start = cursor.copy()
                hl_end = cursor.copy()
                if not hl_start.starts_line():
                    hl_start.backward_word_start()
                if not hl_end.ends_line():
                    hl_end.forward_word_end()
                if hl_start.equal(cursor) and hl_end.equal(cursor) and not hl_end.ends_line():
                    hl_end.forward_char()
                document.source_buffer.place_cursor(cursor)
                document.highlight_section(hl_start, hl_end)
                return

        # Fallback 1: match the clicked word within the source line.
        matches = self.get_synctex_word_bounds(text, position['word'], position['context'])
        if matches != None:
            for word_bounds in matches:
                end = start.copy()
                new_start = start.copy()
                new_start.forward_chars(word_bounds[0])
                end.forward_chars(word_bounds[1])
                document.source_buffer.place_cursor(new_start)
                document.highlight_section(new_start, end)
        else:
            ws_number = len(text) - len(text.lstrip())
            start.forward_chars(ws_number)
            document.source_buffer.place_cursor(start)
            document.highlight_section(start, end)

    def _map_pdf_offset_to_source(self, pdf_text, source_text, pdf_offset):
        '''Map a 0-based character offset in pdf_text to the corresponding
        offset in source_text via fuzzy alignment.

        SequenceMatcher finds matching blocks between the two texts; the PDF
        offset is translated through the block that contains it. When the
        offset falls in a non-matching gap (e.g. inside a LaTeX command that
        has no PDF counterpart), the end of the last preceding match is used.
        Returns the source offset, or None when no mapping is possible.
        '''
        if pdf_offset is None or pdf_offset < 0 or not pdf_text or not source_text:
            return None
        pdf_offset = min(pdf_offset, len(pdf_text))

        # Normalize source to NFC: pdf_text is already NFC (normalized in
        # preview._get_pdf_line_offset), but the Gtk source buffer may store
        # characters in either form. Aligning on a common form lets accented
        # characters match even when Poppler decomposes them.
        source_text = unicodedata.normalize('NFC', source_text)

        matcher = difflib.SequenceMatcher(None, pdf_text, source_text, autojunk=False)
        for block in matcher.get_matching_blocks():
            if block.size == 0:
                continue
            if block.a <= pdf_offset < block.a + block.size:
                return block.b + (pdf_offset - block.a)

        # Offset is in a gap between matching blocks: snap to the end of the
        # last block before it (or 0 if before all blocks).
        src_offset = 0
        for block in matcher.get_matching_blocks():
            if block.size == 0:
                continue
            if block.a + block.size <= pdf_offset:
                src_offset = block.b + block.size
            else:
                break
        return src_offset

    def get_synctex_word_bounds(self, text, word, context):
        if not word: return None
        word = word.split(' ')
        if len(word) > 2:
            word = word[:2]
        word = ' '.join(word)
        regex_pattern = re.escape(word)

        # 原 for c in regex_pattern 逐字符扫描 + replace 替换非 ASCII 字符。
        # re.sub 一次扫描完成所有非 ASCII 字符的替换，语义等价（每个非 ASCII
        # 字符都替换为 (?:\w)），且避免 N 次 str.replace 的字符串分配。
        regex_pattern = re.sub(r'[^\x00-\x7f]', lambda m: r'(?:\w)', regex_pattern)

        # 占位符替换：synctex 的 word 可能含 \x1b/\x1c/\x1d/\- 等文本标记，
        # 替换为对应正则片段。保持原行为不变。
        regex_pattern = regex_pattern.replace('\\x1b', r'(?:\w{2,3})').replace('\\x1c', r'(?:\w{2})').replace('\\x1d', r'(?:\w{2,3})').replace('\\-', r'(?:-{0,1})')
        regex = ServiceLocator.get_regex_object(r'(\W{0,1})' + regex_pattern + r'(\W{0,1})')

        # 循环不变量提到循环外：offset1/offset2 仅依赖 context 与 word，
        # 与 match 无关。原实现每个 match 都重新计算 context.find(word)。
        offset1 = context.find(word)
        offset2 = len(context) - offset1 - len(word)
        lo_pad = max(offset1, 0)
        hi_pad = max(offset2, 0)
        text_len = len(text)

        matches = list()
        top_score = 0.1
        # 复用 SequenceMatcher：set_seq2(context) 一次，循环内仅 set_seq1(match_text)。
        # SequenceMatcher 的 ratio() 在 set_seqs 后会缓存 chaining/autojunk 等中间
        # 状态，原实现每个 match 都新建 SequenceMatcher 重新计算。
        matcher = difflib.SequenceMatcher(None)
        matcher.set_seq2(context)
        for match in regex.finditer(text):
            if not (match.group(1) or match.group(2)):
                # 原实现先算 score 再判断 group，但 score 仅在 group 非空时使用。
                # 提前 continue 跳过 group 皆空的 match，省去 SequenceMatcher.ratio()。
                continue
            match_text = text[max(match.start() - lo_pad, 0):min(match.end() + hi_pad, text_len)]
            matcher.set_seq1(match_text)
            score = matcher.ratio()
            if score > top_score + 0.1:
                top_score = score
                matches = [[match.start() + len(match.group(1)), match.end() - len(match.group(2))]]
                # 提前终止：score >= 0.99 视为完美匹配，不再扫描后续 match。
                # 文档含 N 个相同词的匹配时，原实现 N × SequenceMatcher.ratio()
                # （每个最坏 O(n*m)），此处典型情况 1 次即退出。
                if score >= 0.99:
                    break
            elif score > top_score - 0.1:
                matches.append([match.start() + len(match.group(1)), match.end() - len(match.group(2))])
        if len(matches) > 0:
            return matches
        else:
            return None

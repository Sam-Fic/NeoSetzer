#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from gi.repository import GObject, GLib

import os.path
import subprocess
import sys
import threading

import setzer.workspace.sidebar.document_stats.document_stats_viewgtk as document_stats_section_view
import setzer.helpers.path as path_helpers
from setzer.helpers.timer import timer
from setzer.workspace.sidebar.document_stats.stats_text import (
    format_whole_document_markup, format_current_file_markup,
    format_chars_lines_markup_current,
    format_selection_markup, format_texcount_missing_markup,
)


def count_chars_lines(text):
    '''纯 Python 字符/行数计数。无外部依赖，对 CJK 友好。

    返回 (chars_with_spaces, chars_no_spaces, lines)：
    - chars_with_spaces: 全部字符数（含空格/制表/换行）
    - chars_no_spaces: 排除所有 unicode 空白后的字符数
    - lines: 逻辑行数（splitlines，正确处理 \\r\\n / \\r / \\n）
    '''
    if not text:
        return 0, 0, 0
    chars = len(text)
    # str.isspace() 覆盖空格/制表/换行/不间断空格等所有 unicode 空白
    chars_no_spaces = sum(1 for c in text if not c.isspace())
    lines = len(text.splitlines())
    return chars, chars_no_spaces, lines


def count_words_simple(text):
    '''简单词数计数：按空白分割。对 CJK 不完美（整段无空格的中文算 1 词），
    但与多数编辑器的选区词数行为一致。CJK 用户主要看字符数。
    '''
    if not text:
        return 0
    return len(text.split())


class DocumentStats(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.document = None
        self.group = None

        self.view = document_stats_section_view.DocumentStatsView()

        self.values = dict()
        self.values[None] = {'save_date': 0, 'counts': None, 'python_counts': None}
        self.values_lock = threading.Lock()
        self.texcount_missing = False
        # 正在跑 texcount 的文件集合：update_data 每 1s 轮询，auto_build 每 2s
        # 保存使 mtime 频繁变化，原实现不跟踪 inflight，可能在前一个 texcount
        # 尚未返回时又起一个新的（texcount 是 Perl 脚本，启动数百 ms，并发多个
        # 拖慢系统）。已在跑的文件跳过，run_query 结束时（含异常）discard。
        self._inflight = set()

        # 签名缓存：update_view 每 2000ms（现 2000ms）被定时器调用一次，
        # 若值未变则跳过 set_markup（Pango 重新解析+重排相当昂贵）。
        self._last_whole_markup = None
        self._last_whole_visible = None
        self._last_current_markup = None
        # char/line 与 selection 的签名缓存，同上。
        self._last_chars_lines_markup = None
        self._last_chars_lines_visible = False
        self._last_selection_markup = None
        self._last_selection_visible = False
        self._last_texcount_missing_visible = False

        # 选区跟踪：active document 切换时断开旧 buffer 信号、连接新 buffer。
        # mark-set 每次光标移动都触发，用 idle 去抖合并为一次实际计数+刷新，
        # 避免拖选过程中每帧都跑 count + set_markup。
        self._selection_document = None
        self._selection_handlers = []
        self._selection_update_idle_id = None

        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)

        # update_view 放宽到 2000ms 仅作兜底；run_query 完成后会通过
        # GLib.idle_add 立即触发一次刷新，所以显示延迟几乎为零。
        # 定时器 id 跟踪：Document Stats section 不可见时（用户切到 Symbols
        # 页）暂停，避免每秒 stat + 可能的 texcount spawn 浪费 CPU/能耗。
        # set_active 由 Sidebar 在 Stack visible-child 变化时调用。
        self._data_timeout_id = GObject.timeout_add(1000, self.update_data)
        self._view_timeout_id = GObject.timeout_add(2000, self.update_view)

    def set_active(self, active):
        '''section 可见时启用定时器，不可见时移除。幂等。'''
        if active:
            if self._data_timeout_id is None:
                self._data_timeout_id = GObject.timeout_add(1000, self.update_data)
            if self._view_timeout_id is None:
                self._view_timeout_id = GObject.timeout_add(2000, self.update_view)
        else:
            if self._data_timeout_id is not None:
                GObject.source_remove(self._data_timeout_id)
                self._data_timeout_id = None
            if self._view_timeout_id is not None:
                GObject.source_remove(self._view_timeout_id)
                self._view_timeout_id = None

    def on_new_active_document(self, workspace, document):
        self.set_document()

    def on_root_state_change(self, workspace, root_state):
        self.set_document()

    def set_document(self):
        document = self.workspace.get_root_or_active_latex_document()
        if self.document != document:
            self.document = document
            self.update_data()
        self.update_view()
        # 选区跟踪跟随 active document（可能是 included 文件，与 root 不同）
        self._set_selection_document(self.workspace.get_active_document())

    def _set_selection_document(self, document):
        '''切换选区跟踪的目标 document：断开旧 buffer 信号，连接新 buffer。

        选区是 buffer 级状态，active document 切换时必须重连信号，否则
        旧 document 的选区变化仍会触发刷新、新 document 的选区变化反而无人响应。
        '''
        if document is self._selection_document:
            return

        # 断开旧 document 的信号 + 取消 pending 的去抖 idle
        for handler_id in self._selection_handlers:
            try:
                document_old = self._selection_document
                if document_old is not None:
                    document_old.source_buffer.disconnect(handler_id)
            except Exception:
                pass
        self._selection_handlers = []
        if self._selection_update_idle_id is not None:
            GObject.source_remove(self._selection_update_idle_id)
            self._selection_update_idle_id = None

        self._selection_document = document

        if document is not None:
            buf = document.source_buffer
            # mark-set: 光标移动 / 选区变化（含拖选）。notify::has-selection:
            # 选区从有到无或反之。两者都触发去抖刷新。
            self._selection_handlers.append(
                buf.connect('mark-set', self._on_selection_maybe_changed))
            self._selection_handlers.append(
                buf.connect('notify::has-selection', self._on_selection_maybe_changed))
            # 切到新文档立即刷新一次，避免残留旧文档的选区统计
            self._schedule_selection_update()

    def _on_selection_maybe_changed(self, *args):
        '''mark-set / notify::has-selection 回调：去抖调度一次选区刷新。'''
        self._schedule_selection_update()

    def _schedule_selection_update(self):
        '''idle 去抖：合并多次 mark-set（拖选过程中每帧都触发）为一次计数。'''
        if self._selection_update_idle_id is not None:
            return
        self._selection_update_idle_id = GObject.idle_add(self._update_selection_idle)

    def _update_selection_idle(self):
        self._selection_update_idle_id = None
        self.update_selection()
        return False

    def update_selection(self):
        '''计算当前选区的词数/字符数并更新 label_selection。

        直接从 buffer 取选区文本（实时，含未保存修改），不读文件。
        无选区时隐藏 label。本方法在主线程 idle 中调用，count 操作对
        典型选区（< 数万字）< 1ms，不会卡 UI。
        '''
        document = self._selection_document
        if document is None:
            self._hide_selection()
            return

        buf = document.source_buffer
        try:
            bounds = buf.get_selection_bounds()
        except Exception:
            bounds = ()
        if not bounds:
            self._hide_selection()
            return

        start, end = bounds
        text = buf.get_text(start, end, True)
        if not text:
            self._hide_selection()
            return

        words = count_words_simple(text)
        chars, chars_no_spaces, _ = count_chars_lines(text)
        markup = format_selection_markup(words, chars, chars_no_spaces)
        if not self._last_selection_visible or markup != self._last_selection_markup:
            self._last_selection_markup = markup
            self._last_selection_visible = True
            self.view.label_selection.set_markup(markup)
            self.view.label_selection.set_visible(True)

    def _hide_selection(self):
        if self._last_selection_visible:
            self._last_selection_visible = False
            self._last_selection_markup = None
            self.view.label_selection.set_visible(False)

    #@timer
    def update_data(self):
        if self.document == None: return True

        filenames = {self.document.get_filename()}
        if self.workspace.get_active_document() != None:
            filenames |= {self.workspace.get_active_document().get_filename()}
        for filename, _ in self.document.parser.symbols['included_latex_files']:
            filenames |= {path_helpers.get_abspath(filename, self.document.get_dirname())}

        for filename in filenames:
            if filename not in self.values:
                self.values[filename] = {'save_date': 0, 'counts': None, 'python_counts': None}

            if filename == None:
                with self.values_lock:
                    self.values[filename]['counts'] = None
                    self.values[filename]['python_counts'] = None

            else:
                try:
                    save_date = os.path.getmtime(filename)
                except FileNotFoundError:
                    pass
                else:
                    if save_date > self.values[filename]['save_date']:
                        self.values[filename]['save_date'] = save_date
                        self.count_words(filename)
                        # 纯 Python 字符/行数计数：读文件 + 计数对典型文档 < 10ms，
                        # 直接在主线程做，避免起线程的开销。mtime 变了才重算。
                        self.count_chars_lines(filename)
        return True

    def count_chars_lines(self, filename):
        '''读文件并计算字符/行数。结果存 self.values[filename]['python_counts']。

        纯 Python 实现，无外部依赖，texcount 缺失时仍可工作。失败（文件
        不可读等）时置 None，视图回退到隐藏该行。
        '''
        try:
            with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            self.values[filename]['python_counts'] = None
            return
        self.values[filename]['python_counts'] = count_chars_lines(text)
        # 立即触发一次 view 刷新，无需等 2000ms 兜底轮询。count_chars_lines
        # 在主线程 update_data 中调用，idle_add 保证在当前 update_data 返回后
        # 才执行 update_view，避免重入。
        GLib.idle_add(self.update_view)

    def count_words(self, filename):
        if filename in self._inflight:
            return
        self._inflight.add(filename)
        threading.Thread(target=self.run_query, args=(['texcount', '-brief', filename], filename), daemon=True).start()
        return False

    #@timer
    def run_query(self, arguments, filename):
        try:
            try:
                popen_kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                if sys.platform == 'win32':
                    popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                process = subprocess.Popen(arguments, **popen_kwargs)
            except FileNotFoundError:
                with self.values_lock:
                    self.texcount_missing = True
                    self.values[filename]['counts'] = None
                GLib.idle_add(self.update_view)
                return

            # 30 秒超时：texcount 处理损坏文件或网络文件系统时可能永久挂起，
            # 超时后 kill 进程并置 counts=None，避免线程永久阻塞占用资源。
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                with self.values_lock:
                    self.values[filename]['counts'] = None
                GLib.idle_add(self.update_view)
                return

            # texcount 输出形如 "123+45+67 ..."（text+headers+outside 以 '+'
            # 分隔）。但 texcount 缺失、文件不可读或版本输出格式变化时，stdout
            # 可能是错误信息（无 '+'），split('+') 后不足 3 段，raw_result[1/2]
            # 抛 IndexError 导致后台线程静默崩溃，update_view 永不被触发、视图
            # 永久停滞在旧值。用 try/except 守卫：解析失败时置 counts=None，
            # 视图回退到 '?' 显示。
            try:
                with self.values_lock:
                    raw_result = process.communicate()[0].decode('utf-8').split('+')
                    count_0 = raw_result[0].split('\n')[-1]
                    count_1 = raw_result[1]
                    count_2 = raw_result[2].split(' ')[0]
                    self.values[filename]['counts'] = [count_0, count_1, count_2]
                    self.texcount_missing = False
            except (IndexError, UnicodeDecodeError):
                with self.values_lock:
                    self.values[filename]['counts'] = None
            # 后台线程拿到新值后立即触发主线程刷新，无需等 2000ms 兜底轮询。
            GLib.idle_add(self.update_view)
        finally:
            self._inflight.discard(filename)

    #@timer
    def update_view(self):
        # texcount 缺失处理：原实现直接 hide_view() 把整个 section 隐藏，
        # 用户不知道为什么统计消失。改为显示 install 提示 + 隐藏 word count
        # 行，但保留 char/line 行（纯 Python，仍可用）。
        with self.values_lock:
            texcount_missing = self.texcount_missing
        if texcount_missing:
            self._show_texcount_missing()
            self._hide_whole_document()
            self._hide_current_file_words()
        else:
            self._hide_texcount_missing()
            self._update_whole_document_words()
            self._update_current_file_words()

        # char/line 计数：不依赖 texcount，texcount 缺失时仍显示。
        self._update_chars_lines()

        return True

    def _show_texcount_missing(self):
        if not self._last_texcount_missing_visible:
            self._last_texcount_missing_visible = True
            self.view.label_texcount_missing.set_markup(format_texcount_missing_markup())
            self.view.label_texcount_missing.set_visible(True)

    def _hide_texcount_missing(self):
        if self._last_texcount_missing_visible:
            self._last_texcount_missing_visible = False
            self.view.label_texcount_missing.set_visible(False)

    def _update_whole_document_words(self):
        if self.document != None and self.document.get_is_root():
            with self.values_lock:
                values = self.values[self.document.get_filename()]['counts']

            if values == None or len(values) != 3:
                values = ['?', '?', '?']

            else:
                values = [int(value) for value in values]
                for filename, _ in self.document.parser.symbols['included_latex_files']:
                    filename = path_helpers.get_abspath(filename, self.document.get_dirname())
                    with self.values_lock:
                        if filename in self.values:
                            values_include = self.values[filename]['counts']
                            if values_include != None and len(values_include) == 3:
                                values[0] += int(values_include[0])
                                values[1] += int(values_include[1])
                                values[2] += int(values_include[2])

            markup = format_whole_document_markup(values[0], values[1], values[2])
            if not self._last_whole_visible or markup != self._last_whole_markup:
                self._last_whole_markup = markup
                self._last_whole_visible = True
                self.view.label_whole_document.set_markup(markup)
                self.view.label_whole_document.set_visible(True)
        else:
            self._hide_whole_document()

    def _hide_whole_document(self):
        if self._last_whole_visible:
            self._last_whole_visible = False
            self._last_whole_markup = None
            self.view.label_whole_document.set_visible(False)

    def _update_current_file_words(self):
        document = self.workspace.get_active_document()
        if document == None:
            self._hide_current_file_words()
            return

        with self.values_lock:
            if document.get_filename() not in self.values:
                values = None
            else:
                values = self.values[document.get_filename()]['counts']

        if values == None or len(values) != 3:
            # texcount 还没返回结果（首次打开/刚保存）时显示 '?'，比隐藏更明确。
            # 本方法仅在 not texcount_missing 时被调用，故无需再判断 texcount 状态。
            values = ['?', '?', '?']

        markup = format_current_file_markup(
            os.path.basename(document.get_displayname()),
            values[0], values[1], values[2])
        if markup != self._last_current_markup:
            self._last_current_markup = markup
            self.view.label_current_file.set_markup(markup)
            self.view.label_current_file.set_visible(True)

    def _hide_current_file_words(self):
        if self._last_current_markup is not None:
            self._last_current_markup = None
            self.view.label_current_file.set_visible(False)

    def _update_chars_lines(self):
        document = self.workspace.get_active_document()
        if document is None:
            self._hide_chars_lines()
            return

        filename = document.get_filename()
        if filename is None:
            self._hide_chars_lines()
            return

        with self.values_lock:
            python_counts = self.values.get(filename, {}).get('python_counts')

        if python_counts is None:
            chars, chars_no_spaces, lines = '?', '?', '?'
        else:
            chars, chars_no_spaces, lines = python_counts

        chars_str = str(chars)
        lines_str = str(lines)
        no_spaces_str = str(chars_no_spaces)

        key = (chars_str, no_spaces_str, lines_str)
        if not self._last_chars_lines_visible or key != self._last_chars_lines_markup:
            self._last_chars_lines_markup = key
            self._last_chars_lines_visible = True
            self.view.label_chars_value.set_text(chars_str)
            self.view.label_chars_desc.set_text(
                _('Characters') + (f'  ({_("no spaces")}: {no_spaces_str})' if chars != '?' else ''))
            self.view.label_lines_value.set_text(lines_str)
            self.view.label_lines_desc.set_text(_('Lines'))
            self.view.stats_box.set_visible(True)

    def _hide_chars_lines(self):
        if self._last_chars_lines_visible:
            self._last_chars_lines_visible = False
            self._last_chars_lines_markup = None
            self.view.stats_box.set_visible(False)

    def set_group(self, group):
        self.group = group

    def hide_view(self):
        self.view.set_visible(False)
        if self.group is not None:
            self.group.set_visible(False)

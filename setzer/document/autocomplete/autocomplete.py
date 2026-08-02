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

import re, os.path

import setzer.document.autocomplete.autocomplete_controller as autocomplete_controller
import setzer.document.autocomplete.autocomplete_widget as autocomplete_widget
from setzer.app.latex_db import LaTeXDB
from setzer.app.service_locator import ServiceLocator


# activate_if_possible 在每次单字符插入时调用（打字热路径），预编译避免
# re.search 每次查 re._cache 的字典开销。
_ACTIVATE_REGEX = re.compile(r'\\[a-zA-Z0-9@]+\Z')

# \begin{...} 上下文：光标在 \begin{ 的花括号内时补全环境名而非 LaTeX 命令。
_BEGIN_REGEX = re.compile(r'\\begin\{([a-zA-Z]*)\Z')

# math mode 检测：光标前奇数个未转义 $ 视为 math mode。
def _is_in_math_mode(text_before_cursor):
    count = 0
    i = 0
    while i < len(text_before_cursor):
        if text_before_cursor[i] == '$':
            if i == 0 or text_before_cursor[i - 1] != '\\':
                count += 1
        i += 1
    return count % 2 == 1

# 仅 preamble 可用、文档体（\begin{document} 之后）应隐藏的命令基础名。
# 用于 update_suggestions 的上下文过滤（报告 #7）。
_PREAMBLE_ONLY = {
    '\\documentclass', '\\usepackage', '\\requirepackage', '\\newcommand',
    '\\renewcommand', '\\providecommand', '\\newenvironment', '\\renewenvironment',
    '\\newtheorem', '\\newlength', '\\newcounter', '\\setlength', '\\newsavebox',
    '\\passoptionstopackage', '\\declaremathoperator',
}


def _cmd_base(command):
    '''提取命令基础名（首个 \\word 片段），用于 preamble 过滤的白名单匹配。'''
    m = re.match(r'(\\[A-Za-z]+)', command)
    return m.group(1).lower() if m is not None else command.lower()


class Autocomplete(object):

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer
        self.adjustment = self.document.view.scrolled_window.get_vadjustment()

        self.is_enabled = self.document.settings.get_value('preferences', 'enable_autocomplete')
        self.is_active = False
        self.current_word_offset = None
        self.current_word = None
        self.context = None
        self.items = []
        self.last_tabbed_item = None
        self.first_item_index = None
        self.selected_item_index = None
        # LaTeXDB 解析错误标志：当 last_parse_error 非空且当前是 \ref/\cite
        # 动态查询时为 True。view 据此在补全列表底部显示"标签数据库不可用"
        # 提示行（UX 报告 #8）。items 为空时也保持激活，使提示行可见。
        self.db_error = False

        # suggestions 缓存键 + idle 去抖。
        # 1) 缓存：update_suggestions 在 is_active 时由 on_document_change +
        #    on_cursor_position_change 两路触发，单次按键跑两遍 LaTeXDB.get_items
        #    （会扫所有打开文档的 labels/bibitems）。若 current_word 与
        #    last_tabbed_item 都未变（如光标在同一补全词内左右移），结果必然相同，
        #    直接复用 items，跳过 LaTeXDB 遍历。
        # 2) 去抖：单次按键产生的 changed + cursor-position 两路合并为一次 idle
        #    调用，避免重复 update_suggestions + queue_draw。
        self._last_suggestions_key = None
        self._update_suggestions_idle_id = None

        self.controller = autocomplete_controller.AutocompleteController(self, document)
        # 解析并下发「手动触发补全」的可配置快捷键（默认 Ctrl+Space，报告 #6/B）。
        keyval, mods = self._parse_trigger_accel()
        self.controller.set_trigger(keyval, mods)
        self.widget = autocomplete_widget.AutocompleteWidget(self)

        self.document.connect('changed', self.on_document_change)
        self.source_buffer.connect('notify::cursor-position', self.on_cursor_position_change)
        self.adjustment.connect('changed', self.on_adjustment_change)
        self.adjustment.connect('value-changed', self.on_adjustment_value_change)
        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.document.settings.connect('settings_changed', self._settings_callback)

    def shutdown(self):
        '''文档关闭时由 Document.shutdown 调用。断开 settings 单例信号连接、
        取消挂起的 idle 回调，防止 settings 持有引用导致文档无法 GC，以及
        idle 回调在文档已销毁后访问 source_buffer。'''
        try:
            self.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass
        if self._update_suggestions_idle_id is not None:
            GLib.source_remove(self._update_suggestions_idle_id)
            self._update_suggestions_idle_id = None

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'enable_autocomplete':
            self.is_enabled = value
            if not self.is_enabled: self.deactivate()
        elif item == 'autocomplete_manual_trigger':
            # 用户改了手动触发键：重新解析并下发给 controller（报告 #6/B）。
            keyval, mods = self._parse_trigger_accel()
            self.controller.set_trigger(keyval, mods)
        elif item in ('autocomplete_previous', 'autocomplete_next',
                      'autocomplete_previous_page', 'autocomplete_next_page',
                      'autocomplete_accept', 'autocomplete_cancel'):
            # 用户改了补全弹窗导航键：刷新 controller 的键位缓存（报告 #6 遗留项）。
            self.controller.refresh_nav_keys()

    def _parse_trigger_accel(self):
        '''把偏好中的 GTK 加速器字符串解析为 (keyval, mods)，无效则为 (0, 0)。'''
        accel = self.document.settings.get_value('preferences', 'autocomplete_manual_trigger')
        if not isinstance(accel, str):
            return 0, 0
        # GTK4 的 accelerator_parse 返回 (success, keyval, mods) 三元组，
        # 这里只取 (keyval, mods) 透传给调用方。
        _success, keyval, mods = Gtk.accelerator_parse(accel)
        return keyval, mods

    def on_document_change(self, document):
        if self.is_active:
            self.deactivate_if_necessary()
            if self.is_active:
                self._schedule_update_suggestions()
        elif self.document.parser.last_edit[0] == 'insert':
            if len(self.document.parser.last_edit[2]) == 1:
                self.activate_if_possible()
        # else: 多字符插入或删除操作，不触发补全

    def on_cursor_position_change(self, buffer, position):
        if self.is_active:
            self.deactivate_if_necessary()
            if self.is_active:
                self._schedule_update_suggestions()

    def _schedule_update_suggestions(self):
        '''单次按键触发的 changed + cursor-position 两路合并为一次 idle 调用。
        在 idle 中跑一次 update_suggestions 而非两次，节省一次 LaTeXDB 查询 +
        一次 queue_draw。同时 idle 让出主线程，使按键事件先返回 GTK 渲染。'''
        if self._update_suggestions_idle_id is None:
            self._update_suggestions_idle_id = GLib.idle_add(self._update_suggestions_idle)

    def _update_suggestions_idle(self):
        self._update_suggestions_idle_id = None
        if self.is_active:
            self.update_suggestions()
        return False

    def on_adjustment_change(self, adjustment):
        # 未激活时 widget 已隐藏，滚动无需更新位置/大小/内容。
        # 原实现无条件 queue_draw，每次滚动都重算字体度量并清空重建 row。
        if not self.is_active: return
        self.widget.queue_draw()

    def on_adjustment_value_change(self, adjustment):
        if not self.is_active: return
        self.widget.queue_draw()

    def activate_if_possible(self):
        # No activation if autocomplete is disabled.
        if not self.is_enabled: return

        # Triggered on tab, if ac is inactive,
        # also when text is inserted, if it is a single character.

        # Tries to match a backslash followed by letters from the
        # last backslash before the cursor to the cursor.
        # Then updates items from that match. If there are not at
        # least 2 matching commands, the activation is reversed.
        # So it should not return with an activation if there is
        # nothing to complete.

        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        line_before_cursor = self.document.get_line(insert_iter.get_line())[:insert_iter.get_line_offset()]

        # \begin{...} 上下文优先：光标在 \begin{ 花括号内时补全环境名（报告 #7）。
        begin_match = _BEGIN_REGEX.search(line_before_cursor)
        if begin_match:
            self.context = 'begin'
            self.current_word_offset = insert_iter.get_offset() - len(line_before_cursor) + begin_match.start()
            self.is_active = True
            self.update_suggestions()
            self.widget.queue_draw()
            return

        self.context = None
        matching_result = _ACTIVATE_REGEX.search(line_before_cursor)
        if matching_result:
            self.current_word_offset = insert_iter.get_offset() - len(line_before_cursor) + matching_result.start()
            self.is_active = True
            self.update_suggestions()
        # math mode 内输入单个字母：以该字母为前缀补全 onlymath 命令（希腊字母等）。
        elif _is_in_math_mode(line_before_cursor) and len(line_before_cursor) > 0 and line_before_cursor[-1].isalpha():
            self.context = 'math'
            self.current_word_offset = insert_iter.get_offset() - 1
            self.is_active = True
            self.update_suggestions()
        self.widget.queue_draw()

    def deactivate_if_necessary(self):
        # Deactivates autocomplete if certain invariants don't hold
        # The cursor must be on the same line as the starting point
        # and it must come after it on that line.

        start_iter = self.source_buffer.get_iter_at_offset(self.current_word_offset)
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        if start_iter.get_line() != insert_iter.get_line() or start_iter.get_offset() >= insert_iter.get_offset():
            self.deactivate()

    def deactivate(self):
        self.is_active = False

        self.current_word_offset = None
        self.current_word = None
        self.context = None
        self.context = None
        self.items = []
        self.last_tabbed_item = None
        self.first_item_index = None
        self.selected_item_index = None
        self.db_error = False
        # 清空缓存键与挂起的 idle 回调，避免 deactivate 后 idle 仍跑
        # update_suggestions（is_active=False 时虽会早退，但残留 id 会阻止
        # 下次激活期间新的 idle 调度，导致补全列表不刷新）。
        self._last_suggestions_key = None
        if self._update_suggestions_idle_id is not None:
            GLib.Source.remove(self._update_suggestions_idle_id)
            self._update_suggestions_idle_id = None
        self.widget.queue_draw()

    def update_suggestions(self):
        # Placeholders are not considered as such, so matching is literal.

        if not self.is_active: return

        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        line_before_cursor = self.document.get_line(insert_iter.get_line())[:insert_iter.get_line_offset()]
        line_offset = self.source_buffer.get_iter_at_line(insert_iter.get_line())[1].get_offset()

        self.current_word = line_before_cursor[self.current_word_offset - line_offset:]

        # 刷新 db_error 标志：LaTeXDB.last_parse_error 是外部状态，可能在
        # current_word 未变时改变（如构建完成后 idle 刷新触发 parse）。每次
        # update_suggestions 都重新求值，不依赖 items 缓存。
        # 仅对 \ref/\cite 动态查询设标志——静态命令补全（\section 等）来自
        # XML，不受 parse 错误影响，不需要提示。
        self.db_error = (LaTeXDB.last_parse_error is not None and
                         LaTeXDB.is_dynamic_query(self.current_word))

        # 缓存命中：current_word、last_tabbed_item、context 都未变时，
        # LaTeXDB 查询结果必然相同，跳过遍历。context 纳入键——\begin{} 与
        # 普通命令的补全数据源不同，必须区分。
        cache_key = (self.current_word, self.last_tabbed_item, self.context)
        if cache_key != self._last_suggestions_key:
            self._last_suggestions_key = cache_key
            if self.context == 'begin':
                self.items = LaTeXDB.get_environment_items(self.current_word)
            else:
                self.items = LaTeXDB.get_items(self.current_word, self.last_tabbed_item, onlymath=(self.context == 'math'))
            # 文档体（\begin{document} 之后）隐藏仅 preamble 命令（报告 #7）。
            if self.context != 'begin':
                preamble_end = self._get_preamble_end()
                if preamble_end is not None and insert_iter.get_offset() > preamble_end:
                    self.items = [it for it in self.items
                                  if _cmd_base(it['command']) not in _PREAMBLE_ONLY]
            # items 集合可能已变，重置选中项到首项（与原行为一致）。
            if len(self.items) > 0:
                self.first_item_index = 0
                self.selected_item_index = 0
            elif self.db_error:
                # \ref/\cite 查询无结果且 LaTeXDB 解析失败：保持激活，
                # 让 view 显示"标签数据库不可用"提示行。设索引为 0 使
                # populate() 不因 None 检查提前返回（items 为空时不会
                # 渲染任何命令行，仅渲染错误提示行）。
                self.first_item_index = 0
                self.selected_item_index = 0
            else:
                self.deactivate()
                return
        self.widget.queue_draw()

    def _get_preamble_end(self):
        '''返回 preamble 区块结束偏移（\begin{document} 之前）；无则返回 None。'''
        parser = getattr(self.document, 'parser', None)
        if parser is None:
            return None
        symbols = getattr(parser, 'symbols', None)
        if not symbols:
            return None
        blocks = symbols.get('blocks')
        if not blocks:
            return None
        for block in blocks:
            if len(block) >= 2 and block[-1] == 'preamble':
                return block[1]
        return None

    def select_next(self):
        if len(self.items) == 0: return
        self.selected_item_index = (self.selected_item_index + 1) % len(self.items)
        self.update_first_item_index()
        self.widget.queue_draw()

    def select_previous(self):
        if len(self.items) == 0: return
        self.selected_item_index = (self.selected_item_index - 1) % len(self.items)
        self.update_first_item_index()
        self.widget.queue_draw()

    def update_first_item_index(self):
        if self.selected_item_index < self.first_item_index:
            self.first_item_index = self.selected_item_index
        elif self.selected_item_index >= self.first_item_index + 5:
            self.first_item_index = self.selected_item_index - 4

    def page_down(self):
        if len(self.items) == 0: return
        s_index = self.selected_item_index
        f_index = self.first_item_index
        page_size = min(len(self.items), 5)
        length = len(self.items)

        if s_index < length - page_size:
            self.selected_item_index += page_size
        else:
            self.selected_item_index = length - 1

        if f_index < length - 2 * page_size + 1:
            self.first_item_index += page_size
        elif f_index < length - page_size:
            self.first_item_index = length - page_size
        self.widget.queue_draw()

    def page_up(self):
        if len(self.items) == 0: return
        s_index = self.selected_item_index
        f_index = self.first_item_index
        page_size = min(len(self.items), 5)
        length = len(self.items)

        if s_index >= page_size:
            self.selected_item_index -= page_size
        else:
            self.selected_item_index = 0

        if f_index >= page_size:
            self.first_item_index -= page_size
        else:
            self.first_item_index = 0
        self.widget.queue_draw()

    def tab(self):
        # If the selected item matches the beginning of the end of the
        # current line in the buffer in full, just like on submit,
        # the cursor is moved to the end of the match.
        # Otherwise we only consider the longest common prefix of the
        # items that adds at least one character. For example if we
        # have items "abc" and "abd" and the cursor is after "a", we
        # consider "ab". If the cursor is after "b", we consider "abc".
        # Now we move the cursor if the prefix matches the buffer exactly
        # (including placeholders). Otherwise we add the prefix to the
        # buffer.

        if self.items == None or len(self.items) == 0: return
        if self.selected_item_index == None: return

        result = self.match_current_command_with_buffer()
        if result != None:
            start, end = result
            self.move_cursor_to_offset(end)
            self.deactivate()
        else:
            command = self.items[self.selected_item_index]['command']
            matching_prefix = command[:len(self.current_word) + 1]
            matching_items = [item for item in self.items if item['command'].startswith(matching_prefix)]
            lcp = os.path.commonprefix([item['command'] for item in matching_items])
            matching_result = re.match(re.escape(lcp), self.document.get_line_after_offset(self.current_word_offset))
            if matching_result:
                self.last_tabbed_item = self.items[self.selected_item_index]['command']
                self.move_cursor_to_offset(self.current_word_offset + len(lcp))
            else:
                self.last_tabbed_item = self.items[self.selected_item_index]['command']
                if lcp == command and command.startswith('\\begin{'):
                    bracket_pos = command.find('}') + 1
                    end_name = command[7:bracket_pos]
                    if not self._replace_begin_keep_auto_end(end_name):
                        command += '\n\t•\n\\end{' + end_name + '}'
                        self.replace_current_word_in_buffer(command, select_dot_and_scroll=True)
                    self.deactivate()
                else:
                    self.replace_current_word_in_buffer(lcp, select_dot_and_scroll=False)
                    if lcp == command:
                        self.deactivate()

    def submit(self):
        # If the selected item matches with the beginning of the end
        # of the current line in the buffer in full, move the cursor
        # to the end of the match.
        # Placeholder match any sequence of characters.
        # Otherwise we add the command to the buffer.

        if self.items == None or len(self.items) == 0: return
        if self.selected_item_index == None: return

        result = self.match_current_command_with_buffer()
        if result != None:
            start, end = result
            self.move_cursor_to_offset(end)
        else:
            command = self.items[self.selected_item_index]['command']
            if command.startswith('\\begin{'):
                bracket_pos = command.find('}') + 1
                end_name = command[7:bracket_pos]
                if self._replace_begin_keep_auto_end(end_name):
                    self.deactivate()
                    return
                command += '\n\t•\n\\end{' + end_name + '}'
                self.replace_current_word_in_buffer(command, select_dot_and_scroll=True)
            else:
                self.replace_current_word_in_buffer(command, select_dot_and_scroll=True)

        self.deactivate()

    def _replace_begin_keep_auto_end(self, end_name):
        r'''若光标后已存在环境自动补插入的配对 \end{}（含占位符 •），
        仅补全 \begin{name} 部分并保留已有 \end{}，避免重复插入。'''
        source_buffer = self.source_buffer
        begin_start_iter = source_buffer.get_iter_at_offset(self.current_word_offset)
        if begin_start_iter is None:
            return False
        insert_iter = source_buffer.get_iter_at_mark(source_buffer.get_insert())

        text_after = source_buffer.get_text(insert_iter, source_buffer.get_end_iter(), False)
        match = re.search(r'^\}?\n\t•\n\\end\{', text_after)
        if match is None:
            return False

        close_iter = insert_iter.copy()
        if match.group(0).startswith('}'):
            close_iter.forward_char()
        dot_offset = insert_iter.get_offset() + match.start() + len('\n\t')
        dot_mark = source_buffer.create_mark(None, source_buffer.get_iter_at_offset(dot_offset), True)

        source_buffer.begin_user_action()
        try:
            source_buffer.delete(begin_start_iter, close_iter)
            source_buffer.insert(begin_start_iter, '\\begin{' + end_name + '}')
            # begin_start_iter 现位于尾串起始（\begin{name} 之后），把已插入的 \end{} 环境名同步为 name
            found, end_bs_iter, _ = begin_start_iter.forward_search('\\end{', Gtk.TextSearchFlags(0), None)
            if found:
                name_start = end_bs_iter.copy()
                name_start.forward_chars(5)
                name_end = name_start.copy()
                while not name_end.get_char() == '}' and not name_end.is_end():
                    name_end.forward_char()
                source_buffer.delete(name_start, name_end)
                source_buffer.insert(name_start, end_name)
        finally:
            source_buffer.end_user_action()

        dot_iter = source_buffer.get_iter_at_mark(dot_mark)
        source_buffer.delete_mark(dot_mark)
        source_buffer.place_cursor(dot_iter)
        self.document.select_first_dot_around_cursor(1, 0)
        self.document.scroll_cursor_onscreen()
        return True

    def match_current_command_with_buffer(self):
        command = self.items[self.selected_item_index]['command']
        regex = re.escape(command)
        regex = regex.replace('•', r'\{(?:[^\{\}\(\)\[\]])*\}')
        matching_result = re.match(regex, self.document.get_line_after_offset(self.current_word_offset))
        if matching_result:
            return (self.current_word_offset, self.current_word_offset + matching_result.end())
        else:
            return None

    def move_cursor_to_offset(self, offset):
        new_cursor_iter = self.source_buffer.get_iter_at_offset(offset)
        self.source_buffer.place_cursor(new_cursor_iter)
        self.document.scroll_cursor_onscreen()

    def replace_current_word_in_buffer(self, text, select_dot_and_scroll):
        start_iter = self.source_buffer.get_iter_at_offset(self.current_word_offset)
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())

        text = text[len(self.current_word):]
        text = self.document.replace_tabs_with_spaces_if_set(text)
        text = self.document.indent_text_with_whitespace_at_iter(text, start_iter)

        # try/finally 确保 end_user_action 总被调用：若 insert_at_cursor 抛异常
        # （如 buffer 被外部修改或未来引入只读模式），不调用 end_user_action 会让
        # 后续所有编辑被合并进同一个 undo 单元，破坏撤销粒度。
        self.source_buffer.begin_user_action()
        try:
            self.source_buffer.insert_at_cursor(text)
        finally:
            self.source_buffer.end_user_action()

        if select_dot_and_scroll:
            self.document.select_first_dot_around_cursor(offset_before=len(text), offset_after=0)
            self.document.scroll_cursor_onscreen()



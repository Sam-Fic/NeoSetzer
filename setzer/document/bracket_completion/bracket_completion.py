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
from gi.repository import Gtk, Gdk, GLib

from setzer.app.service_locator import ServiceLocator


# on_keypress 每次按键都跑，原实现每次都调 Gdk.keyval_from_name 做字符串→
# keyval 查表（C 函数 + 内部哈希）。模块级预计算为整数常量后，热路径只做
# 整数 == 比较。
_KEYVAL_BACKSLASH = Gdk.keyval_from_name('backslash')
_KEYVAL_BRACKETLEFT = Gdk.keyval_from_name('bracketleft')
_KEYVAL_BRACELEFT = Gdk.keyval_from_name('braceleft')
_KEYVAL_PARENLEFT = Gdk.keyval_from_name('parenleft')
_KEYVAL_DOLLAR = Gdk.keyval_from_name('dollar')
_KEYVAL_BRACKETRIGHT = Gdk.keyval_from_name('bracketright')
_KEYVAL_BRACERIGHT = Gdk.keyval_from_name('braceright')
_KEYVAL_PARENRIGHT = Gdk.keyval_from_name('parenright')


class BracketCompletion(object):

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer

        self.autoclose_enabled = self.document.settings.get_value('preferences', 'enable_bracket_completion')
        self.environment_autocomplete_enabled = self.document.settings.get_value('preferences', 'enable_environment_autocomplete')
        self.bracket_selection_enabled = self.document.settings.get_value('preferences', 'bracket_selection')

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

        self.completion_marks = list()
        # idle 去抖：on_buffer_changed 和 on_cursor_position_changed 单次按键
        # 两路都触发，去抖后只跑一次 reconsider_completion_marks。
        self._reconsider_idle_id = None
        self.document.connect('cursor_position_changed', self.on_cursor_position_changed)
        self.document.connect('changed', self.on_buffer_changed)
        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.document.settings.connect('settings_changed', self._settings_callback)

    def shutdown(self):
        '''文档关闭时由 Document.shutdown 调用。断开 settings 单例信号连接、
        取消挂起的 idle 回调，防止 settings 持有引用导致文档无法 GC。'''
        try:
            self.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass
        if self._reconsider_idle_id is not None:
            GLib.source_remove(self._reconsider_idle_id)
            self._reconsider_idle_id = None

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'enable_bracket_completion':
            self.autoclose_enabled = value
            if not self.autoclose_enabled:
                self.reconsider_completion_marks()
        if item == 'enable_environment_autocomplete':
            self.environment_autocomplete_enabled = value
            if not value:
                self.reconsider_completion_marks()

        if item == 'bracket_selection':
            self.bracket_selection_enabled = value

    def on_keypress(self, controller, keyval, keycode, state):
        # 环境自动补：输入 \begin{ 时自动补出配对 \end{}（即使补全弹窗已激活）。
        if keyval == _KEYVAL_BRACELEFT and self.environment_autocomplete_enabled \
                and self.document.get_chars_at_cursor(-6) == '\\begin':
            self.complete_environment_on_open()
            return True
        if self.document.autocomplete.is_active: return False

        modifiers = Gtk.accelerator_get_default_mod_mask()

        if self.source_buffer.get_has_selection():
            if keyval == _KEYVAL_BACKSLASH:
                return self.bracket_selection('\\')
            if keyval == _KEYVAL_BRACKETLEFT:
                return self.bracket_selection('[')
            if keyval == _KEYVAL_BRACELEFT:
                return self.bracket_selection('{')
            if keyval == _KEYVAL_PARENLEFT:
                return self.bracket_selection('(')
            if keyval == _KEYVAL_DOLLAR:
                return self.bracket_selection('$')
        else:
            # $ 无独立开/闭键，需先判定“光标后已存在自动补全的 $”再决定跳过还是开启
            if keyval == _KEYVAL_DOLLAR:
                if self.handle_autoclosing_bracket_overwrite('$'):
                    return True
                return self.autoclose_brackets('$')

            if keyval == _KEYVAL_BRACKETLEFT:
                return self.autoclose_brackets('[')
            if keyval == _KEYVAL_BRACELEFT:
                return self.autoclose_brackets('{')
            if keyval == _KEYVAL_PARENLEFT:
                return self.autoclose_brackets('(')

            if keyval == _KEYVAL_BRACKETRIGHT:
                return self.handle_autoclosing_bracket_overwrite(']')
            if keyval == _KEYVAL_BRACERIGHT:
                return self.handle_autoclosing_bracket_overwrite('}')
            if keyval == _KEYVAL_PARENRIGHT:
                return self.handle_autoclosing_bracket_overwrite(')')
            if keyval == _KEYVAL_BACKSLASH:
                return self.handle_autoclosing_bracket_overwrite('\\')

        return False

    def on_cursor_position_changed(self, document):
        self._schedule_reconsider()

    def on_buffer_changed(self, document):
        self._schedule_reconsider()

    def _schedule_reconsider(self):
        '''单次按键触发 on_buffer_changed + on_cursor_position_changed 两路，
        去抖后只跑一次 reconsider_completion_marks。'''
        if self._reconsider_idle_id is None:
            self._reconsider_idle_id = GLib.idle_add(self._reconsider_idle)

    def _reconsider_idle(self):
        self._reconsider_idle_id = None
        self.reconsider_completion_marks()
        return False

    def bracket_selection(self, char):
        if not self.bracket_selection_enabled: return False

        # if backslash or opening brackets are typed, don't replace selected text
        # but put a backslash or brackets around it.

        bounds = self.source_buffer.get_selection_bounds()
        closing_char = {'[': ']', '{': '}', '(': ')', '$': '$', '\\': ''}[char]
        if self.document.get_chars_at_iter(bounds[0], -1) == '\\' and char in ['[', '{', '(']:
            closing_char = '\\' + closing_char
        offset_start = bounds[0].get_offset()
        text = char + self.document.get_selected_text() + closing_char

        self.source_buffer.begin_user_action()
        try:
            self.source_buffer.delete_selection(True, True)
            self.source_buffer.insert_at_cursor(text)
            start_iter = self.source_buffer.get_iter_at_offset(offset_start + 1)
            end_iter = self.source_buffer.get_iter_at_offset(offset_start + len(text) - len(closing_char))
            self.source_buffer.select_range(start_iter, end_iter)
        finally:
            # try/finally 保证 end_user_action 总被调用：若 delete_selection 或
            # insert_at_cursor 抛异常（buffer 被外部修改等），不保护的化后续所有
            # 编辑会被合并进这个未关闭的 user-action，破坏 undo 粒度。
            self.source_buffer.end_user_action()
        return True

    def autoclose_brackets(self, char):
        if not self.autoclose_enabled: return False

        closing_char = {'[': ']', '{': '}', '(': ')', '$': '$'}[char]
        if self.document.get_chars_at_cursor(-1) == '\\':
            closing_char = '\\' + closing_char

        self.source_buffer.begin_user_action()
        try:
            self.source_buffer.insert_at_cursor(char + closing_char)
        finally:
            self.source_buffer.end_user_action()

        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        insert_iter.backward_chars(len(closing_char))
        self.source_buffer.place_cursor(insert_iter)

        self.add_completion_marks(insert_iter, len(closing_char), len(closing_char))

        return True

    def complete_environment_on_open(self):
        r'''输入 \begin{ 时自动补出配对的 \begin{} 与 \end{}（含内容占位符 •）。'''
        source_buffer = self.source_buffer
        source_buffer.begin_user_action()
        try:
            source_buffer.insert_at_cursor('{' + '}' + '\n\t•\n\\end{}')
        finally:
            source_buffer.end_user_action()
        # 光标退回 \begin{} 的花括号内，便于继续输入环境名
        insert_iter = source_buffer.get_iter_at_mark(source_buffer.get_insert())
        insert_iter.backward_chars(len('\n\t•\n\\end{}') + 1)
        source_buffer.place_cursor(insert_iter)
        self.add_completion_marks(insert_iter, 1, 1)
        self.reconsider_completion_marks()

    def add_completion_marks(self, insert_iter, len_before, len_after):
        # marks are added to the text buffer, to signal that a completion took place
        # these are remove whenever the cursor moves outside the bracketed area

        start_iter = insert_iter.copy()
        start_iter.backward_chars(len_before)
        start_mark = self.source_buffer.create_mark('brackets_autoclose_start_' + str(ServiceLocator.get_increment('brackets_autoclose_start')), start_iter, True)

        end_iter = insert_iter.copy()
        end_iter.forward_chars(len_after)
        end_mark = self.source_buffer.create_mark('brackets_autoclose_end_' + str(ServiceLocator.get_increment('brackets_autoclose_end')), end_iter, True)

        self.completion_marks.append([start_mark, end_mark])

    def reconsider_completion_marks(self):
        # remove completion marks when the cursor is outside the bracketed area.

        completion_marks = list()
        # insert 位置在整个循环中不变，提到循环外只取一次。原实现每个 mark
        # 各调一次 get_iter_at_mark(get_insert())，N 个 mark 做 N 次 mark 查找 +
        # iter 创建。completion_marks 通常只有 0-1 项，但取值开销本身可消除。
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        insert_offset = insert_iter.get_offset()

        for marks in self.completion_marks:
            start_mark, end_mark = marks
            start_iter = self.source_buffer.get_iter_at_mark(start_mark)
            end_iter = self.source_buffer.get_iter_at_mark(end_mark)

            if (self.autoclose_enabled or self.environment_autocomplete_enabled) and start_iter.get_offset() < insert_offset and end_iter.get_offset() > insert_offset:
                completion_marks.append([start_mark, end_mark])
            else:
                self.source_buffer.delete_mark(start_mark)
                self.source_buffer.delete_mark(end_mark)

        self.completion_marks = completion_marks

    def handle_autoclosing_bracket_overwrite(self, char):
        # if there's a mark indicating that a completion took place, closing brackets
        # are just skipped over when they are typed and a similar bracket is already present
        # at the cursor.

        if not self.document.get_chars_at_cursor(1) == char: return False

        # 直接遍历 self.completion_marks（通常 0-1 项）而非 insert_iter.get_marks()，
        # 后者会返回该位置上所有 mark（含 GTK 内置的 insert / selection_bound），
        # 在每次按键时徒增一次完整 mark 集合迭代开销。
        insert_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        # char 为 ]/}) 时跳 1 个字符（单个闭合括号）；char 为 '\\' 时跳 2 个，
        # 因为 autoclose_brackets 在前一个字符是 '\' 时会把闭合括号扩展为 LaTeX
        # 命令形式（\}、\]、\)），共 2 个字符。跳 2 才能越过整个 LaTeX 闭合对，
        # 与 completion_marks 里 end_mark 的位置（add_completion_marks 在闭合串
        # 末尾设的 mark）对齐。
        target_iter = insert_iter.copy()
        target_iter.forward_chars(2 if char == '\\' else 1)
        target_offset = target_iter.get_offset()

        for start_mark, end_mark in self.completion_marks:
            end_iter = self.source_buffer.get_iter_at_mark(end_mark)
            if end_iter.get_offset() == target_offset:
                start_iter = self.source_buffer.get_iter_at_mark(start_mark)
                self.source_buffer.begin_user_action()
                try:
                    if char == '\\':
                        # '\\' 不是闭合括号而是 LaTeX 命令前缀。用户在已自动补全
                        # 的 '\}' 处再按 '\' 时，不重复插入反斜杠（否则变成 '\\}'
                        # 即换行命令），而是回退 1 字符把光标放在 '\' 与 '}' 之间
                        # （\|}），复用已有的反斜杠让用户接着输入命令名，例如
                        # '\textbf}'。对 ]/}) 则直接跳过到闭合括号之后。
                        target_iter.backward_chars(1)
                        self.source_buffer.place_cursor(target_iter)
                    else:
                        self.source_buffer.place_cursor(target_iter)
                        # 环境自动补：配对的 \begin{} 后含 \end{} 内容占位符，跳过右括号后跳到占位符
                        if self.environment_autocomplete_enabled:
                            rest = self.source_buffer.get_text(target_iter, self.source_buffer.get_end_iter(), False)
                            if rest.startswith('\n\t•\n\\end{'):
                                # 读取 \begin{} 环境名并同步到已插入的 \end{}
                                begin_name = self.source_buffer.get_text(start_iter.copy().forward_char(), end_iter, False)
                                self._update_env_end_name(target_iter, begin_name)
                                dot_iter = target_iter.copy()
                                dot_iter.forward_chars(2)
                                self.source_buffer.place_cursor(dot_iter)
                                self.document.select_first_dot_around_cursor(1, 0)
                                self.document.autocomplete.deactivate()
                finally:
                    self.source_buffer.end_user_action()
                self.reconsider_completion_marks()
                return True

        return False

    def _update_env_end_name(self, after_begin_iter, begin_name):
        r'''把已自动补的 \end{} 环境名同步为 begin 的环境名。'''
        source_buffer = self.source_buffer
        found, end_bs_iter, _ = after_begin_iter.forward_search('\\end{', Gtk.TextSearchFlags(0), None)
        if not found:
            return
        name_start = end_bs_iter.copy()
        name_start.forward_chars(5)
        name_end = name_start.copy()
        while not name_end.get_char() == '}' and not name_end.is_end():
            name_end.forward_char()
        source_buffer.begin_user_action()
        try:
            source_buffer.delete(name_start, name_end)
            source_buffer.insert(name_start, begin_name)
        finally:
            source_buffer.end_user_action()



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
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib

import re, os.path

import setzer.document.autocomplete.autocomplete_controller as autocomplete_controller
import setzer.document.autocomplete.autocomplete_widget as autocomplete_widget
from setzer.app.latex_db import LaTeXDB
from setzer.app.service_locator import ServiceLocator


# activate_if_possible 在每次单字符插入时调用（打字热路径），预编译避免
# re.search 每次查 re._cache 的字典开销。
_ACTIVATE_REGEX = re.compile(r'\\[a-zA-Z]+\Z')


class Autocomplete(object):

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer
        self.adjustment = self.document.view.scrolled_window.get_vadjustment()

        self.is_enabled = self.document.settings.get_value('preferences', 'enable_autocomplete')
        self.is_active = False
        self.current_word_offset = None
        self.current_word = None
        self.items = []
        self.last_tabbed_item = None
        self.first_item_index = None
        self.selected_item_index = None

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

    def on_document_change(self, document):
        if self.is_active:
            self.deactivate_if_necessary()
            if self.is_active:
                self._schedule_update_suggestions()
        elif self.document.parser.last_edit[0] == 'insert':
            if len(self.document.parser.last_edit[2]) == 1:
                self.activate_if_possible()

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
        matching_result = _ACTIVATE_REGEX.search(line_before_cursor)
        if matching_result:
            self.current_word_offset = insert_iter.get_offset() - len(line_before_cursor) + matching_result.start()
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
        self.items = []
        self.last_tabbed_item = None
        self.first_item_index = None
        self.selected_item_index = None
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

        # 缓存命中：current_word 与 last_tabbed_item 都未变时（例如 queue_draw
        # 链路重复触发、或 idle 与直接调用同帧到达），LaTeXDB.get_items 结果
        # 必然相同，跳过遍历。current_word 是从 cursor 位置派生的，光标在词内
        # 任何移动都会改变它，所以命中场景主要是「同帧重复调用」——这正是
        # idle 去抖之外仍可能出现的情况（如 tab() 后再 update_suggestions）。
        cache_key = (self.current_word, self.last_tabbed_item)
        if cache_key != self._last_suggestions_key:
            self._last_suggestions_key = cache_key
            self.items = LaTeXDB.get_items(self.current_word, self.last_tabbed_item)
            # items 集合可能已变，重置选中项到首项（与原行为一致）。
            if len(self.items) > 0:
                self.first_item_index = 0
                self.selected_item_index = 0
            else:
                self.deactivate()
                return
        self.widget.queue_draw()

    def select_next(self):
        self.selected_item_index = (self.selected_item_index + 1) % len(self.items)
        self.update_first_item_index()
        self.widget.queue_draw()

    def select_previous(self):
        self.selected_item_index = (self.selected_item_index - 1) % len(self.items)
        self.update_first_item_index()
        self.widget.queue_draw()

    def update_first_item_index(self):
        if self.selected_item_index < self.first_item_index:
            self.first_item_index = self.selected_item_index
        elif self.selected_item_index >= self.first_item_index + 5:
            self.first_item_index = self.selected_item_index - 4

    def page_down(self):
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
                    command += '\n\t•\n\\end{' + command[7:bracket_pos]
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
                command += '\n\t•\n\\end{' + command[7:bracket_pos]
                self.replace_current_word_in_buffer(command, select_dot_and_scroll=True)
            else:
                self.replace_current_word_in_buffer(command, select_dot_and_scroll=True)

        self.deactivate()

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

        self.source_buffer.begin_user_action()
        self.source_buffer.insert_at_cursor(text)
        self.source_buffer.end_user_action()

        if select_dot_and_scroll:
            self.document.select_first_dot_around_cursor(offset_before=len(text), offset_after=0)
            self.document.scroll_cursor_onscreen()



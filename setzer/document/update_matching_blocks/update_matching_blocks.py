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
from gi.repository import Gtk, Gdk

import re

from setzer.app.service_locator import ServiceLocator
from setzer.document.update_matching_blocks.begin_end_match import (
    find_cursor_in_begin_end,
)


# on_keypress 是打字热路径，每次按键都跑。原实现每帧调
# ServiceLocator.get_regex_object('[a-zA-Z]\\Z') 做一次 dict 查表，
# 模块级预编译后直接 .match，省去哈希查找。
_LETTER_REGEX = re.compile(r'[a-zA-Z]\Z')

# 原 _BEGIN_END_REGEX 用 %•% 标记注入 line 文本标记光标位置，存在
# 标记注入风险：用户文本若含 %•%（LaTeX 注释中可能出现），会错误匹配。
# 改为调用 find_cursor_in_begin_end 结构化定位（按 offset 切分 line），
# 不再修改字符串内容。详见 begin_end_match.py。

# Gdk.keyval_from_name 是 C 函数 + 字符串查表，每次按键调用 3-6 次。
# 模块级预计算为整数常量后，热路径只做整数比较。
_KEYVAL_ASTERISK = Gdk.keyval_from_name('asterisk')
_KEYVAL_BACKSPACE = Gdk.keyval_from_name('BackSpace')
_KEYVAL_DELETE = Gdk.keyval_from_name('Delete')


class UpdateMatchingBlocks(object):

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer

        self.is_enabled = self.document.settings.get_value('preferences', 'update_matching_blocks')

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

        self.document.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'update_matching_blocks':
            self.is_enabled = value

    def on_keypress(self, controller, keyval, keycode, state):
        if self.document.autocomplete.is_active: return False
        if not self.is_enabled: return False

        modifiers = Gtk.accelerator_get_default_mod_mask()

        if _LETTER_REGEX.match(Gdk.keyval_name(keyval)) or keyval == _KEYVAL_ASTERISK or keyval == _KEYVAL_BACKSPACE or keyval == _KEYVAL_DELETE:
            if state & modifiers == 0:
                if not self.document.autocomplete.is_active:
                    if self.handle_keypress_inside_begin_or_end(keyval):
                        return True

        return False

    def handle_keypress_inside_begin_or_end(self, keyval):
        buffer = self.source_buffer
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        line = self.document.get_line(insert_iter.get_line())
        line_offset = insert_iter.get_line_offset()
        cursor_offset = insert_iter.get_offset()

        # 结构化定位替代 %•% 标记注入：不修改 line 文本，按 offset 切分
        # 并查找包含光标的 \begin{...}/\end{...} 区域。避免用户文本含 %•%
        # 时误匹配（虽然概率低，但标记注入模式本身脆弱）。
        #
        # find_cursor_in_begin_end 返回 (begin_or_end, before_cursor,
        # after_cursor, backslash_offset_in_line)。before/after_cursor
        # 对应原正则的 group(2)/group(3)；backslash_offset_in_line 是
        # \begin/\end 在行内的起点（等价于原 start(1) - 1）。
        match = find_cursor_in_begin_end(line, line_offset)
        if match is None:
            return False
        begin_or_end, before_cursor, after_cursor, backslash_offset_in_line = match

        if keyval == _KEYVAL_BACKSPACE and len(before_cursor) == 0: return False
        if keyval == _KEYVAL_DELETE and len(after_cursor) == 0: return False

        # 计算 \begin/\end 在 buffer 中的绝对偏移。
        # cursor_offset - line_offset = 当前行的起始偏移。
        # backslash_offset_in_line 是 \begin/\end 在行内的起点（\ 的位置）。
        # 两者相加即 \begin/\end 在 buffer 中的绝对偏移。
        #
        # 原代码用 match.begin_end.start()，但 re.match() 的整体匹配始终从
        # 位置 0 开始（正则开头的 .* 会吞掉 \begin 前的所有内容），所以
        # start() 恒为 0，orig_offset 恒等于行首偏移。当 \begin/\end 前有
        # 缩进空格或其他文本时，block[0]/block[1]（记录的是 \begin/\end 的
        # 实际偏移）不等于行首偏移，下面的 for 循环找不到匹配 block，功能
        # 静默失效。改用 backslash_offset_in_line 后，无论前面是否有空白
        # 都能正确定位。
        orig_offset = cursor_offset - line_offset + backslash_offset_in_line
        target_offset = None
        for block in self.document.parser.symbols['blocks']:
            if block[0] == orig_offset:
                if block[1] == None:
                    return False
                else:
                    target_offset = block[1] + 5 + len(before_cursor)
                    break
            elif block[1] == orig_offset:
                if block[0] == None:
                    return False
                else:
                    target_offset = block[0] + 7 + len(before_cursor)
                    break
        if target_offset == None: return False

        buffer.begin_user_action()
        if keyval == _KEYVAL_ASTERISK:
            if cursor_offset < target_offset: target_offset += 1
            buffer.insert_at_cursor('*')
            buffer.insert(buffer.get_iter_at_offset(target_offset), '*')
        elif keyval == _KEYVAL_BACKSPACE:
            if cursor_offset < target_offset: target_offset -= 1
            buffer.delete(buffer.get_iter_at_offset(cursor_offset - 1), buffer.get_iter_at_offset(cursor_offset))
            buffer.delete(buffer.get_iter_at_offset(target_offset - 1), buffer.get_iter_at_offset(target_offset))
        elif keyval == _KEYVAL_DELETE:
            if cursor_offset < target_offset: target_offset -= 1
            buffer.delete(buffer.get_iter_at_offset(cursor_offset), buffer.get_iter_at_offset(cursor_offset + 1))
            buffer.delete(buffer.get_iter_at_offset(target_offset), buffer.get_iter_at_offset(target_offset + 1))
        else:
            if cursor_offset < target_offset: target_offset += 1
            char = Gdk.keyval_name(keyval)
            buffer.insert_at_cursor(char)
            buffer.insert(buffer.get_iter_at_offset(target_offset), char)
        buffer.end_user_action()

        return True



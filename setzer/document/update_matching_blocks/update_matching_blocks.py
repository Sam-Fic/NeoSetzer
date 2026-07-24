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
from gi.repository import Gtk, Gdk

import re

from setzer.app.service_locator import ServiceLocator


# on_keypress 是打字热路径，每次按键都跑。原实现每帧调
# ServiceLocator.get_regex_object('[a-zA-Z]\\Z') 做一次 dict 查表，
# 模块级预编译后直接 .match，省去哈希查找。
_LETTER_REGEX = re.compile(r'[a-zA-Z]\Z')
# handle_keypress_inside_begin_or_end 中匹配 \begin{...}\end{...} 的正则，
# 同样从每次按键的 ServiceLocator 查表改为直接持有。
_BEGIN_END_REGEX = re.compile(r'.*\\(begin|end)\{((?:[^\{\[\(])*)%•%((?:[^\{\[\(])*)\}')

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
        offset = insert_iter.get_line_offset()
        cursor_offset = insert_iter.get_offset()
        line = line[:offset] + '%•%' + line[offset:]
        match_begin_end = _BEGIN_END_REGEX.match(line)
        if match_begin_end == None: return False
        if keyval == _KEYVAL_BACKSPACE and len(match_begin_end.group(2)) == 0: return False
        if keyval == _KEYVAL_DELETE and len(match_begin_end.group(3)) == 0: return False

        orig_offset = cursor_offset - insert_iter.get_line_offset() + match_begin_end.start()
        offset = None
        for block in self.document.parser.symbols['blocks']:
            if block[0] == orig_offset:
                if block[1] == None:
                    return False
                else:
                    offset = block[1] + 5 + len(match_begin_end.group(2))
                    break
            elif block[1] == orig_offset:
                if block[0] == None:
                    return False
                else:
                    offset = block[0] + 7 + len(match_begin_end.group(2))
                    break
        if offset == None: return False

        buffer.begin_user_action()
        if keyval == _KEYVAL_ASTERISK:
            if cursor_offset < offset: offset += 1
            buffer.insert_at_cursor('*')
            buffer.insert(buffer.get_iter_at_offset(offset), '*')
        elif keyval == _KEYVAL_BACKSPACE:
            if cursor_offset < offset: offset -= 1
            buffer.delete(buffer.get_iter_at_offset(cursor_offset - 1), buffer.get_iter_at_offset(cursor_offset))
            buffer.delete(buffer.get_iter_at_offset(offset - 1), buffer.get_iter_at_offset(offset))
        elif keyval == _KEYVAL_DELETE:
            if cursor_offset < offset: offset -= 1
            buffer.delete(buffer.get_iter_at_offset(cursor_offset), buffer.get_iter_at_offset(cursor_offset + 1))
            buffer.delete(buffer.get_iter_at_offset(offset), buffer.get_iter_at_offset(offset + 1))
        else:
            if cursor_offset < offset: offset += 1
            char = Gdk.keyval_name(keyval)
            buffer.insert_at_cursor(char)
            buffer.insert(buffer.get_iter_at_offset(offset), char)
        buffer.end_user_action()

        return True



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
from gi.repository import Gtk

from setzer.app.color_manager import ColorManager


class BeginEndHighlight(object):
    r'''Highlight the \begin{...} / \end{...} pair next to the cursor.

    GtkSourceView only highlights real brackets (()[]{}). This feature adds
    the equivalent for LaTeX environments: when the cursor is on (or
    immediately after) a \begin{name} or \end{name}, both that command and
    its matching partner are highlighted with a background tag. Nesting is
    taken into account so the innermost matching pair wins.

    The parser stores each begin/end occurrence as a 3-tuple
    ``(regex_match, line_number, absolute_offset)`` in
    ``parser.block_symbol_matches['begin_or_end']``:
      - ``regex_match.group(1)`` is ``'begin'`` or ``'end'``
      - ``regex_match.group(2)`` is the environment name
      - ``regex_match.group(0)`` is the full ``\begin{name}`` / ``\end{name}``
      - ``absolute_offset`` is the buffer offset of the leading backslash.
    '''

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer

        self.is_enabled = self.document.settings.get_value('preferences', 'highlight_matching_begin_end')

        # One persistent tag reused across updates. High priority so the
        # background is drawn on top of syntax highlighting.
        self.tag = self.source_buffer.create_tag('begin_end_match')
        color = ColorManager.get_ui_color('highlight_begin_end_textview')
        self.tag.props.background_full_height = True
        self.tag.props.background_rgba = color
        self.tag.set_priority(self.source_buffer.get_tag_table().get_size() - 1)

        self.last_tagged_ranges = []

        self.document.connect('cursor_position_changed', self.on_cursor_position_changed)
        self.document.parser.connect('finished_parsing', self.on_parser_finished)
        self.document.settings.connect('settings_changed', self.on_settings_changed)

        self.update()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'highlight_matching_begin_end':
            self.is_enabled = value
            if not value:
                self.clear()
            else:
                self.update()

    def on_cursor_position_changed(self, document):
        self.update()

    def on_parser_finished(self, parser):
        if self.is_enabled:
            self.update()

    def clear(self):
        if self.last_tagged_ranges:
            char_count = self.source_buffer.get_char_count()
            for start, end in self.last_tagged_ranges:
                start = max(0, min(start, char_count))
                end = max(0, min(end, char_count))
                if end > start:
                    self.source_buffer.remove_tag(self.tag,
                        self.source_buffer.get_iter_at_offset(start),
                        self.source_buffer.get_iter_at_offset(end))
            self.last_tagged_ranges = []

    @staticmethod
    def _span(item):
        '''Return (start_offset, end_offset, kind, name) for a begin/end tuple.'''
        regex_match, _line, offset = item
        start = offset
        end = offset + len(regex_match.group(0))
        return start, end, regex_match.group(1), regex_match.group(2)

    def _matches_sorted(self):
        matches = self.document.parser.block_symbol_matches['begin_or_end']
        return sorted(matches, key=lambda item: item[2])

    def _match_at_cursor(self, matches, cursor_offset):
        candidates = []
        for item in matches:
            start, end, _kind, _name = self._span(item)
            if start <= cursor_offset <= end:
                candidates.append((item, start))
        if not candidates:
            return None
        # 当偏移量正好落在两条命令交界处（前一条的 end == 后一条的
        # start）时，优先选择起点正好等于光标的那条，即「点击某条命令
        # 开头」会高亮该命令，而不是左边相邻命令。
        for item, start in candidates:
            if start == cursor_offset:
                return item
        return candidates[0][0]

    def _find_partner(self, matches, target):
        t_start, t_end, t_kind, t_name = self._span(target)

        if t_kind == 'begin':
            depth = 0
            for item in matches:
                if item is target:
                    continue
                start, end, kind, name = self._span(item)
                if start <= t_start:
                    continue
                if kind == 'begin' and name == t_name:
                    depth += 1
                elif kind == 'end' and name == t_name:
                    if depth == 0:
                        return item
                    depth -= 1
        else:
            depth = 0
            for item in reversed(matches):
                if item is target:
                    continue
                start, end, kind, name = self._span(item)
                if start >= t_end:
                    continue
                if kind == 'end' and name == t_name:
                    depth += 1
                elif kind == 'begin' and name == t_name:
                    if depth == 0:
                        return item
                    depth -= 1
        return None

    def find_pair_at_offset(self, offset):
        '''Return ``(target_span, partner_span)`` for the \\begin/\\end pair
        under ``offset`` (each span is ``(start_offset, end_offset)``), or None.

        ``target_span`` is the command containing the offset; ``partner_span``
        is its matching counterpart. Shared by highlighting and Ctrl+Click
        navigation.'''
        matches = self._matches_sorted()
        target = self._match_at_cursor(matches, offset)
        if target is None:
            return None
        partner = self._find_partner(matches, target)
        if partner is None:
            return None
        return (self._span(target)[:2], self._span(partner)[:2])

    def update(self):
        if not self.is_enabled:
            self.clear()
            return

        self.clear()

        insert = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        cursor_offset = insert.get_offset()

        pair = self.find_pair_at_offset(cursor_offset)
        if pair is None:
            return

        ranges = [pair[0], pair[1]]
        for start, end in ranges:
            self.source_buffer.apply_tag(self.tag,
                self.source_buffer.get_iter_at_offset(start),
                self.source_buffer.get_iter_at_offset(end))
        self.last_tagged_ranges = ranges

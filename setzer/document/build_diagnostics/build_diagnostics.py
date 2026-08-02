#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) later versions.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
from gi.repository import Gdk

from setzer.helpers.observable import Observable


class BuildDiagnostics(Observable):
    '''把编译日志里解析出的错误/警告行号映射回编辑器高亮。

    - 在 source buffer 中给对应整行加背景高亮（错误强制红、警告琥珀色，
      均不读取强调色 / accent，避免与主题强调色混淆）。
    - 暴露 error_lines / warning_lines 集合供 gutter 在行号左缘绘制色条。
    - 文档内容被修改后行号即失效，自动清除高亮，避免标错位置。
    '''

    # 固定颜色，不跟随强调色。
    ERROR_COLOR = Gdk.RGBA(0.85, 0.13, 0.13, 1.0)
    WARNING_COLOR = Gdk.RGBA(0.90, 0.55, 0.10, 1.0)
    ERROR_BG = Gdk.RGBA(1.0, 0.55, 0.55, 0.22)
    WARNING_BG = Gdk.RGBA(1.0, 0.85, 0.40, 0.20)

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.source_buffer = document.source_buffer

        self.error_lines = set()
        self.warning_lines = set()
        # 行号(1-based) -> 该行的错误/警告描述列表，供 gutter 悬停提示使用。
        self.error_messages = dict()
        self.warning_messages = dict()

        self.error_tag = self.source_buffer.create_tag(
            'build_error',
            background_rgba=self.ERROR_BG,
            background_full_height=True)
        self.warning_tag = self.source_buffer.create_tag(
            'build_warning',
            background_rgba=self.WARNING_BG,
            background_full_height=True)

        self.source_buffer.connect('changed', self.on_buffer_changed)

    def set_diagnostics(self, error_map, warning_map):
        # error_map / warning_map: {line_number(1-based): [description, ...]}
        self.error_lines = set(error_map.keys())
        self.warning_lines = set(warning_map.keys())
        self.error_messages = error_map
        self.warning_messages = warning_map
        self._apply_tags()
        self.document.add_change_code('build_diagnostics_changed')

    def clear(self):
        self.set_diagnostics(dict(), dict())

    def _apply_tags(self):
        buf = self.source_buffer
        start = buf.get_start_iter()
        end = buf.get_end_iter()
        buf.remove_tag(self.error_tag, start, end)
        buf.remove_tag(self.warning_tag, start, end)
        # 同行既报错又告警时，只打 error 标签（红），不再叠加 warning 标签，
        # 从而与 gutter「错误优先显红色」的行为完全一致；同时规避两个标签
        # 都设 background 时由标签优先级决定胜负的歧义。纯警告行仍显琥珀。
        for line_number in self.warning_lines - self.error_lines:
            self._tag_line(buf, line_number, self.warning_tag)
        for line_number in self.error_lines:
            self._tag_line(buf, line_number, self.error_tag)

    def _tag_line(self, buf, line_number, tag):
        line_index = line_number - 1
        if line_index < 0 or line_index >= buf.get_line_count():
            return
        found, start = buf.get_iter_at_line(line_index)
        end = start.copy()
        end.forward_to_line_end()
        # 包含换行符，使整行背景（background_full_height）正确渲染到行尾。
        if not end.ends_line():
            end.forward_char()
        if start.equal(end):
            return
        buf.apply_tag(tag, start, end)

    def on_buffer_changed(self, buffer):
        if self.error_lines or self.warning_lines:
            self.error_lines = set()
            self.warning_lines = set()
            self.error_messages = dict()
            self.warning_messages = dict()
            buf = self.source_buffer
            buf.remove_tag(self.error_tag, buf.get_start_iter(), buf.get_end_iter())
            buf.remove_tag(self.warning_tag, buf.get_start_iter(), buf.get_end_iter())
            self.document.add_change_code('build_diagnostics_changed')

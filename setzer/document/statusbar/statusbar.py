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

'''状态栏控制器：监听文档变化并更新状态栏各字段。

更新时机：
- 光标移动（cursor_position_changed）：行/列、选区词数
- 设置变化（settings_changed）：缩进设置
- 文档加载/语言切换：语言、编码（一次性，构造时即定）

每个文档实例拥有自己的 StatusBar（与 Gutter / Search 同模式），
DocumentView 在 Gtk.Stack 中切换时，对应文档的状态栏自然可见，
无需跟踪 active document 切换。
'''

import setzer.document.statusbar.statusbar_viewgtk as statusbar_view


# 编码：应用统一用 open(filename) 无显式 encoding（见 document.py load_from_disk），
# 默认 UTF-8（Linux locale 通常为 UTF-8）。无多编码追踪，状态栏显示静态 UTF-8。
_ENCODING_LABEL = 'UTF-8'

# 语言显示名映射：document.language ('latex'/'bibtex') → 展示名
_LANGUAGE_LABELS = {'latex': 'LaTeX', 'bibtex': 'BibTeX'}


class StatusBar(object):

    def __init__(self, document):
        self.document = document
        self.source_buffer = document.source_buffer
        self.settings = document.settings
        self.view = statusbar_view.StatusBarView()

        # 静态字段：编码、语言（文档生命周期内不变）
        self.view.encoding_label.set_text(_ENCODING_LABEL)
        self.view.language_label.set_text(
            _LANGUAGE_LABELS.get(document.language, document.language))

        # 初始刷新一次（行/列、缩进、选区）
        self.update_cursor_fields()
        self.update_indent_field()

        # 监听光标移动：行/列与选区词数都依赖光标位置
        document.connect('cursor_position_changed', self.on_cursor_position_changed)
        # 监听设置变化：缩进设置（spaces_instead_of_tabs / tab_width）可被用户在偏好中改
        self.settings.connect('settings_changed', self.on_settings_changed)

    def on_cursor_position_changed(self, document):
        self.update_cursor_fields()

    def on_settings_changed(self, settings, parameter):
        # 设置变化范围广（不止缩进），但状态栏仅关心缩进相关项。
        # parameter 为 (group, key) 元组时精确判断；无参数时全量刷新（廉价）。
        if parameter is None:
            self.update_indent_field()
            return
        group, key = parameter[0], parameter[1]
        if group == 'preferences' and key in ('spaces_instead_of_tabs', 'tab_width'):
            self.update_indent_field()

    def update_cursor_fields(self):
        '''更新行/列与选区词数。光标移动时调用。'''
        # 行/列：get_line/get_line_offset 均为 0-based，显示用 1-based
        insert_mark = self.source_buffer.get_insert()
        iter_at_cursor = self.source_buffer.get_iter_at_mark(insert_mark)
        line = iter_at_cursor.get_line() + 1
        col = iter_at_cursor.get_line_offset() + 1
        # 使用gettext: 行列标签。格式与多数编辑器一致："Ln {line}, Col {col}"
        self.view.line_col_label.set_text(
            _('Ln {line}, Col {col}').format(line=line, col=col))

        # 选区行数：仅有选区时显示
        bounds = self.source_buffer.get_selection_bounds()
        if len(bounds) == 2 and bounds[0].get_offset() != bounds[1].get_offset():
            start_line = bounds[0].get_line() + 1
            end_line = bounds[1].get_line() + 1
            line_count = end_line - start_line + 1
            # ngettext 处理单复数: "1 line selected" / "N lines selected"
            self.view.selection_label.set_text(
                ngettext('{n} line selected', '{n} lines selected', line_count).format(n=line_count))
            self.view.selection_label.set_visible(True)
        else:
            self.view.selection_label.set_visible(False)

    def update_indent_field(self):
        '''更新缩进设置标签。'''
        spaces = self.settings.get_value('preferences', 'spaces_instead_of_tabs')
        tab_width = self.settings.get_value('preferences', 'tab_width')
        if spaces:
            self.view.indent_label.set_text(_('Spaces: {n}').format(n=tab_width))
        else:
            self.view.indent_label.set_text(_('Tabs: {n}').format(n=tab_width))

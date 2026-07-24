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

from gi.repository import Gdk

# 延迟导入避免循环：controller 引用 presenter 的 TYPE_FILTER 常量。
import setzer.dialogs.build_log.build_log_dialog_presenter as presenter_module


class BuildLogDialogController(object):
    '''处理弹窗内的用户交互：单击行跳转报错行 + Copy All 按钮。'''

    def __init__(self, build_log, dialog_view):
        self.build_log = build_log
        self.view = dialog_view

        # Copy All 按钮
        self.view.copy_all_button.connect('clicked', self.on_copy_all_clicked)

        # 每个 list 的 row-activated：单击跳转报错行（与原 BuildLogController 一致）。
        # 弹窗内有 3 个 list（Errors / Warnings / Badboxes），全部连同一个回调。
        for lst in self.view.lists.values():
            lst.connect('row-activated', self.on_row_activated)

    def on_row_activated(self, listbox, row):
        '''单击行：打开对应源文件并定位到报错行。

        逻辑与原 BuildLogController.on_row_activated 完全一致，迁移至此。
        '''
        if self.build_log.document is None:
            return
        if row is None or row.filename is None:
            return

        # open_document_by_filename 在文件无法打开时返回 None 且不改变
        # active_document。原代码无视返回值继续对 active_document 调用
        # place_cursor：若文件打不开会把光标定位到「上一个活动文档」的错误
        # 位置;若没有任何文档打开则 active_document 为 None,此处会抛
        # AttributeError 崩溃。故必须检查返回值。
        document = self.build_log.workspace.open_document_by_filename(row.filename)
        if document is None:
            return
        line_number = row.line_number - 1
        if line_number < 0:
            return

        document.place_cursor(row.line_number - 1)
        document.scroll_cursor_onscreen()
        document.source_view.grab_focus()

    def on_copy_all_clicked(self, button):
        '''Copy 所有当前显示的 items（按设置项过滤后），格式 file:line: description per line。'''
        autoshow = self.build_log.settings.get_value('preferences', 'autoshow_build_log')
        visible_types = presenter_module.BuildLogDialogPresenter.TYPE_FILTER.get(
            autoshow, presenter_module.BuildLogDialogPresenter.TYPE_FILTER['all'])

        lines = []
        for item in self.build_log.items:
            if item[0] not in visible_types:
                continue
            lines.append(self._format_item(item))
        Gdk.Display.get_default().get_clipboard().set('\n'.join(lines))

    @staticmethod
    def _format_item(item):
        '''单行文本格式，与 BuildLogList._format_row_text 一致。'''
        # item 元组：item[0]=type, item[1]=未用, item[2]=filename, item[3]=line_number, item[4]=description
        item_type, _, filename, line_number, description = item
        parts = []
        if filename:
            parts.append(filename)
            if line_number >= 0:
                parts.append(str(line_number))
        text = ':'.join(parts)
        if description:
            text = (text + ': ' + description) if text else description
        return text

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

from gi.repository import Gdk, Adw, Gtk, Gio
import os.path

# 延迟导入避免循环：controller 引用 presenter 的 TYPE_FILTER 常量。
import setzer.dialogs.build_log.build_log_dialog_presenter as presenter_module


class BuildLogDialogController(object):
    '''处理弹窗内的用户交互：单击行跳转报错行 + Copy All 按钮。'''

    def __init__(self, build_log, dialog_view):
        self.build_log = build_log
        self.view = dialog_view
        self.presenter = None

        # Copy All 按钮
        self.view.copy_all_button.connect('clicked', self.on_copy_all_clicked)

        # Save Log As 按钮
        self.view.save_log_button.connect('clicked', self.on_save_log_clicked)

        # 搜索框：输入文本实时过滤日志项。
        self.view.search_entry.connect('changed', self.on_search_changed)

        # 过滤器信号（存储 handler_id 以便 presenter 更新下拉框时屏蔽信号）
        self.view._file_filter_handler_id = self.view.file_filter_combo.connect('changed', self.on_filter_changed)
        self.view._type_filter_handler_id = self.view.type_filter_combo.connect('changed', self.on_filter_changed)
        self.view.line_min_spin.connect('value-changed', self.on_filter_changed)
        self.view.line_max_spin.connect('value-changed', self.on_filter_changed)

        # 每个 list 的 row-activated：单击跳转报错行（与原 BuildLogController 一致）。
        # 弹窗内有 3 个 list（Errors / Warnings / Badboxes），全部连同一个回调。
        for lst in self.view.lists.values():
            lst.connect('row-activated', self.on_row_activated)

    def on_row_activated(self, listbox, row):
        '''单击行：打开对应源文件并定位到报错行。

        逻辑与原 BuildLogController.on_row_activated 完全一致，迁移至此。
        增加：跳转后高亮目标行，\\input 文件自动打开，跳转失败时 toast 提示。
        '''
        if self.build_log.document is None:
            return
        if row is None or row.filename is None:
            return

        document = self.build_log.workspace.open_document_by_filename(row.filename)
        if document is None:
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Could not open file')))
            return
        line_number = row.line_number - 1
        if line_number < 0:
            return

        document.place_cursor(line_number)
        document.scroll_cursor_onscreen()
        document.source_view.grab_focus()

        start, end = document.source_buffer.get_iter_at_line(line_number), None
        if start is not None:
            end = start.copy()
            if not start.ends_line():
                end.forward_to_line_end()
            document.highlight_section(start, end)

    def on_copy_all_clicked(self, button):
        '''Copy 所有当前显示的 items（按设置项过滤后），格式 file:line: description per line。'''
        lines = self._get_filtered_lines()
        Gdk.Display.get_default().get_clipboard().set('\n'.join(lines))
        self.view.toast_overlay.add_toast(Adw.Toast.new(_('Copied to clipboard')))

    def on_save_log_clicked(self, button):
        '''Save Log As：将过滤后的日志保存到文件。'''
        dialog = Gtk.FileChooserNative(
            title=_('Save Build Log As'),
            transient_for=self.view.get_root(),
            action=Gtk.FileChooserAction.SAVE,
            accept_label=_('Save'),
            cancel_label=_('Cancel'))
        dialog.set_current_name('build_log.txt')

        # 过滤器：文本文件
        filter_text = Gtk.FileFilter()
        filter_text.set_name(_('Text files'))
        filter_text.add_mime_type('text/plain')
        dialog.add_filter(filter_text)

        filter_all = Gtk.FileFilter()
        filter_all.set_name(_('All files'))
        filter_all.add_pattern('*')
        dialog.add_filter(filter_all)

        dialog.connect('response', self.on_save_log_response)
        dialog.show()

    def on_save_log_response(self, dialog, response):
        '''处理文件保存对话框的响应。'''
        if response != Gtk.ResponseType.ACCEPT:
            return
        file = dialog.get_file()
        if file is None:
            return
        lines = self._get_filtered_lines()
        try:
            with open(file.get_path(), 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Build log saved')))
        except Exception:
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Failed to save build log')))

    def _get_filtered_lines(self):
        '''获取当前过滤条件下所有 items 的文本行列表。'''
        autoshow = self.build_log.settings.get_value('preferences', 'autoshow_build_log')
        visible_types = presenter_module.BuildLogDialogPresenter.TYPE_FILTER.get(
            autoshow, presenter_module.BuildLogDialogPresenter.TYPE_FILTER['all'])

        # 获取过滤器值
        file_filter = self.view.file_filter_combo.get_active_text()
        type_filter = self.view.type_filter_combo.get_active_text()

        line_min = int(self.view.line_min_spin.get_value())
        line_max = int(self.view.line_max_spin.get_value())
        if line_max == 0:
            line_max = 999999

        lines = []
        search_text = self.view.search_entry.get_text().lower()
        for item in self.build_log.items:
            if item[0] not in visible_types:
                continue
            # 搜索过滤
            if search_text:
                description = (item[4] or '').lower()
                filename = (item[2] or '').lower()
                line_number = str(item[3]) if item[3] >= 0 else ''
                if not (search_text in description or search_text in filename or search_text in line_number):
                    continue
            # 文件过滤
            if file_filter and file_filter != _('All'):
                if item[2] is None or os.path.basename(item[2]) != file_filter:
                    continue
            # 错误类型过滤（基于描述内容的关键词匹配）
            if type_filter and type_filter != _('All'):
                desc = (item[4] or '').lower()
                if not self._matches_error_type(type_filter, desc, item[0]):
                    continue
            # 行号范围过滤
            if item[3] >= 0 and (item[3] < line_min or item[3] > line_max):
                continue
            lines.append(self._format_item(item))
        return lines

    def _matches_error_type(self, type_filter, description, item_type):
        '''检查日志项是否匹配指定的错误类型过滤。'''
        if type_filter == _('Undefined reference'):
            return 'undefined' in description and 'reference' in description
        elif type_filter == _('Missing package'):
            return 'missing' in description or 'not found' in description
        elif type_filter == _('Syntax error'):
            return 'syntax' in description or 'error' in description.lower()
        elif type_filter == _('All types'):
            return True
        return True

    def on_filter_changed(self, widget, *args):
        '''过滤器变化时触发视图重建。'''
        if self.presenter is not None:
            self.presenter.set_filter_values(
                self._get_file_filter_value(),
                self._get_type_filter_value(),
                int(self.view.line_min_spin.get_value()),
                int(self.view.line_max_spin.get_value()))

    def _get_file_filter_value(self):
        return self.view.file_filter_combo.get_active_text()

    def _get_type_filter_value(self):
        return self.view.type_filter_combo.get_active_text()

    def on_search_changed(self, search_entry):
        self.presenter.set_search_text(search_entry.get_text())

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

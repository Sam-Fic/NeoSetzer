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
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Adw, Gio


class OpenDocumentDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.view = None

    def run(self):
        self.setup()
        self.view.open_multiple(self.main_window, None, self.dialog_process_response)

    def setup(self):
        self.view = Gtk.FileDialog()
        self.view.set_modal(True)
        self.view.set_title(_('Open'))

        latex_filter = Gtk.FileFilter()
        latex_filter.add_pattern('*.tex')
        latex_filter.add_pattern('*.bib')
        latex_filter.add_pattern('*.cls')
        latex_filter.add_pattern('*.sty')
        latex_filter.set_name(_('LaTeX and BibTeX Files'))

        all_filter = Gtk.FileFilter()
        all_filter.add_pattern('*')
        all_filter.set_name(_('All Files'))

        filters_model = Gio.ListStore.new(Gtk.FileFilter)
        filters_model.append(latex_filter)
        filters_model.append(all_filter)
        self.view.set_filters(filters_model)
        self.view.set_default_filter(latex_filter)

    def dialog_process_response(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            # 用户取消了对话框。
            return

        if files is None:
            return

        # 收集所有路径，显示 spinner 后延迟 200ms 批量打开，
        # 让 spinner 先渲染再执行重操作（读盘 + 创建 GTK 组件）。
        paths = []
        for file in files:
            path = file.get_path()
            if path:
                paths.append(path)

        if not paths:
            return

        if hasattr(self.main_window, 'show_loading_spinner'):
            self.main_window.show_loading_spinner()
            GLib.timeout_add(200, self._do_open_files, paths)
        else:
            self._do_open_files(paths)

    def _do_open_files(self, paths):
        '''timeout 回调：spinner 渲染后批量打开文件。

        逐个文件独立 try/except：单个文件打开失败（损坏、权限等）不应中断
        其余文件的打开。收集失败列表，操作完成后统一提示。
        '''
        failed_files = []
        for path in paths:
            try:
                self.workspace.open_document_by_filename(path)
            except Exception:
                failed_files.append(path)

        if failed_files:
            self._show_open_errors(failed_files)
        return False

    def _show_open_errors(self, failed_files):
        '''批量打开失败时弹出 toast 提示用户哪些文件未能打开。'''
        from setzer.app.service_locator import ServiceLocator
        main_window = ServiceLocator.get_main_window()
        if len(failed_files) == 1:
            msg = _('Could not open: {filename}').format(
                filename=failed_files[0].split('/')[-1])
        else:
            msg = _('Could not open {count} files').format(count=len(failed_files))
        toast = Adw.Toast.new(msg)
        toast.set_timeout(5)
        main_window.toast_overlay.add_toast(toast)



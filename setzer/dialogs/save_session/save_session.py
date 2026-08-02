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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, Adw

import os.path

from setzer.app.service_locator import ServiceLocator


class SaveSessionDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace

    def run(self):
        self.setup()
        self.view.save(self.main_window, None, self.dialog_process_response)

    def setup(self):
        self.view = Gtk.FileDialog()
        self.view.set_modal(True)
        self.view.set_title(_('Save Session'))

        # 与 OpenSessionDialog 一致的 .stzs 过滤器，避免用户误存为其他扩展名
        # 导致下次打开时找不到文件。
        file_filter = Gtk.FileFilter()
        file_filter.add_pattern('*.stzs')
        file_filter.set_name(_('Setzer Session'))
        self.view.set_default_filter(file_filter)

        if self.workspace.session_file_opened != None:
            self.view.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.workspace.session_file_opened)))
            self.view.set_initial_name(os.path.basename(self.workspace.session_file_opened))
        else:
            document = self.workspace.get_root_or_active_latex_document()
            # 默认文件名：取根/活动文档基名 + .stzs（如 report.tex → report.stzs）。
            # 不用原默认 '.stzs'——点开头在 Linux 上是隐藏文件，用户在文件选择器
            # 中看不到，多次保存还会互相覆盖。无文档时退回 'session.stzs'。
            default_name = 'session.stzs'
            if document != None:
                pathname = document.get_filename()
                if pathname != None:
                    self.view.set_initial_folder(Gio.File.new_for_path(os.path.dirname(pathname)))
                    stem = os.path.splitext(os.path.basename(pathname))[0]
                    if stem and not stem.startswith('.'):
                        default_name = stem + '.stzs'
            self.view.set_initial_name(default_name)

    def dialog_process_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except Exception: pass
        else:
            if file != None:
                filename = file.get_path()
                # save_session 返回 False 表示写入失败（权限不足、磁盘满等）。
                # 原代码两层静默吞掉（workspace 的 except IOError: pass + 此处
                # except Exception: pass），用户完全无感知。现在失败时弹 toast。
                try:
                    success = self.workspace.save_session(filename)
                except Exception:
                    success = False
                if not success:
                    self._show_toast(_('Failed to save session'))

    def _show_toast(self, text):
        '''与 BuildWidget._show_toast 同模式：通过 main_window.toast_overlay 弹出
        短暂提示。toast_overlay 在 main_window 上常驻。'''
        main_window = ServiceLocator.get_main_window()
        if hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(text)
            toast.set_timeout(3)
            main_window.toast_overlay.add_toast(toast)



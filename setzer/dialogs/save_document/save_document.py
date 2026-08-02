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
# along with this program. If not, see <https://www.gnu.org/licenses/>.


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw

import os.path

from setzer.app.service_locator import ServiceLocator


class SaveDocumentDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.document = None
        self.callback = None
        self.arguments = None

    def run(self, document, callback=None, arguments=None):
        self.document = document
        self.callback = callback
        self.arguments = arguments
        self.setup()
        self.view.save(self.main_window, None, self.dialog_process_response)

    def setup(self):
        self.view = Gtk.FileDialog()
        self.view.set_modal(True)
        self.view.set_title(_('Save document'))

        pathname = self.document.get_filename()
        if pathname != None:
            self.view.set_initial_name(os.path.basename(pathname))
            self.view.set_initial_folder(Gio.File.new_for_path(self.document.get_dirname()))
        else:
            if self.document.get_document_type() == 'latex':
                ending = '.tex'
            elif self.document.get_document_type() == 'bibtex':
                ending = '.bib'
            else:
                ending = ''
            self.view.set_initial_name(ending)

    def dialog_process_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            # 用户取消了对话框（GTK 抛 GLib.Error with "Dismissed by user"）。
            pass
        else:
            if file != None:
                filename = file.get_path()
                self.document.set_filename(filename)
                if self.document.save_to_disk(show_toast=False):
                    self.workspace.update_recently_opened_document(filename)
                else:
                    # save_to_disk 返回 False，用带重试按钮的 toast 提示
                    self._show_retry_toast()

        if self.callback != None:
            self.callback(self.arguments)

    def _show_retry_toast(self):
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Save failed. The disk may be full or you may not have permission to write the file.'))
            toast.set_timeout(0)
            toast.set_button_label(_('Retry'))
            toast.connect('button-clicked', self._on_retry_clicked)
            main_window.toast_overlay.add_toast(toast)

    def _on_retry_clicked(self, toast):
        if not self.document.save_to_disk(show_toast=False):
            self._show_retry_toast()

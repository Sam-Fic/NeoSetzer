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
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Adw, Gio
import os
import shutil


class ExportPdfDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace

    def run(self, document):
        pdf_path = document.preview.pdf_filename
        if pdf_path is None or not os.path.exists(pdf_path):
            self._show_toast(_('No PDF available to export.'))
            return

        self.source_path = pdf_path

        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        dialog.set_title(_('Export PDF As…'))
        dialog.set_initial_name(os.path.basename(pdf_path))

        initial_folder = Gio.File.new_for_path(os.path.dirname(pdf_path))
        dialog.set_initial_folder(initial_folder)

        pdf_filter = Gtk.FileFilter()
        pdf_filter.add_mime_type('application/pdf')
        pdf_filter.add_pattern('*.pdf')
        pdf_filter.set_name(_('PDF Files'))

        filters_model = Gio.ListStore.new(Gtk.FileFilter)
        filters_model.append(pdf_filter)
        dialog.set_filters(filters_model)
        dialog.set_default_filter(pdf_filter)

        dialog.save(self.main_window, None, self._on_response)

    def _on_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            # 用户取消了对话框。
            return

        if file is None:
            return

        dest = file.get_path()
        try:
            shutil.copy2(self.source_path, dest)
        except Exception as e:
            self._show_toast(_('Could not export PDF to: {filename}\n{error}').format(
                filename=os.path.basename(dest),
                error=str(e)))
            return

        self._show_toast(_('PDF exported to: {filename}').format(
            filename=os.path.basename(dest)))

    def _show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        self.main_window.toast_overlay.add_toast(toast)

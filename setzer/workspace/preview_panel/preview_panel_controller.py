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
from gi.repository import Gio, GLib

import os.path

from setzer.app.service_locator import ServiceLocator


class PreviewPanelController(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel
        self.headerbar = self.main_window.headerbar

        # 按钮已迁移到 headerbar（Pass-11），通过 headerbar.preview_* 引用。
        self.headerbar.preview_zoom_in_button.connect('clicked', self.on_zoom_in_button_clicked)
        self.headerbar.preview_zoom_out_button.connect('clicked', self.on_zoom_out_button_clicked)

        self.headerbar.preview_external_viewer_button.connect('clicked', self.on_external_viewer_button_clicked)
        self.headerbar.preview_recolor_pdf_toggle.connect('toggled', self.on_recolor_pdf_toggle_toggled)

    def on_zoom_in_button_clicked(self, button):
        document = self.workspace.get_root_or_active_latex_document()
        if document != None:
            document.preview.zoom_manager.zoom_in()

    def on_zoom_out_button_clicked(self, button):
        document = self.workspace.get_root_or_active_latex_document()
        if document != None:
            document.preview.zoom_manager.zoom_out()

    def on_external_viewer_button_clicked(self, button):
        document = self.workspace.get_root_or_active_latex_document()
        if document != None:
            pdf_filename = document.preview.pdf_filename
            if document.preview.poppler_document != None and pdf_filename != None:
                if os.path.isfile(pdf_filename):
                    Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(pdf_filename))

    def on_recolor_pdf_toggle_toggled(self, toggle_button, parameter=None):
        recolor_pdf = toggle_button.get_active()
        if ServiceLocator.get_settings().get_value('preferences', 'recolor_pdf') != recolor_pdf:
            ServiceLocator.get_settings().set_value('preferences', 'recolor_pdf', recolor_pdf)



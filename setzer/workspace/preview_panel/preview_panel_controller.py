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
from gi.repository import Gio, GLib

import os.path

from setzer.app.service_locator import ServiceLocator


class PreviewPanelController(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel

        # Pass-12: 按钮回到 preview_panel 内嵌工具栏（与左侧栏一致）。
        self.view.zoom_in_button.connect('clicked', self.on_zoom_in_button_clicked)
        self.view.zoom_out_button.connect('clicked', self.on_zoom_out_button_clicked)

        self.view.external_viewer_button.connect('clicked', self.on_external_viewer_button_clicked)
        self.view.recolor_pdf_toggle.connect('toggled', self.on_recolor_pdf_toggle_toggled)
        self.view.magnifier_toggle.connect('toggled', self.on_magnifier_toggle_toggled)
        # 弹出为独立窗口。guard 在 workspace.pop_out_preview 内（已弹出 / 无文档时 no-op）。
        # 收回方式：关闭独立窗口（close-request → workspace.pop_in_preview）。
        self.view.detach_button.connect('clicked', self.on_detach_button_clicked)

    def on_detach_button_clicked(self, button):
        self.workspace.pop_out_preview()

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

    def on_magnifier_toggle_toggled(self, toggle_button, parameter=None):
        use_magnifier = toggle_button.get_active()
        if ServiceLocator.get_settings().get_value('preferences', 'use_magnifier') != use_magnifier:
            ServiceLocator.get_settings().set_value('preferences', 'use_magnifier', use_magnifier)



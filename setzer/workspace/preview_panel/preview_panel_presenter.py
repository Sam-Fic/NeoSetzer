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

from setzer.app.service_locator import ServiceLocator


class PreviewPanelPresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel
        self.stack = self.main_window.preview_panel.stack
        # Pass-11: 按钮与 paging_label 已迁移到 headerbar / MainWindow。
        #   - preview_* 按钮 → headerbar.preview_*
        #   - paging_label   → main_window.paging_label（preview_paned_overlay overlay）
        self.headerbar = self.main_window.headerbar
        self.paging_label = self.main_window.paging_label
        self.document = None

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)

        self.update_label()
        self.update_buttons()

        self.headerbar.preview_recolor_pdf_toggle.set_active(self.workspace.settings.get_value('preferences', 'recolor_pdf'))
        self.workspace.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.headerbar.preview_recolor_pdf_toggle.set_active(value)

    def on_new_document(self, workspace, document):
        if document.is_latex_document():
            self.stack.add_child(document.preview.view)

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            self.stack.remove(document.preview.view)

    def on_new_active_document(self, workspace, document):
        self.set_preview_document()

    def on_root_state_change(self, workspace, root_state):
        self.set_preview_document()

    def set_preview_document(self):
        if self.document != None:
            self.document.preview.disconnect('pdf_changed', self.on_pdf_changed)

        self.document = self.workspace.get_root_or_active_latex_document()
        if self.document == None:
            self.stack.set_visible_child(self.view.empty_placeholder)
            self.update_label()
            self.update_buttons()
        else:
            self.stack.set_visible_child(self.document.preview.view)
            self.update_label()
            self.update_buttons()
            self.update_zoom_level()
            self.document.preview.connect('pdf_changed', self.on_pdf_changed)
            self.document.preview.connect('position_changed', self.on_position_changed)
            self.document.preview.connect('layout_changed', self.on_layout_changed)
            self.document.preview.zoom_manager.connect('zoom_level_changed', self.on_zoom_level_changed)

    def on_pdf_changed(self, preview):
        self.update_label()
        self.update_buttons()

    def on_position_changed(self, preview):
        self.update_label()

    def on_layout_changed(self, preview):
        self.update_label()

    def on_zoom_level_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self.update_zoom_level()

    def update_label(self):
        # Pass-11: paging_label 仅在预览展开（workspace.show_preview=True）时显示。
        # 预览关闭时即使文档/PDF 变化也不显示，避免标题栏残留 stale 文本。
        if self.document == None or not self.workspace.show_preview:
            self.paging_label.set_visible(False)
        else:
            self.paging_label.set_visible(True)
            preview = self.document.preview
            if preview.poppler_document != None:
                total = str(preview.poppler_document.get_n_pages())
                if preview.layout != None:
                    offset = preview.view.content.scrolling_offset_y
                    current = str(preview.layout.get_page_by_offset(offset))
                else:
                    current = "0"
            else:
                total = "0"
                current = "0"
            self.paging_label.set_text(_('Page ') + current + _(' of ') + total)

    def update_buttons(self):
        self.document = self.workspace.get_root_or_active_latex_document()
        if self.document == None or self.document.preview.poppler_document == None:
            self.headerbar.preview_external_viewer_button.set_visible(False)
            self.headerbar.preview_recolor_pdf_toggle.set_visible(False)
            self.headerbar.preview_zoom_out_button.set_visible(False)
            self.headerbar.preview_zoom_level_button.set_visible(False)
            self.headerbar.preview_zoom_in_button.set_visible(False)
        else:
            self.headerbar.preview_external_viewer_button.set_visible(True)
            self.headerbar.preview_recolor_pdf_toggle.set_visible(True)
            self.headerbar.preview_zoom_out_button.set_visible(True)
            self.headerbar.preview_zoom_level_button.set_visible(True)
            self.headerbar.preview_zoom_in_button.set_visible(True)

            zoom_level = self.document.preview.zoom_manager.get_zoom_level()

            self.headerbar.preview_zoom_in_button.set_sensitive(zoom_level != None and zoom_level < 4)
            self.headerbar.preview_zoom_out_button.set_sensitive(zoom_level != None and zoom_level > 0.25)

    def update_zoom_level(self):
        zoom_level = self.document.preview.zoom_manager.get_zoom_level()

        if zoom_level != None:
            self.headerbar.preview_zoom_level_label.set_text('{0:.1f}%'.format(zoom_level * 100))

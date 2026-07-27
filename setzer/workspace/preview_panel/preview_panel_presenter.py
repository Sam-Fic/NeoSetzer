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
from gi.repository import GLib


class PreviewPanelPresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel
        self.stack = self.main_window.preview_panel.stack
        self.document = None
        self._label_update_timeout_id = None

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)

        self.view.page_spin.connect('activate', self._on_page_spin_activate)
        self.view.fit_width_button.connect('clicked', self._on_fit_width_clicked)

        self.view.switch_button.connect('clicked', self._on_switch_clicked)
        self.main_window.help_panel.switch_button.connect('clicked', self._on_switch_clicked)

        # 反向挂到 view，便于其它地方（如 workspace_presenter 切换同步）
        # 通过 main_window.preview_panel.presenter 访问。
        self.view.presenter = self

        # 按实际显示的面板同步一次按钮图标（图标始终展示"目标面板"）。
        self._sync_switch_icons()
        self.update_label()
        self.update_buttons()

        self.view.recolor_pdf_toggle.set_active(self.workspace.settings.get_value('preferences', 'recolor_pdf'))
        self.workspace.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.view.recolor_pdf_toggle.set_active(value)

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
            self.document.preview.disconnect('position_changed', self.on_position_changed)
            self.document.preview.disconnect('layout_changed', self.on_layout_changed)
            self.document.preview.disconnect('pdf_stale_changed', self.on_pdf_stale_changed)
            self.document.preview.zoom_manager.disconnect('zoom_level_changed', self.on_zoom_level_changed)

        self.document = self.workspace.get_root_or_active_latex_document()
        if self.document == None:
            self.stack.set_visible_child(self.view.empty_placeholder)
            self.update_label()
            self.update_buttons()
            self.view.set_stale_banner_visible(False)
            self._detach_target_bar()
        else:
            self.stack.set_visible_child(self.document.preview.view)
            self.update_label()
            self.update_buttons()
            self.update_zoom_level()
            self.document.preview.connect('pdf_changed', self.on_pdf_changed)
            self.document.preview.connect('position_changed', self.on_position_changed)
            self.document.preview.connect('layout_changed', self.on_layout_changed)
            self.document.preview.connect('pdf_stale_changed', self.on_pdf_stale_changed)
            self.document.preview.zoom_manager.connect('zoom_level_changed', self.on_zoom_level_changed)
            self.view.set_stale_banner_visible(self.document.preview.pdf_is_stale)
            self._attach_target_bar(self.document.preview.view)

    def on_pdf_changed(self, preview):
        self.update_label()
        self.update_buttons()

    def on_position_changed(self, preview):
        self.update_label_debounced()

    def on_layout_changed(self, preview):
        self.update_label()

    def on_pdf_stale_changed(self, preview):
        # 构建失败未产出 PDF 时显示横幅；下次构建成功（set_pdf_filename 清除
        # stale）时隐藏。由 preview.set_pdf_is_stale 触发。
        self.view.set_stale_banner_visible(preview.pdf_is_stale)

    def on_zoom_level_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self.update_zoom_level()

    def update_label_debounced(self):
        '''滚动时 position_changed 高频触发（每帧）。页码标签只需秒级精度，
        用 150ms debounce 合并连续滚动事件，避免对 500 页 PDF 频繁调用
        get_n_pages / get_page_by_offset。'''
        if self._label_update_timeout_id is not None:
            GLib.source_remove(self._label_update_timeout_id)
        self._label_update_timeout_id = GLib.timeout_add(150, self._do_update_label)

    def _do_update_label(self):
        self._label_update_timeout_id = None
        self.update_label()
        return False

    def update_label(self):
        if self.document == None:
            self.view.page_spin.set_visible(False)
            self.view.paging_of_label.set_visible(False)
        else:
            self.view.page_spin.set_visible(True)
            self.view.paging_of_label.set_visible(True)
            preview = self.document.preview
            if preview.poppler_document != None:
                total = preview.poppler_document.get_n_pages()
                if preview.layout != None:
                    offset = preview.view.content.scrolling_offset_y
                    current = preview.layout.get_page_by_offset(offset)
                else:
                    current = 1
                self.view.page_spin.set_range(1, max(total, 1))
                self.view.page_spin.set_value(current)
                self.view.paging_of_label.set_text(_('of ') + str(total))
            else:
                self.view.paging_of_label.set_text(_('No preview'))

    def update_buttons(self):
        self.document = self.workspace.get_root_or_active_latex_document()
        has_pdf = self.document != None and self.document.preview.poppler_document != None

        self.view.toolbar.set_visible(True)
        self.view.page_spin.set_visible(True)
        self.view.paging_of_label.set_visible(True)
        self.view.external_viewer_button.set_visible(True)
        self.view.recolor_pdf_toggle.set_visible(True)
        self.view.zoom_out_button.set_visible(True)
        self.view.fit_width_button.set_visible(True)
        self.view.zoom_level_button.set_visible(True)
        self.view.zoom_in_button.set_visible(True)

        self.view.external_viewer_button.set_sensitive(has_pdf)
        self.view.recolor_pdf_toggle.set_sensitive(has_pdf)
        self.view.zoom_out_button.set_sensitive(has_pdf)
        self.view.fit_width_button.set_sensitive(has_pdf)
        self.view.zoom_level_button.set_sensitive(has_pdf)
        self.view.zoom_in_button.set_sensitive(has_pdf)
        self.view.page_spin.set_sensitive(has_pdf)

        if has_pdf:
            self.update_label()
            zoom_level = self.document.preview.zoom_manager.get_zoom_level()
            self.view.zoom_in_button.set_sensitive(zoom_level != None and zoom_level < 4)
            self.view.zoom_out_button.set_sensitive(zoom_level != None and zoom_level > 0.25)
        else:
            self.view.paging_of_label.set_text(_('No preview'))

    def update_zoom_level(self):
        zoom_level = self.document.preview.zoom_manager.get_zoom_level()

        if zoom_level != None:
            self.view.zoom_level_label.set_text('{0:.1f}%'.format(zoom_level * 100))

    def _on_page_spin_activate(self, spin_button):
        page_number = int(spin_button.get_value())
        if self.document == None:
            return
        preview = self.document.preview
        if preview.layout == None or preview.poppler_document == None:
            return
        total = preview.poppler_document.get_n_pages()
        page_number = max(1, min(page_number, total))
        content = preview.view.content
        step = preview.layout.page_height + preview.layout.page_gap
        y = (page_number - 1) * step
        preview.scroll_to_position(content.scrolling_offset_x, y)

    def _on_fit_width_clicked(self, button):
        if self.document == None:
            return
        self.document.preview.zoom_manager.set_zoom_fit_to_width_auto_offset()

    def _sync_switch_icons(self):
        '''按当前显示的面板，把两个 switch 按钮的图标设为"目标面板"图标。
        预览模式 → 显示 Help 图标（点击去 Help）；Help 模式 → 显示 PDF 图标。'''
        if self.main_window.preview_help_stack.get_visible_child_name() == 'preview':
            icon = 'help-browser-symbolic'
        else:
            icon = 'view-paged-symbolic'
        self.view.switch_button.get_child().set_from_icon_name(icon)
        self.main_window.help_panel.switch_button.get_child().set_from_icon_name(icon)

    def _on_switch_clicked(self, button):
        # 以 preview_help_stack 当前可见面板为唯一真相来源，决定切换到哪个、
        # 以及按钮图标应展示的目标面板。不依赖独立的 _is_preview 布尔，
        # 避免快捷键 / 状态恢复等其它切换路径导致布尔与实际显示失同步。
        if self.main_window.preview_help_stack.get_visible_child_name() == 'preview':
            # 当前预览 → 切到 Help，按钮图标展示目标（Help）
            self.view.switch_button.get_child().set_from_icon_name('help-browser-symbolic')
            self.main_window.help_panel.switch_button.get_child().set_from_icon_name('help-browser-symbolic')
            self.workspace.set_show_preview_or_help(False, True)
        else:
            # 当前 Help → 切到预览，按钮图标展示目标（PDF）
            self.view.switch_button.get_child().set_from_icon_name('view-paged-symbolic')
            self.main_window.help_panel.switch_button.get_child().set_from_icon_name('view-paged-symbolic')
            self.workspace.set_show_preview_or_help(True, False)

    def _attach_target_bar(self, preview_view):
        revealer = preview_view.target_label_revealer
        parent = revealer.get_parent()
        if parent is not None:
            parent.remove(revealer)
        self.view.target_bar_placeholder.append(revealer)

    def _detach_target_bar(self):
        placeholder = self.view.target_bar_placeholder
        child = placeholder.get_first_child()
        if child is not None:
            placeholder.remove(child)

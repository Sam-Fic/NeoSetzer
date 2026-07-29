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
from gi.repository import Gtk, Gdk, Gio, GLib

import os.path

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget


class FilesSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'folder-symbolic',
            _('No files'),
            _('The main document is always shown. Add \\input{...} or \\include{...} to include other files.')
        )

    def populate(self):
        # 签名 = id(document) + 主文件名 + 各 include 文件名元组。
        # 按键不动 \input/\include 时签名命中，跳过重建。
        doc = self.model.data_provider.document
        signature = (
            id(doc),
            doc.get_displayname(),
            tuple(include['filename'] for include in self.model.includes),
        )
        if not self.populate_if_changed(signature):
            return
        self.clear_rows()

        doc_dir = doc.get_dirname() or ''

        row = self.make_file_row(
            doc.get_displayname(),
            doc_dir,
            'document-open-symbolic',
            0
        )
        row.item_data = ('main', None)
        self.append_row(row)

        for include in self.model.includes:
            row = self.make_file_row(
                include['filename'],
                doc_dir,
                'text-x-generic-symbolic',
                18
            )
            row.item_data = ('include', include)
            self.append_row(row)

        self.set_empty_state_visible(False)
        self._sync_selection_to_accent_row()

    def make_file_row(self, filename, doc_dir, icon_name, indent):
        basename = os.path.basename(filename)
        row = self.make_row(icon_name, basename, indent)
        row.add_css_class('sidebar-file-row')
        row.set_tooltip_text(filename)

        dirname = os.path.dirname(filename)
        if dirname and dirname != doc_dir:
            # 显示相对于文档目录的目录，若无法相对则显示原始目录
            try:
                rel_dir = os.path.relpath(dirname, doc_dir) if doc_dir else dirname
            except ValueError:
                rel_dir = dirname
            if rel_dir and rel_dir != '.':
                row.set_subtitle(rel_dir)

        # Right-click context menu
        row.right_click_gesture = Gtk.GestureClick()
        row.right_click_gesture.set_button(3)
        row.right_click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        row.right_click_gesture.connect('pressed', self._on_row_right_click_pressed, row)
        row.right_click_gesture.connect('released', self._on_row_right_click_released, row)
        row.add_controller(row.right_click_gesture)

        return row

    def _on_row_right_click_pressed(self, gesture, n_press, x, y, row):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_row_right_click_released(self, gesture, n_press, x, y, row):
        item_data = getattr(row, 'item_data', None)
        if item_data is None:
            return

        kind, include = item_data
        workspace = self.model.data_provider.workspace
        if kind == 'main':
            document = self.model.data_provider.document
            filename = document.get_filename()
        else:
            filename = include['filename']
            # include 缓存中的 document 可能过期（期间被打开/关闭），
            # 弹菜单时按 filename 重新解析。
            document = workspace.get_document_by_filename(filename)
        self._show_context_menu(row, kind, document, filename, x, y)

    def _build_menu_model(self, kind, document, filename):
        workspace = self.model.data_provider.workspace
        menu = Gio.Menu()

        # 文件相关：打开所在文件夹（无路径的未保存文档不显示）
        if filename is not None:
            file_section = Gio.Menu()
            file_section.append_item(Gio.MenuItem.new(_('Open Containing Folder'), 'file.open-folder'))
            menu.append_section(None, file_section)

        # 文档相关：另存为 / 主文档设置
        doc_section = Gio.Menu()
        if document is not None:
            doc_section.append_item(Gio.MenuItem.new(_('Save Document As…'), 'file.save-as'))
        if kind == 'main' and document is not None and document == workspace.get_root_document():
            doc_section.append_item(Gio.MenuItem.new(_('Unset Root Document'), 'file.unset-root'))
        else:
            doc_section.append_item(Gio.MenuItem.new(_('Set as Root'), 'file.set-root'))
        menu.append_section(None, doc_section)

        # 关闭（仅对已打开的文档显示）
        if document is not None:
            close_section = Gio.Menu()
            close_section.append_item(Gio.MenuItem.new(_('Close Document'), 'file.close'))
            menu.append_section(None, close_section)

        return menu

    def _build_action_group(self, kind, document, filename):
        action_group = Gio.SimpleActionGroup()

        def add_action(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            action_group.add_action(action)

        add_action('open-folder', lambda a, p: self._open_containing_folder(filename))
        add_action('save-as', lambda a, p: self._save_document_as(document))
        add_action('set-root', lambda a, p: self._set_as_root(document, filename))
        add_action('unset-root', lambda a, p: self._unset_root())
        add_action('close', lambda a, p: self._close_document(document))

        return action_group

    def _show_context_menu(self, row, kind, document, filename, x, y):
        menu_model = self._build_menu_model(kind, document, filename)
        action_group = self._build_action_group(kind, document, filename)

        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)
        popover.insert_action_group('file', action_group)

        popover.set_offset(144, 0)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

        popover.connect('closed', lambda p: p.insert_action_group('file', None))

    def _open_containing_folder(self, filename):
        if filename is None:
            return
        folder = os.path.dirname(filename)
        try:
            folder_uri = GLib.filename_to_uri(folder)
        except Exception:
            return
        Gio.AppInfo.launch_default_for_uri_async(folder_uri, None, None, None, None)

    def _save_document_as(self, document):
        if document is None:
            return
        from setzer.dialogs.dialog_locator import DialogLocator
        DialogLocator.get_dialog('save_document').run(document)

    def _set_as_root(self, document, filename):
        workspace = self.model.data_provider.workspace
        if document is None:
            # include 文件尚未打开：先打开（open_document_by_filename 内部会
            # set_active_document），文件不存在/无法打开时返回 None。
            document = workspace.open_document_by_filename(filename)
        if document is not None and document.is_latex_document():
            workspace.set_one_document_root(document)

    def _unset_root(self):
        self.model.data_provider.workspace.unset_root_document()

    def _close_document(self, document):
        if document is None:
            return
        workspace = self.model.data_provider.workspace
        # 与 actions.close_active_document 一致：先压入重开栈（Ctrl+Shift+T），
        # 有未保存修改时弹确认对话框，复用 actions 的回调处理保存/丢弃。
        workspace.actions.push_closed_document(document.get_filename())
        if document.source_buffer.get_modified():
            from setzer.dialogs.dialog_locator import DialogLocator
            dialog = DialogLocator.get_dialog('close_confirmation')
            dialog.run({'unsaved_document': document}, workspace.actions.close_document_callback)
        else:
            workspace.remove_document(document)

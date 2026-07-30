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
from setzer.app.service_locator import ServiceLocator


class FilesSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'folder-symbolic',
            _('No files'),
            _('The main document is always shown. Add \\input{...} or \\include{...} to include other files.')
        )

        self._register_context_actions()

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

    def _register_context_actions(self):
        '''在 main_window 上注册带 win. 前缀的上下文 action（带文件名 target）。

        把 action 注册到窗口而非 PopoverMenu，可避免 Gtk.PopoverMenu 基于
        menu model 渲染的菜单项在点击时无法解析到 action group 的问题。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        if main_window.lookup_action('file-ctx-open-folder') is not None:
            return  # 已经注册过

        def add(name, callback, param_type=None):
            action = Gio.SimpleAction.new(name, param_type)
            action.connect('activate', callback)
            main_window.add_action(action)

        add('file-ctx-open-folder', self._on_file_ctx_open_folder, GLib.VariantType('s'))
        add('file-ctx-save-as', self._on_file_ctx_save_as, GLib.VariantType('s'))
        add('file-ctx-set-root', self._on_file_ctx_set_root, GLib.VariantType('s'))
        add('file-ctx-unset-root', self._on_file_ctx_unset_root)
        add('file-ctx-close', self._on_file_ctx_close, GLib.VariantType('s'))

    def _on_file_ctx_open_folder(self, action, parameter):
        self._open_containing_folder(parameter.get_string())

    def _on_file_ctx_save_as(self, action, parameter):
        workspace = self.model.data_provider.workspace
        document = workspace.get_document_by_filename(parameter.get_string())
        self._save_document_as(document)

    def _on_file_ctx_set_root(self, action, parameter):
        self._set_as_root(None, parameter.get_string())

    def _on_file_ctx_unset_root(self, action, parameter):
        self._unset_root()

    def _on_file_ctx_close(self, action, parameter):
        workspace = self.model.data_provider.workspace
        document = workspace.get_document_by_filename(parameter.get_string())
        self._close_document(document)

    def _build_menu_model(self, kind, document, filename):
        workspace = self.model.data_provider.workspace
        menu = Gio.Menu()

        # 文件相关：打开所在文件夹（无路径的未保存文档不显示）
        if filename is not None:
            file_section = Gio.Menu()
            item = Gio.MenuItem.new(_('Open Containing Folder'), 'win.file-ctx-open-folder')
            item.set_action_and_target_value('win.file-ctx-open-folder', GLib.Variant('s', filename))
            file_section.append_item(item)
            menu.append_section(None, file_section)

        # 文档相关：另存为 / 主文档设置
        doc_section = Gio.Menu()
        if document is not None:
            item = Gio.MenuItem.new(_('Save Document As…'), 'win.file-ctx-save-as')
            item.set_action_and_target_value('win.file-ctx-save-as', GLib.Variant('s', filename or ''))
            doc_section.append_item(item)
        if kind == 'main' and document is not None and document == workspace.get_root_document():
            item = Gio.MenuItem.new(_('Unset Root Document'), 'win.file-ctx-unset-root')
            doc_section.append_item(item)
        else:
            item = Gio.MenuItem.new(_('Set as Root'), 'win.file-ctx-set-root')
            item.set_action_and_target_value('win.file-ctx-set-root', GLib.Variant('s', filename or ''))
            doc_section.append_item(item)
        menu.append_section(None, doc_section)

        # 关闭（仅对已打开的文档显示）
        if document is not None:
            close_section = Gio.Menu()
            item = Gio.MenuItem.new(_('Close Document'), 'win.file-ctx-close')
            item.set_action_and_target_value('win.file-ctx-close', GLib.Variant('s', filename or ''))
            close_section.append_item(item)
            menu.append_section(None, close_section)

        return menu

    def _show_context_menu(self, row, kind, document, filename, x, y):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()

        menu_model = self._build_menu_model(kind, document, filename)
        if menu_model.get_n_items() == 0:
            return

        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)

        popover.set_offset(144, 0)
        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _open_containing_folder(self, filename):
        if filename is None:
            return
        folder = os.path.dirname(filename)
        if not folder:
            return
        try:
            folder_uri = GLib.filename_to_uri(folder)
        except Exception:
            return
        # 目录 URI 以 '/' 结尾更稳妥，部分文件管理器对非结尾斜杠的目录 URI 处理不一致。
        if not folder_uri.endswith('/'):
            folder_uri += '/'
        # launch_default_for_uri_async(..., None, None, None, None) 在本环境下静默失败，
        # 改用同步版（与预览面板/编译失败对话框一致），失败时不崩溃。
        try:
            Gio.AppInfo.launch_default_for_uri(folder_uri)
        except Exception:
            pass

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

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
# along with this program. If not to, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib

import os.path

import setzer.workspace.sidebar.document_structure_page.sidebar_list_viewgtk as sidebar_list_view
from setzer.app.service_locator import ServiceLocator


class FilesSectionView(sidebar_list_view.SidebarListView):

    # 分组名常量：重建与 setup-menu 共用。
    SECTION_MAIN = 'main'
    SECTION_INCLUDES = 'includes'
    SECTION_BIBS = 'bibs'

    def __init__(self, model):
        sidebar_list_view.SidebarListView.__init__(self, model)
        self.set_placeholder_info(
            'folder-symbolic',
            _('No files'),
            _('The main document is always shown. Add \\input{...} or \\include{...} to include other files.')
        )

        self._register_context_actions()

    def populate(self):
        # 签名 = 主文档文件名与打开状态 + 各 include/bib 文件名与打开状态。
        # 按键不动 \input/\include 或开闭标签页时签名命中，跳过重建。
        doc = self.model.data_provider.document
        main_entry = self.model.data_provider.get_files_main()
        includes = self.model.data_provider.get_files_includes()
        bibliographies = self.model.data_provider.get_bibliographies()
        signature = (
            main_entry['filename'],
            main_entry['document'] is not None,
            tuple((include['filename'], include['document'] is not None)
                  for include in includes),
            tuple((bib['filename'], bib['document'] is not None)
                  for bib in bibliographies),
        )
        if not self.populate_if_changed(signature):
            return
        self.clear_items()

        # 副标题一律相对主文档目录（处于子文件时主文档仍是列表的根）。
        main_filename = main_entry['filename']
        doc_dir = os.path.dirname(main_filename) if main_filename else ''

        # 三个产品级分组替代原 0/18px 两级缩进：主文档 / 包含文件 / 文献。
        # 空分组（无 include/bib）不添加，自然隐藏。
        self.add_section(self.SECTION_MAIN, _('Main Document'))
        main_item = self.make_file_item(
            os.path.basename(main_filename) if main_filename else doc.get_displayname(),
            doc_dir,
            'document-open-symbolic',
            is_open=main_entry['document'] is not None,
        )
        if main_filename is not None:
            # tooltip 补全完整路径（basename 只能到文件名）。
            main_item.set_tooltip(main_filename)
        main_item.item_data = ('main', main_entry)
        self.append_item(self.SECTION_MAIN, main_item)

        if includes:
            self.add_section(self.SECTION_INCLUDES, _('Included Files'))
            for include in includes:
                item = self.make_file_item(
                    include['filename'],
                    doc_dir,
                    'text-x-generic-symbolic',
                    is_open=include['document'] is not None,
                )
                item.item_data = ('include', include)
                self.append_item(self.SECTION_INCLUDES, item)

        if bibliographies:
            self.add_section(self.SECTION_BIBS, _('Bibliographies'))
            for bib in bibliographies:
                item = self.make_file_item(
                    bib['filename'],
                    doc_dir,
                    'document-bibtex-symbolic',
                    is_open=bib['document'] is not None,
                )
                item.item_data = ('include', bib)
                self.append_item(self.SECTION_BIBS, item)

        self.sync_accent_to_active_document()

    def sync_accent_to_active_document(self):
        '''选中项跟随当前活跃文档所在行，与 Document Structure 的光标跟随
        语义一致（structure.py 把光标所在节设为选中）。迁移自原 .accent
        css 方案——Adw.Sidebar 的 selected 即选中状态，程序化对齐。'''
        workspace = self.model.data_provider.workspace
        active_document = workspace.get_active_document()
        active_filename = active_document.get_filename() if active_document is not None else None

        selected_index = None
        for index, item in enumerate(self._items):
            item_data = getattr(item, 'item_data', None)
            row_filename = None
            if item_data is not None:
                kind, entry = item_data
                # main 与 include 的条目都带 filename（未命名主文档为 None）。
                row_filename = entry['filename']
            if active_filename is not None and row_filename is not None \
                    and os.path.abspath(row_filename) == os.path.abspath(active_filename):
                selected_index = index
                break
        if selected_index is not None:
            self.sidebar.set_selected(selected_index)
        else:
            self.select_first()

    def make_file_item(self, filename, doc_dir, icon_name, is_open=False):
        basename = os.path.basename(filename)
        item = self.make_item(basename, icon_name=icon_name, tooltip=filename)
        if is_open:
            # 已在标签页打开的文件加圆点后缀，区分"项目里有"与"正开着"。
            dot = Gtk.Label(label='●')
            dot.add_css_class('sidebar-open-dot')
            item.set_suffix(dot)

        dirname = os.path.dirname(filename)
        if dirname and dirname != doc_dir:
            # 显示相对于文档目录的目录，若无法相对则显示原始目录
            try:
                rel_dir = os.path.relpath(dirname, doc_dir) if doc_dir else dirname
            except ValueError:
                rel_dir = dirname
            if rel_dir and rel_dir != '.':
                item.set_subtitle(rel_dir)

        return item

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

    def _on_setup_menu(self, sidebar, item):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()

        # 菜单按被右键的文件条目动态重建（Adw.Sidebar 的 menu_model 支持
        # 在 setup-menu 里替换；无 item 或空模型时不弹）。
        if item is None or getattr(item, 'item_data', None) is None:
            self.sidebar.set_menu_model(None)
            return

        kind, entry = item.item_data
        workspace = self.model.data_provider.workspace
        filename = entry['filename']
        # entry['document'] 只在构建缓存那一刻正确（期间可能被打开/关闭），
        # 弹菜单时按 filename 重新解析。
        document = workspace.get_document_by_filename(filename) if filename is not None else None
        menu_model = self._build_menu_model(kind, document, filename)
        if menu_model.get_n_items() == 0:
            self.sidebar.set_menu_model(None)
        else:
            self.sidebar.set_menu_model(menu_model)

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
        # 统一走 workspace.remove_document → WorkspacePresenter.on_document_removed
        # → Adw.TabView close-page 协议（在 presenter 里统一做
        # push_closed_document + modified 检查 + confirm 对话框）。这里不
        # 再自行 push/confirm，避免与 tab view 关闭链路重复弹框。
        workspace.remove_document(document)

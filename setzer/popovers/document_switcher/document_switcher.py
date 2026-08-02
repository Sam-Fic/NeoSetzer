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
from gi.repository import Gtk, Gdk, Gio, GLib

import os.path

from setzer.helpers.observable import Observable
from setzer.popovers.document_switcher.document_switcher_viewgtk import DocumentSwitcherView
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.service_locator import ServiceLocator


class DocumentSwitcher(Observable):

    def __init__(self, workspace):
        Observable.__init__(self)
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = DocumentSwitcherView()
        self.view.search_entry.connect('search-changed', self.on_search_changed)

        self.root_selection_mode = False

        # 切换器在 PopoverManager.init 中预创建，其信号处理器
        # （on_modified_changed/on_name_change/on_is_root_changed 等）在文档属性
        # 变化时即触发，绝大多数时候对话框是关闭的。原 update_items 每次都全量
        # 重建 Adw.ActionRow + 重连信号——重建不可见的行纯属浪费。改为：对话框
        # 未显示时仅标记 _dirty 并刷新便宜的按钮灵敏度，show() 时一次性重建。
        self._is_visible = False
        self._dirty = False

        # 右键上下文菜单：每次右键新建 PopoverMenu + Gio.Menu model，
        # 通过 action target = filename 传递被右键的 document。
        self._register_context_actions()

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)

        self.view.dialog.connect('closed', self.on_dialog_closed)
        self.view.set_root_document_row.connect('activated', self.set_selection_mode)
        self.view.unset_root_document_row.connect('activated', self.unset_root_document)
        self.view.cancel_button.connect('clicked', self.activate_normal_mode)

        self.update_items()
        self.update_unset_root_button()

    def _register_context_actions(self):
        '''注册右键菜单用到的 Gio.SimpleAction。

        所有 action 接收 string 参数（被右键文档的 filename）。未保存文档
        filename 为 None，菜单项会被禁用（由 _build_context_menu 根据
        document.get_filename() 判断）。
        '''
        main_window = self.main_window

        def add_action(name, callback):
            action = Gio.SimpleAction.new(name, GLib.VariantType('s'))
            action.connect('activate', callback)
            main_window.add_action(action)

        add_action('tab-ctx-copy-path', self._on_copy_path)
        add_action('tab-ctx-copy-relative-path', self._on_copy_relative_path)
        add_action('tab-ctx-open-folder', self._on_open_containing_folder)
        add_action('tab-ctx-close-others', self._on_close_others)

    def _on_copy_path(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(filename)

    def _on_copy_relative_path(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
            return
        # 相对路径基准：root 文档所在目录（无 root 则取 active document 目录）
        base_dir = None
        root_doc = self.workspace.root_document
        if root_doc is not None and root_doc.get_filename() is not None:
            base_dir = root_doc.get_dirname()
        else:
            active = self.workspace.get_active_document()
            if active is not None and active.get_filename() is not None:
                base_dir = active.get_dirname()
        if base_dir is None:
            relpath = filename
        else:
            try:
                relpath = os.path.relpath(filename, base_dir)
            except ValueError:
                # Windows 跨盘符等情况下 relpath 会抛 ValueError，回滚绝对路径
                relpath = filename
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(relpath)

    def _on_open_containing_folder(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
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

    def _on_close_others(self, action, parameter):
        keep_filename = parameter.get_string()
        # 复制一份列表，避免在迭代中修改 open_documents
        to_close = [d for d in list(self.workspace.open_documents)
                    if d.get_filename() != keep_filename]
        for document in to_close:
            # 仅关闭已保存的文档；有未保存修改的跳过（避免误丢数据，
            # 用户仍可逐个确认关闭）。与 on_close_button_clicked 不同，
            # 这里不弹确认对话框——批量确认对话框是未来增强项。
            if not document.source_buffer.get_modified():
                self.workspace.actions.push_closed_document(document.get_filename())
                self.workspace.remove_document(document)

    def _build_context_menu(self, document):
        '''为指定 document 构建 Gio.Menu model。

        未保存文档（filename 为 None）时不添加文件路径相关项——Gio action
        的 enabled 状态是全局的，无法按 target 单独禁用，故改为条件添加。
        '''
        menu = Gio.Menu()
        filename = document.get_filename()
        has_path = filename is not None

        if has_path:
            section_file = Gio.Menu()
            item_copy_path = Gio.MenuItem.new(_('Copy Path'), 'win.tab-ctx-copy-path')
            item_copy_path.set_action_and_target_value('win.tab-ctx-copy-path',
                                                        GLib.Variant('s', filename))
            section_file.append_item(item_copy_path)

            item_copy_rel = Gio.MenuItem.new(_('Copy Relative Path'), 'win.tab-ctx-copy-relative-path')
            item_copy_rel.set_action_and_target_value('win.tab-ctx-copy-relative-path',
                                                       GLib.Variant('s', filename))
            section_file.append_item(item_copy_rel)

            item_open_folder = Gio.MenuItem.new(_('Open Containing Folder'), 'win.tab-ctx-open-folder')
            item_open_folder.set_action_and_target_value('win.tab-ctx-open-folder',
                                                          GLib.Variant('s', filename))
            section_file.append_item(item_open_folder)
            menu.append_section(None, section_file)

        section_close = Gio.Menu()
        item_close_others = Gio.MenuItem.new(_('Close Others'), 'win.tab-ctx-close-others')
        item_close_others.set_action_and_target_value('win.tab-ctx-close-others',
                                                       GLib.Variant('s', filename or ''))
        section_close.append_item(item_close_others)
        menu.append_section(None, section_close)

        return menu

    def _show_context_menu(self, row, document, x, y):
        '''在右键位置弹出上下文菜单。

        每次右键新建 popover + menu model（document 不同，action target 不同）。
        popover closed 时 unparent 释放对 row 的引用，让 GC 回收。比复用单例
        更简单（无需处理 reparent 警告），创建开销可忽略。
        '''
        menu_model = self._build_context_menu(document)
        popover = Gtk.PopoverMenu()
        popover.set_has_arrow(False)
        popover.set_menu_model(menu_model)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.set_parent(row)
        popover.connect('map', lambda p: p.grab_focus())
        # closed 后 unparent，避免 row 持有已关闭 popover 的引用导致泄漏
        popover.connect('closed', lambda p: p.unparent())
        popover.popup()

    def _on_row_right_click(self, gesture, n_press, x, y):
        '''Gtk.GestureClick pressed 回调：gesture 已 set_button(3)，仅右键触发。'''
        if n_press != 1:
            return
        row = gesture.get_widget()
        document = getattr(row, 'document', None)
        if document is None:
            return
        self._show_context_menu(row, document, int(x), int(y))
        # 与源码编辑器右键菜单（document_controller.py:185）保持一致：
        # 弹出后 reset，结束当前手势序列，避免手势持续 claim 后续事件。
        gesture.reset()

    def open(self):
        self._is_visible = True
        # 打开时清空上次残留的搜索词，避免带着旧过滤条件弹出。
        self.view.query = ''
        self.view.search_entry.set_text('')
        # 若关闭期间有文档属性变化（_dirty），先重建行再展示，保证打开即最新。
        if self._dirty:
            self._dirty = False
            self._rebuild_rows()
        self.view.dialog.present(self.main_window)

    def on_search_changed(self, entry):
        self.view.query = entry.get_text().strip().lower()
        self.update_items()

    def _rebuild_rows(self):
        '''全量重建行 + 重连信号。仅在对话框可见或即将展示时调用。'''
        self.view.update_items(self.workspace.open_documents, self.root_selection_mode, self.workspace.get_active_document())
        # (re)wire row + close-button handlers for the freshly built rows
        for row in self.view.rows:
            row.connect('activated', self.on_row_activated)
            row.close_button.connect('clicked', self.on_close_button_clicked)
            # 右键上下文菜单：set_button(3) 使 gesture 仅响应右键，左键事件
            # 不被 claim、正常传播到 row 的 activated 信号。每行一个 gesture
            # （GTK4 controller 不能跨 widget 共享）。
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            # 与源码编辑器右键菜单（document_controller.py:68）保持一致：
            # TARGET 阶段处理，仅 claim 右键（set_button(3)），左键事件正常
            # 传播到 row 的 activated 信号。
            gesture.set_propagation_phase(Gtk.PropagationPhase.TARGET)
            gesture.connect('pressed', self._on_row_right_click)
            row.add_controller(gesture)

    def on_new_document(self, workspace, document):
        document.connect('filename_change', self.on_name_change)
        document.connect('displayname_change', self.on_name_change)
        document.connect('modified_changed', self.on_modified_changed)
        document.connect('is_root_changed', self.on_is_root_changed)
        self.update_items()

    def on_document_removed(self, workspace, document):
        document.disconnect('filename_change', self.on_name_change)
        document.disconnect('displayname_change', self.on_name_change)
        document.disconnect('modified_changed', self.on_modified_changed)
        document.disconnect('is_root_changed', self.on_is_root_changed)
        self.update_items()

    def on_new_active_document(self, workspace, document):
        self.update_items()

    def on_name_change(self, document, name=None):
        self.update_items()

    def on_is_root_changed(self, document, is_root):
        self.update_items()
        self.update_unset_root_button()

    def on_root_state_changed(self, workspace, state):
        self.update_unset_root_button()

    def on_modified_changed(self, document):
        self.update_items()

    def on_row_activated(self, row):
        if row is None:
            return
        document = row.document
        if self.root_selection_mode:
            self.workspace.set_one_document_root(document)
            self.activate_normal_mode()
        else:
            self.workspace.set_active_document(document)
            self.view.dialog.close()

    def on_close_button_clicked(self, button):
        row = button.row
        document = row.document
        # 与 actions.close_active_document 一致：在确认对话框之前压入重开栈，
        # 使 Ctrl+Shift+T 对切换器关闭路径同样生效。push_closed_document 内部
        # 仅对已保存到磁盘的文档入栈，未保存文档会被忽略。
        self.workspace.actions.push_closed_document(document.get_filename())
        if document.source_buffer.get_modified():
            # 记录被关闭文档是否为当前活动文档，用于决定是否需要重显切换器。
            # 不在此处预先 set_active_document：该调用会在确认对话框弹出前
            # 同步切换视图，造成用户可见的跳变。确认对话框已显示文档名，
            # 用户无需看到文档内容即可决定是否保存。
            is_active = (document == self.workspace.get_active_document())

            self.view.dialog.close()
            dialog = DialogLocator.get_dialog('close_confirmation')
            dialog.run({'unsaved_document': document, 'is_active': is_active}, self.on_close_document_callback)
        else:
            if document == self.workspace.get_active_document():
                self.view.dialog.close()
            self.workspace.remove_document(document)

    def on_close_document_callback(self, parameters):
        is_active = parameters['is_active']

        if parameters['response'] == 0:
            self.workspace.remove_document(parameters['unsaved_document'])
        elif parameters['response'] == 2:
            document = parameters['unsaved_document']
            if document.get_filename() == None:
                # 新文档无路径：设置为活动态以承载文件选择器视图，
                # 保存对话框关闭后通过回调重新展示切换器（用户可再次尝试关闭）。
                self.workspace.set_active_document(document)
                DialogLocator.get_dialog('save_document').run(
                    document, self._on_save_new_document_callback, parameters)
                return
            else:
                if document.save_to_disk():
                    self.workspace.remove_document(document)
                else:
                    # 保存失败：保持文档打开，toast 已由 save_to_disk 弹出
                    if is_active:
                        self.workspace.set_active_document(document)
                    self._show_switcher()
                    return

        # 非活动文档被关闭 / 取消时重显切换器；
        # 活动文档被关闭时，remove_document 已自动切换到其他文档，不重显。
        if not is_active or parameters['response'] == 1:
            self._show_switcher()

    def _on_save_new_document_callback(self, parameters):
        # 用户在保存对话框完成（含取消）后，重新展示切换器让用户再次尝试关闭。
        self._show_switcher()

    def _show_switcher(self):
        # close() 已触发 on_dialog_closed 把 _is_visible 置 False；重新展示前
        # 恢复标志并按 dirty 重建（关闭期间 remove_document 等已标记 dirty）。
        self._is_visible = True
        if self._dirty:
            self._dirty = False
            self._rebuild_rows()
        self.view.dialog.present(self.main_window)

    def on_dialog_closed(self, dialog=None):
        self._is_visible = False
        active_document = self.workspace.get_active_document()
        if active_document != None:
            active_document.view.source_view.grab_focus()
        self.activate_normal_mode()

    def update_items(self):
        # 对话框未显示时跳过昂贵的行重建，仅标记 dirty 等下次 show() 重建，
        # 并刷新便宜的「Set as Root」按钮灵敏度（该按钮在 headerbar 常驻）。
        if not self._is_visible:
            self._dirty = True
            self.activate_set_root_document_button()
            return
        self._dirty = False
        self._rebuild_rows()
        self.activate_set_root_document_button()

    def iter_rows(self):
        return list(self.view.rows)

    def set_selection_mode(self, action, parameter=None):
        self.activate_selection_mode()

    def unset_root_document(self, action, parameter=None):
        self.workspace.unset_root_document()
        self.activate_normal_mode()

    def activate_normal_mode(self, button=None):
        self.root_selection_mode = False
        self.view.dialog.set_title(_('Open Documents'))
        self.activate_set_root_document_button()
        self.update_unset_root_button()
        self.view.explanation_group.set_visible(False)
        self.update_items()

    def activate_selection_mode(self):
        self.root_selection_mode = True
        self.view.dialog.set_title(_('Select Root Document'))
        self.view.set_root_document_row.set_sensitive(False)
        self.view.unset_root_document_row.set_sensitive(True)
        self.view.explanation_group.set_visible(True)
        self.update_items()

    def activate_set_root_document_button(self):
        if len(self.workspace.open_latex_documents) > 0:
            self.view.set_root_document_row.set_sensitive(True)
        else:
            self.view.set_root_document_row.set_sensitive(False)

    def update_unset_root_button(self):
        if self.workspace.root_document is not None:
            self.view.unset_root_document_row.set_sensitive(True)
        else:
            self.view.unset_root_document_row.set_sensitive(False)

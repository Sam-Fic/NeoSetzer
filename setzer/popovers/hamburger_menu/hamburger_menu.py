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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GLib, Gio
import os.path

from setzer.keyboard_shortcuts import shortcut_tooltips
from setzer.popovers.theme_selector.theme_selector import ThemeSelector


class HamburgerMenu(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = self.workspace.main_window if hasattr(self.workspace, 'main_window') else None

        self.menu_model = Gio.Menu()

        self.build_static_items()
        self.build_session_submenu()
        self.build_recent_documents_submenu()

        self.register_actions()

        self.workspace.connect('update_recently_opened_session_files', self.on_update_recently_opened_session_files)
        self.workspace.connect('update_recently_opened_documents', self.on_update_recently_opened_documents)

        # recent 列表签名缓存：update_recently_opened_documents 在每次打开/关闭
        # 文档时触发。若列表内容（filename+date）未变（如打开一个已在列表中的
        # 文档仅刷新其 date 之外无变化），签名短路避免 remove_all + 重建 10 个
        # Gio.MenuItem。session files 同理。
        self._recent_documents_signature = None
        self._recent_sessions_signature = None

    def register_actions(self):
        main_window = ServiceLocator_get_main_window()
        # Restore Previous Session: open the session file chooser
        action_restore = Gio.SimpleAction.new('restore-session', None)
        action_restore.connect('activate', self.on_restore_session_click, None)
        main_window.add_action(action_restore)
        # Open a specific recent session file
        action_open = Gio.SimpleAction.new('open-session-file', GLib.VariantType('s'))
        action_open.connect('activate', self.on_restore_session_click)
        main_window.add_action(action_open)
        # Open a specific recent document
        action_open_recent = Gio.SimpleAction.new('open-recent-document', GLib.VariantType('s'))
        action_open_recent.connect('activate', self.on_open_recent_document)
        main_window.add_action(action_open_recent)

    def build_static_items(self):

        # 移植自 GNOME Builder：在菜单最顶部放置主题快速切换器。
        # GTK4 的做法是在菜单模型里占一个带 custom 属性的空项，
        # 之后用 popover.add_child(widget, name) 把真实 widget 渲染进去。
        # 单独用一个 section 包裹，使其与下方菜单项之间自动出现分隔线。
        section_theme = Gio.Menu()
        theme_item = Gio.MenuItem.new(None, None)
        theme_item.set_attribute_value('custom', GLib.Variant('s', 'theme_selector'))
        section_theme.append_item(theme_item)
        self.menu_model.append_section(None, section_theme)

        # GMenu 的分隔线由 section 自动渲染：每个 append_section 之间出一条标准分隔线，
        # 不要用自定义 role=separator 的 MenuItem（会被 Gtk 渲染成空白行=空白项 bug）。
        section_save = Gio.Menu()
        # Save Document（win.save）：补回普通 Save 项。原 section 只有 Save As / Save All，
        # 缺普通 Save——窄窗 headerbar compact 模式隐藏 save 按钮后，用户仍可从这里保存
        # （Ctrl+S 兜底，菜单项提升可发现性）。
        save_doc = Gio.MenuItem.new(_('Save Document'), 'win.save')
        save_doc.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>s'))
        section_save.append_item(save_doc)
        save_as = Gio.MenuItem.new(_('Save Document As…'), 'win.save-as')
        save_as.set_attribute_value('accel', GLib.Variant('s', '<Ctrl><Shift>s'))
        section_save.append_item(save_as)
        section_save.append(_('Save All Documents'), 'win.save-all')
        # Export PDF As…：把 build 生成的 PDF 另存到其他位置（副本，不改源文件）。
        section_save.append(_('Export PDF As…'), 'win.export-pdf-as')
        print_item = Gio.MenuItem.new(_('Print…'), 'win.print')
        print_item.set_attribute_value('accel', GLib.Variant('s', '<Control>p'))
        section_save.append_item(print_item)
        self.menu_model.append_section(None, section_save)

        self.recent_documents_section = Gio.Menu()
        self.menu_model.append_submenu(_('Recent Documents'), self.recent_documents_section)

        self.session_section = Gio.Menu()
        self.menu_model.append_submenu(_('Session'), self.session_section)

        section_commands = Gio.Menu()
        command_palette = Gio.MenuItem.new(_('Command Palette'), 'win.show-command-palette')
        command_palette.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>.'))
        section_commands.append_item(command_palette)
        self.menu_model.append_section(None, section_commands)

        section_prefs = Gio.Menu()
        prefs = Gio.MenuItem.new(_('Preferences'), 'win.show-preferences-dialog')
        prefs.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>comma'))
        section_prefs.append_item(prefs)
        doc_props = Gio.MenuItem.new(_('Document Properties'), 'win.show-document-properties')
        section_prefs.append_item(doc_props)
        self.menu_model.append_section(None, section_prefs)

        section_help = Gio.Menu()
        fullscreen_item = Gio.MenuItem.new(_('Toggle Fullscreen'), 'win.toggle-fullscreen')
        fullscreen_item.set_attribute_value('accel', GLib.Variant('s', 'F11'))
        section_help.append_item(fullscreen_item)
        shortcuts = Gio.MenuItem.new(_('Keyboard Shortcuts'), 'win.show-shortcuts-dialog')
        shortcuts.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>question'))
        section_help.append_item(shortcuts)
        about = Gio.MenuItem.new(_('About'), 'win.show-about-dialog')
        # 不设 accel 提示：show_about_dialog 现无默认快捷键（About 对话框菜单可达），
        # 若仍写死 Ctrl+Shift+H 会与默认行为不一致、误导用户。
        section_help.append_item(about)
        self.menu_model.append_section(None, section_help)

        section_close = Gio.Menu()
        close_all = Gio.MenuItem.new(_('Close All Documents'), 'win.close-all-documents')
        close_all.set_attribute_value('accel', GLib.Variant('s', '<Ctrl><Shift>w'))
        section_close.append_item(close_all)
        close_doc = Gio.MenuItem.new(_('Close Document'), 'win.close-active-document')
        close_doc.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>w'))
        section_close.append_item(close_doc)
        # Reopen Closed Document：原只有 Ctrl+Shift+T 快捷键、无菜单入口，可发现性为零。
        # 复用 actions 已注册的 win.reopen-last-closed-document action（enabled 状态
        # 由 update_actions 根据 _closed_document_stack 是否为空管理）。放在 Close 旁边
        # 语义最近，Ctrl+Shift+T accel 与 shortcut_controller_app.py 注册的一致。
        reopen_item = Gio.MenuItem.new(_('Reopen Closed Document'), 'win.reopen-last-closed-document')
        reopen_item.set_attribute_value('accel', GLib.Variant('s', '<Ctrl><Shift>t'))
        section_close.append_item(reopen_item)
        quit_item = Gio.MenuItem.new(_('Quit'), 'win.quit')
        quit_item.set_attribute_value('accel', GLib.Variant('s', '<Ctrl>q'))
        section_close.append_item(quit_item)
        self.menu_model.append_section(None, section_close)

    def build_session_submenu(self):
        self.session_section.remove_all()
        restore = Gio.MenuItem.new(_('Restore Previous Session…'), 'win.restore-session')
        restore.set_attribute_value('accel', GLib.Variant('s', '<Ctrl><Shift>j'))
        self.session_section.append_item(restore)
        self.session_section.append(_('Save Current Session…'), 'win.save-session')
        # Recent Sessions 作为 labeled section，自带分隔线，无需手动 separator
        self.recent_section = Gio.Menu()
        self.recent_item = Gio.MenuItem.new_section(_('Recent Sessions'), self.recent_section)
        self.session_section.append_item(self.recent_item)

    def build_recent_documents_submenu(self):
        self.recent_documents_section.remove_all()

    def on_update_recently_opened_documents(self, workspace, recently_opened_documents):
        items = list(recently_opened_documents.values())
        # 仅取前 10（与展示一致）参与签名，避免对完整列表排序后再比较。
        top_items = sorted(items, key=lambda val: val['date'], reverse=True)[:10]
        signature = tuple((item['filename'], item['date']) for item in top_items)
        if signature == self._recent_documents_signature:
            return
        self._recent_documents_signature = signature

        self.recent_documents_section.remove_all()
        for item in top_items:
            filename = item['filename']
            displayname = os.path.basename(filename)
            menu_item = Gio.MenuItem.new(displayname, 'win.open-recent-document')
            menu_item.set_action_and_target_value('win.open-recent-document', GLib.Variant('s', filename))
            self.recent_documents_section.append_item(menu_item)

    def on_open_recent_document(self, action, parameter):
        filename = parameter.unpack()
        if filename:
            if not os.path.isfile(filename):
                self.workspace.remove_recently_opened_document(filename, notify=True)
                return
            self.workspace.open_document_by_filename_with_spinner(filename)

    def on_update_recently_opened_session_files(self, workspace, recently_opened_session_files):
        items = list(recently_opened_session_files.values())
        # 菜单里用 basename（避免一长串路径）；展示全部（最多 15 个，受容量上限约束）。
        sorted_items = sorted(items, key=lambda val: val['date'], reverse=True)
        signature = tuple((item['filename'], item['date']) for item in sorted_items)
        if signature == self._recent_sessions_signature:
            return
        self._recent_sessions_signature = signature

        self.recent_section.remove_all()
        for item in sorted_items:
            filename = item['filename']
            displayname = os.path.basename(filename)
            menu_item = Gio.MenuItem.new(displayname, 'win.open-session-file')
            menu_item.set_action_and_target_value('win.open-session-file', GLib.Variant('s', filename))
            self.recent_section.append_item(menu_item)

    def get_menu_button(self):
        button = Gtk.MenuButton()
        button.set_icon_name('open-menu-symbolic')
        shortcut_tooltips.set_tooltip(button, _('Main Menu'), 'hamburger_menu')
        button.set_menu_model(self.menu_model)
        popover = button.get_popover()
        if popover is not None:
            popover.add_css_class('menu')
            # 仿 GNOME Builder：在 popover 顶部注入 ThemeSelector 自定义 widget
            # 使用 add_child() 将 widget 绑定到菜单模型的 custom 属性
            self.theme_selector = ThemeSelector()
            popover.add_child(self.theme_selector, 'theme_selector')
            # 监听设置变化，更新 ThemeSelector 选中状态
            settings = ServiceLocator_get_settings()
            settings.connect('settings_changed', self.theme_selector.update_from_settings)
        return button

    def on_restore_session_click(self, action, parameter):
        from setzer.dialogs.dialog_locator import DialogLocator
        if parameter is None:
            DialogLocator.get_dialog('open_session').run(self.restore_session_cb)
        else:
            filename = parameter.unpack()
            self.restore_session_cb(filename)

    def restore_session_cb(self, filename):
        if filename == None: return

        from setzer.dialogs.dialog_locator import DialogLocator
        unsaved_documents = self.workspace.get_unsaved_documents()
        if len(unsaved_documents) > 0:
            self.workspace.set_active_document(unsaved_documents[0])
            dialog = DialogLocator.get_dialog('close_confirmation')
            # 传 'documents' 字段：≥2 个未保存文档时弹批量对话框（多文档路径用批量）。
            dialog.run({'unsaved_document': unsaved_documents[0], 'documents': unsaved_documents, 'session_filename': filename}, self.close_confirmation_cb)
        else:
            # 先尝试加载；仅当加载成功才关闭旧文档，避免坏 session 文件导致
            # 旧文档被关、用户两手空空。加载失败（load 已弹 toast）则回滚加载
            # 过程中可能已部分添加的文档，保持旧文档不变。
            # 显示 spinner 并延迟 200ms 执行重操作，让 spinner 先渲染。
            main_window = ServiceLocator_get_main_window()
            if main_window is not None and hasattr(main_window, 'show_loading_spinner'):
                main_window.show_loading_spinner()
                GLib.timeout_add(200, self._do_restore_session, filename)
            else:
                self._do_restore_session(filename)

    def _do_restore_session(self, filename):
        '''timeout 回调：spinner 渲染后执行会话加载 + 旧文档清理。'''
        old_documents = set(self.workspace.get_all_documents())
        if self.workspace.load_documents_from_session_file(filename):
            for document in old_documents:
                self.workspace.remove_document(document)
        else:
            for document in set(self.workspace.get_all_documents()) - old_documents:
                self.workspace.remove_document(document)
            # 加载失败：未触发 set_active_document，显式 hide 兜底。
            main_window = ServiceLocator_get_main_window()
            if main_window is not None and hasattr(main_window, 'hide_loading_spinner'):
                main_window.hide_loading_spinner()
        return False

    def close_confirmation_cb(self, parameters):
        from setzer.dialogs.dialog_locator import DialogLocator
        document = parameters['unsaved_document']
        unsaved_documents = parameters['documents']
        session_filename = parameters['session_filename']
        response = parameters['response']

        if response == 0:  # discard (单)：移除当前，继续恢复会话
            self.workspace.remove_document(document)
            self.restore_session_cb(session_filename)
        elif response == 4:  # discard_all (批量)：移除所有未保存，继续恢复会话
            for d in list(unsaved_documents):
                self.workspace.remove_document(d)
            self.restore_session_cb(session_filename)
        elif response == 2:  # save (单)
            if document.get_filename() == None:
                DialogLocator.get_dialog('save_document').run(document, self.restore_session_cb, session_filename)
            else:
                if document.save_to_disk():
                    self.restore_session_cb(session_filename)
                # 保存失败：不恢复会话，toast 已弹出
        elif response == 3:  # save_all (批量)
            # 保存所有有 filename 的；无 filename 的逐个弹 save_document。
            # save_all_processed 跟踪已提示的 untitled，避免取消时无限循环。
            for d in list(unsaved_documents):
                if d.get_filename() is not None:
                    d.save_to_disk()
            parameters['save_all_processed'] = set()
            untitled = [d for d in unsaved_documents if d.get_filename() is None]
            if untitled:
                first = untitled[0]
                parameters['save_all_processed'].add(id(first))
                self.workspace.set_active_document(first)
                parameters['unsaved_document'] = first
                DialogLocator.get_dialog('save_document').run(first, self.restore_session_save_all_cb, parameters)
            else:
                self.restore_session_cb(session_filename)

    def restore_session_save_all_cb(self, parameters):
        '''save_all 模式下逐个 untitled 文档的 save_document 回调。'''
        from setzer.dialogs.dialog_locator import DialogLocator
        unsaved_documents = parameters['documents']
        session_filename = parameters['session_filename']
        processed = parameters.get('save_all_processed', set())
        untitled = [d for d in unsaved_documents
                    if d.get_filename() is None and id(d) not in processed]
        if untitled:
            next_doc = untitled[0]
            processed.add(id(next_doc))
            self.workspace.set_active_document(next_doc)
            parameters['unsaved_document'] = next_doc
            DialogLocator.get_dialog('save_document').run(next_doc, self.restore_session_save_all_cb, parameters)
        else:
            # 所有 untitled 已提示过，继续恢复会话（仍有未保存的会再次弹批量对话框）
            self.restore_session_cb(session_filename)


def ServiceLocator_get_main_window():
    from setzer.app.service_locator import ServiceLocator
    return ServiceLocator.get_main_window()


def ServiceLocator_get_settings():
    from setzer.app.service_locator import ServiceLocator
    return ServiceLocator.get_settings()

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
from gi.repository import Gtk
from gi.repository import Gio
from gi.repository import GLib

import os.path

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.popovers.popover_manager import PopoverManager


class Headerbar(object):
    
    def __init__(self, workspace):
        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().headerbar

        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('update_recently_opened_documents', self.on_update_recently_opened_documents)
        self.workspace.connect('root_state_change', self.on_root_state_change)

        # Initialize the correct Open button visibility now. The signal may
        # have been emitted before this controller was constructed, leaving
        # both buttons visible by default.
        self.on_update_recently_opened_documents(None, self.workspace.recently_opened_documents)

        self.activate_welcome_screen_mode()

        # Compact 模式：窄窗（<700px breakpoint）时隐藏 save / help 按钮（有 Ctrl+S、
        # F1 兜底），缓解 headerbar 在 360px 下的按钮溢出。不能直接用
        # Adw.Breakpoint.add_setter(visible)：本 presenter 频繁 set_visible 这些按钮
        # （welcome/document 模式切换、show/hide_*_toggles），add_setter 会被覆盖。
        # 通过 _compact 标志在 show 路径末尾覆盖隐藏；set_compact 重跑当前模式生效/恢复。
        # F1/F9 直接操作 toggle 的 set_active（见 shortcut_controller_app），不受
        # set_visible 影响，故隐藏 help_toggle 不会困住用户。
        self._compact = False
        main_window = ServiceLocator.get_main_window()
        main_window.connect('notify::current-breakpoint', self._on_breakpoint_change)
        # 同步初始状态（窗口启动时可能已在窄窗，breakpoint 已 apply）
        self._on_breakpoint_change(main_window, None)

    def on_document_removed(self, workspace, document):
        if self.workspace.active_document == None:
            self.set_build_button_state()
            self.activate_welcome_screen_mode()

    def on_new_active_document(self, workspace, document):
        self.set_build_button_state()
        self.activate_document_mode()
        self.show_document_name(document)
        self.update_toggles()

        document.connect('filename_change', self.on_name_change)
        document.connect('displayname_change', self.on_name_change)
        document.connect('modified_changed', self.on_modified_changed)

    def on_new_inactive_document(self, workspace, document):
        document.disconnect('filename_change', self.on_name_change)
        document.disconnect('displayname_change', self.on_name_change)
        document.disconnect('modified_changed', self.on_modified_changed)

    def on_root_state_change(self, workspace, state):
        self.set_build_button_state()
        self.update_toggles()

    def on_name_change(self, document, name=None):
        self.show_document_name(document)

    def on_modified_changed(self, document):
        self.show_document_name(document)

    def on_update_recently_opened_documents(self, workspace, recently_opened_documents):
        if self.workspace.active_document is None:
            self.view.open_document_button.set_visible(False)
            self.view.open_document_blank_button.set_visible(False)
            return
        data = recently_opened_documents.values()
        if len(data) > 0:
            self.view.open_document_button.set_sensitive(True)
            self.view.open_document_button.set_visible(True)
            self.view.open_document_blank_button.set_visible(False)
        else:
            self.view.open_document_button.set_sensitive(False)
            self.view.open_document_button.set_visible(False)
            self.view.open_document_blank_button.set_visible(True)

    def set_build_button_state(self):
        document = self.workspace.get_root_or_active_latex_document()

        if document != None:
            current = self.view.build_wrapper.get_first_child()
            # 如果当前已显示的就是目标文档的 build_widget，跳过 remove+append。
            # 避免不必要的 widget 重建（每次根文档变化时都会调用此方法）。
            if current is not document.build_widget.view:
                if current is not None:
                    self.view.build_wrapper.remove(current)
                self.view.build_wrapper.append(document.build_widget.view)
        else:
            if self.view.build_wrapper.get_first_child() is not None:
                self.view.build_wrapper.remove(self.view.build_wrapper.get_first_child())

    def activate_welcome_screen_mode(self):
        self.hide_sidebar_toggles()
        self.hide_preview_help_toggles()
        self.view.save_document_button.set_visible(False)
        self.view.open_document_button.set_visible(False)
        self.view.open_document_blank_button.set_visible(False)
        self.view.new_document_button.set_visible(False)
        self.view.center_button.set_sensitive(False)
        self.view.center_widget.set_visible_child_name('welcome')
        self.view.widget.add_css_class('welcome')

    def activate_document_mode(self):
        self.view.save_document_button.set_visible(True)
        self.view.new_document_button.set_visible(True)
        self.on_update_recently_opened_documents(None, self.workspace.recently_opened_documents)
        self.view.center_button.set_sensitive(True)
        self.view.center_widget.set_visible_child_name('button')
        self.view.widget.remove_css_class('welcome')
        # compact 覆盖：窄窗隐藏 save（Ctrl+S 兜底，hamburger 有 Save Document 项）
        if self._compact:
            self.view.save_document_button.set_visible(False)

    def show_document_name(self, document):
        mod_text = '*' if document.source_buffer.get_modified() else ''
        self.view.document_title.set_title(document.get_basename() + mod_text)
        dirname = document.get_dirname()
        if dirname != '':
            folder_text = dirname.replace(os.path.expanduser('~'), '~')
            self.view.document_title.set_subtitle(folder_text)
        else:
            self.view.document_title.set_subtitle('')

    def update_toggles(self):
        if self.workspace.get_active_latex_document():
            self.show_sidebar_toggles()
        else:
            self.hide_sidebar_toggles()

        if self.workspace.get_root_or_active_latex_document():
            self.show_preview_help_toggles()
        else:
            self.hide_preview_help_toggles()

    def hide_sidebar_toggles(self):
        # sidebar toggles are packed directly into the Adw.HeaderBar (no
        # wrapping box), so toggle visibility on each widget directly —
        # mirrors the preview/help toggle handling below.
        self.view.document_structure_toggle.set_visible(False)
        self.view.document_structure_toggle.set_sensitive(False)
        self.view.symbols_toggle.set_visible(False)
        self.view.symbols_toggle.set_sensitive(False)

    def hide_preview_help_toggles(self):
        self.view.preview_toggle.set_visible(False)
        self.view.preview_toggle.set_sensitive(False)
        self.view.help_toggle.set_visible(False)
        self.view.help_toggle.set_sensitive(False)

    def show_sidebar_toggles(self):
        self.view.document_structure_toggle.set_visible(True)
        self.view.document_structure_toggle.set_sensitive(True)
        self.view.symbols_toggle.set_visible(True)
        self.view.symbols_toggle.set_sensitive(True)

    def show_preview_help_toggles(self):
        self.view.preview_toggle.set_visible(True)
        self.view.preview_toggle.set_sensitive(True)
        self.view.help_toggle.set_visible(True)
        self.view.help_toggle.set_sensitive(True)
        # compact 覆盖：窄窗隐藏 help（F1 直接 set_active 兜底，隐藏后仍可切换）
        if self._compact:
            self.view.help_toggle.set_visible(False)

    def set_compact(self, compact):
        '''窄窗 compact 模式开关。设标志后重跑当前模式的可见性逻辑，
        让 activate_document_mode / show_preview_help_toggles 末尾的 compact
        覆盖生效（compact=True）或恢复（compact=False）。幂等。

        不能只 set_visible：welcome/document 模式与 toggle 状态共同决定可见性，
        必须重跑对应路径以保证 save/help 与其它按钮状态一致。'''
        if self._compact == compact:
            return
        self._compact = compact
        if self.workspace.active_document is not None:
            self.activate_document_mode()
        else:
            self.activate_welcome_screen_mode()
        self.update_toggles()

    def _on_breakpoint_change(self, window, pspec):
        '''notify::current-breakpoint 回调：当前 breakpoint 为 narrow_breakpoint
        时进入 compact 模式，否则（含 None=宽窗）退出。'''
        bp = window.get_current_breakpoint()
        narrow = getattr(window, 'narrow_breakpoint', None)
        self.set_compact(bp is not None and bp is narrow)



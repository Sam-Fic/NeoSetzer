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

from setzer.app.service_locator import ServiceLocator
from setzer.popovers.popover_manager import PopoverManager
from setzer.keyboard_shortcuts.shortcut_controller_app import ShortcutControllerApp
from setzer.keyboard_shortcuts.shortcut_controller_document import ShortcutControllerDocument
from setzer.keyboard_shortcuts.shortcut_controller_latex import ShortcutControllerLaTeX


class Shortcuts(object):

    def __init__(self):
        self.main_window = ServiceLocator.get_main_window()
        self.workspace = ServiceLocator.get_workspace()

        self.shortcut_controller_app = ShortcutControllerApp()

        self.main_window.add_controller(self.shortcut_controller_app)
        for document in self.workspace.open_documents: self.setup_document_shortcuts(document)
        self.workspace.connect('new_document', self.on_new_document)

        # 引用计数：多个 popover 可同时打开（如 preview_zoom_level 未关时
        # 右键 source_view 打开 context_menu）。GTK4 的 Gtk.Popover 不互斥，
        # 原代码每个 popup 都 remove_controller、每个 popdown 都 add_controller，
        # 在第二个 popup 时重复 remove 触发 GTK critical，第二个 popdown 重复
        # add 同样告警。计数确保只在深度 0↔1 转换时实际 add/remove。
        self._popover_depth = 0
        PopoverManager.connect('popup', self.on_popover_popup)
        PopoverManager.connect('popdown', self.on_popover_popdown)

    def on_new_document(self, workspace, document):
        self.setup_document_shortcuts(document)

    def setup_document_shortcuts(self, document):
        document.view.source_view.add_controller(ShortcutControllerDocument())
        if document.is_latex_document():
            document.view.source_view.add_controller(ShortcutControllerLaTeX())

    def on_popover_popup(self, name):
        # 仅在深度 0→1 转换时实际 remove_controller；深度 >0 时控制器
        # 已被移除，跳过避免 GTK critical（重复 remove 同一控制器）。
        if self._popover_depth == 0:
            self.main_window.remove_controller(self.shortcut_controller_app)
        self._popover_depth += 1

    def on_popover_popdown(self, name):
        # 若 popdown 在无对应 popup 时触发（depth 已为 0），直接返回——
        # 此时控制器已 attach，重复 add_controller 会触发 GTK critical。
        # 正常路径下仅在深度 1→0 转换时实际 add_controller。
        if self._popover_depth == 0:
            return
        self._popover_depth -= 1
        if self._popover_depth == 0:
            self.main_window.add_controller(self.shortcut_controller_app)



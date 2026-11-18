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

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_accels import ShortcutAccels, SectionNavigationController
from setzer.keyboard_shortcuts.shortcut_controller_document import ShortcutControllerDocument
from setzer.keyboard_shortcuts.shortcut_controller_latex import ShortcutControllerLaTeX, register_global_accels


class Shortcuts(object):

    def __init__(self):
        self.main_window = ServiceLocator.get_main_window()
        self.workspace = ServiceLocator.get_workspace()

        # 窗口级键走 application accel（见 shortcut_accels 模块注释：菜单提示
        # 自动跟随改键、popover 打开时不再失效、冲突检测可全量扫描）。
        # ShortcutAccels 自己监听 settings_changed，改键即时全局生效。
        self.shortcut_accels = ShortcutAccels()
        # 例外：章节导航 Alt+Up/Down 保留在窗口级控制器（编辑态需放行给
        # document 层的移动行，application accel 做不到放行）。
        self.section_nav_controller = SectionNavigationController()
        self.main_window.add_controller(self.section_nav_controller)
        # LaTeX 带参动作的加速器（textbf/italic/fraction 等）是窗口级全局
        # 注册，与具体文档无关：应用启动时执行一次即可。此前随每个新建/
        # 打开的文档重复注册，实测每次 0.6-1s——是「新建文档 / 打开文件 /
        # 会话恢复卡顿数秒」的主要来源。
        self.settings = ServiceLocator.get_settings()
        register_global_accels(self.settings)
        # 键位偏好变化时重新注册，全局即时生效（旧实现里已存在的文档
        # 从不更新，只有之后新建的文档才用新键位）。
        self.settings.connect('settings_changed', self.on_settings_changed)
        for document in self.workspace.open_documents: self.setup_document_shortcuts(document)
        self.workspace.connect('new_document', self.on_new_document)

    def on_new_document(self, workspace, document):
        self.setup_document_shortcuts(document)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'keyboard_shortcuts':
            register_global_accels(settings)
            # 文档级控制器（cut/copy、注释、引号等）在创建时读取设置，不会
            # 自动跟随偏好页的修改。重建已打开文档的控制器，使用户改键位
            # 后立即生效——尤其 toggle_comment 此前只存在于 LaTeX 控制器，
            # 偏好页只改设置，不重建就不会影响当前文档。
            self._refresh_document_shortcuts()

    def setup_document_shortcuts(self, document):
        controller = ShortcutControllerDocument()
        document.view.source_view.add_controller(controller)
        document._shortcut_controller_document = controller
        if document.is_latex_document():
            controller = ShortcutControllerLaTeX()
            document.view.source_view.add_controller(controller)
            document._shortcut_controller_latex = controller

    def _refresh_document_shortcuts(self):
        for document in self.workspace.open_documents:
            for attribute in ('_shortcut_controller_document',
                              '_shortcut_controller_latex'):
                controller = getattr(document, attribute, None)
                if controller is not None:
                    document.view.source_view.remove_controller(controller)
                    setattr(document, attribute, None)
            self.setup_document_shortcuts(document)



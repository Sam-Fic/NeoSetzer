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

import setzer.dialogs.build_log.build_log_dialog as build_log_dialog
from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator


class BuildLog(Observable):

    def __init__(self, workspace):
        Observable.__init__(self)
        self.workspace = workspace
        self.settings = ServiceLocator.get_settings()
        self.document = None

        self.items = list()

        # Pass-10: view 从嵌入式 Gtk.Box（BuildLogView）改为 Adw.Dialog 弹窗
        # （BuildLogDialog wrapper）。dialog 实例在此创建，按需 present/close。
        # wire(self) 注入 BuildLog 模型，建立 presenter + controller 双向连接。
        self.view = build_log_dialog.BuildLogDialog(ServiceLocator.get_main_window())
        self.view.wire(self)

        # is_open 标志位：Adw.Dialog 没有可靠的 is_open 属性（get_visible 语义是
        # widget 已 mapped），用自身标志位追踪。present 时置 True，closed 信号
        # 触发时置 False。workspace_presenter 据此判断切换文档时是否需刷新内容。
        self.is_open = False

        # 监听 dialog 关闭：用户按 Esc / 点 HeaderBar close 按钮关闭弹窗时，
        # 同步 workspace.show_build_log=False，触发 shortcutsbar toggle button 复位。
        # Adw.Dialog 的 closed 信号在 document_switcher / document_chooser 已有先例。
        self.view.view.connect('closed', self.on_dialog_closed)

    def on_build_log_update(self, build_system):
        if build_system.document == self.document:
            self.update_items(True)

    def set_document(self, document):
        if document == self.document: return

        if self.document != None:
            self.document.build_system.disconnect('build_log_update', self.on_build_log_update)

        self.document = document
        # document 可能为 None（最后一个文档关闭后 set_build_log 虽然不会
        # 主动传 None，但防御性处理避免 AttributeError 崩溃）。此时仅清空
        # items 引用，不访问 build_system。
        if document is None:
            self.items = list()
            self.add_change_code('build_log_finished_adding', False)
            return
        self.update_items()
        self.document.build_system.connect('build_log_update', self.on_build_log_update)

    def update_items(self, just_built=False):
        self.items = self.document.build_system.build_log_data['items']
        self.signal_finish_adding()

        if just_built and self.has_items(self.settings.get_value('preferences', 'autoshow_build_log')):
            # 自动构建路径：若用户关闭了 auto_build_autoshow_errors，则不自动弹出
            # 日志弹窗。避免打字途中触发自动构建、文档尚未输完导致报错时频繁打扰。
            # 手动构建（F5/F6）is_auto_build 为 False，始终遵循 autoshow_build_log。
            is_auto = getattr(self.document.build_system, 'is_auto_build', False)
            if is_auto and not self.settings.get_value('preferences', 'auto_build_autoshow_errors'):
                return
            self.workspace.set_show_build_log(True)

    def signal_finish_adding(self):
        self.add_change_code('build_log_finished_adding', self.document.build_system.document_has_been_built)

    def has_items(self, types='all'):
        return self.count_items(types) > 0

    def count_items(self, types='all'):
        # document 可能为 None（所有文档已关闭）。此时无构建日志可统计。
        if self.document is None:
            return 0
        if types == 'errors':
            return self.document.build_system.get_error_count()
        elif types == 'errors_warnings':
            return self.document.build_system.get_error_count() + self.document.build_system.get_warning_count()
        elif types == 'all':
            return self.document.build_system.get_error_count() + self.document.build_system.get_warning_count() + self.document.build_system.get_badbox_count()
        elif types == 'warnings':
            return self.document.build_system.get_warning_count()
        elif types == 'badboxes':
            return self.document.build_system.get_badbox_count()
        elif types == 'never':
            # 用户明确选择「从不自动显示构建日志」：无论出现何种问题都不自动弹出。
            return 0
        return 0

    def on_present(self):
        '''由 workspace_presenter 在 present 弹窗后调用，更新 is_open 标志。'''
        self.is_open = True
        # 调试断言：is_open 与 workspace.show_build_log 应保持一致。
        # 若不一致，说明 present/close 路径有遗漏（如外部直接 present 但未
        # set_show_build_log(True)），需要排查调用方。python -O 时自动剥离。
        assert self.workspace.get_show_build_log(), (
            'BuildLog.on_present: is_open=True 但 workspace.show_build_log=False，状态不一致'
        )
        if self.view.presenter is not None and getattr(self.view.presenter, '_dirty', False):
            self.view.presenter._update_filter_dropdowns()
            self.view.presenter.populate()

    def on_dialog_closed(self, dialog):
        '''Adw.Dialog 的 closed 信号回调。

        同步 workspace.show_build_log=False：
          - 触发 show_build_log_state_change
          - workspace_presenter.update_build_log_visibility 调 close（幂等，已关闭）
          - shortcutsbar.update_buttons 调 button_build_log.set_active(False)
          - GTK4 中程序 set_active 不触发 clicked，无循环
        '''
        self.is_open = False
        # 仅当当前 workspace 状态认为弹窗仍打开时才同步，避免 close→set(False)→
        # state_change→close 的无谓递归（虽然 close 幂等，但减少信号噪声）。
        # 若 workspace 已认为弹窗关闭（外部先调了 set_show_build_log(False)），
        # 此处跳过——is_open 与 show_build_log 此时一致（均为 False）。
        if self.workspace.get_show_build_log():
            self.workspace.set_show_build_log(False)

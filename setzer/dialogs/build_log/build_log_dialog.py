#!/usr/bin/env python3
# coding: utf-8

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

import setzer.dialogs.build_log.build_log_dialog_viewgtk as view_module
import setzer.dialogs.build_log.build_log_dialog_presenter as presenter_module
import setzer.dialogs.build_log.build_log_dialog_controller as controller_module


class BuildLogDialog(object):
    '''build_log 弹窗的组装入口（Pass-10）。

    范式与 `PreferencesDialog` 一致：hold view，按需 `present()`。
    `wire(build_log)` 在 `BuildLog.__init__` 中调用，注入 BuildLog 模型后
    建立 presenter + controller 的双向连接。

    `view` 属性暴露 `BuildLogDialogView`（`Adw.Dialog` 子类），供
    `BuildLog` 监听 `closed` 信号、`workspace_presenter` 调用 `present/close`。
    '''

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = view_module.BuildLogDialogView(main_window)
        # presenter / controller 在 wire() 中创建（需要 build_log 引用）。
        self.presenter = None
        self.controller = None

    def wire(self, build_log):
        '''绑定 BuildLog 模型，建立 presenter + controller。'''
        self.presenter = presenter_module.BuildLogDialogPresenter(build_log, self.view)
        self.controller = controller_module.BuildLogDialogController(build_log, self.view)
        self.controller.presenter = self.presenter

    def present(self):
        '''显示弹窗。Adw.Dialog.present 是幂等的：已打开时再 present 无副作用。'''
        self.view.present(self.main_window)

    def close(self):
        '''关闭弹窗。Adw.Dialog.close 是幂等的：未打开时 close 无副作用。'''
        self.view.close()

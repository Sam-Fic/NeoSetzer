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


class Shortcutsbar(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().shortcutsbar

        self.view.button_build_log.set_active(self.workspace.get_show_build_log())
        self.view.button_build_log.connect('clicked', self.on_build_log_button_clicked)

        self.view.button_search.connect('clicked', self.on_find_button_clicked)

        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('show_build_log_state_change', self.update_buttons)
        self.workspace.connect('root_state_change', self.on_root_state_change)

        # Pass-10: build_log_paned 已移除（build_log 改为弹窗）。原 width-changed
        # 监听早已无操作（注释说明），此处一并清理。
        self.document = self.workspace.active_document
        if self.document != None:
            self.document.search.connect('mode_changed', self.update_buttons)
            self.document.build_system.connect('build_state', self.on_build_state)
            self.update_wizard_button()
            # 启动时按当前编译结果初始化错误样式（如会话恢复了一个上次报错的文档）。
            self._refresh_build_log_error_style(self.document)

    def on_document_removed(self, workspace=None, parameter=None):
        if self.workspace.active_document == None:
            if self.document != None:
                self.document.search.disconnect('mode_changed', self.update_buttons)
                self.document.build_system.disconnect('build_state', self.on_build_state)
                self._clear_build_log_error_style()
            self.document = None

        self.update_buttons()

    def on_new_active_document(self, workspace=None, parameter=None):
        if self.document != None:
            self.document.search.disconnect('mode_changed', self.update_buttons)
            self.document.build_system.disconnect('build_state', self.on_build_state)

        self.document = self.workspace.active_document
        if self.document != None:
            self.document.search.connect('mode_changed', self.update_buttons)
            self.document.build_system.connect('build_state', self.on_build_state)
            self.update_wizard_button()
            # 切换文档后立刻按新文档当前的编译结果刷新错误样式。
            # 关键修复：切换到无 error 的文档时，按钮必须退出红色状态，
            # 而不能残留上一个文档（曾有 error）的红色样式——因为切换文档本身
            # 不会触发 build_state 信号，仅靠 on_build_state 无法纠正残留态。
            self._refresh_build_log_error_style(self.document)

        self.update_buttons()

    def on_root_state_change(self, workspace, state):
        self.update_buttons()

    def update_wizard_button(self, animate=False):
        if self.document == None: return

        # 与其他 latex 按钮一致：latex 文档时常驻显示，纯图标。
        # is_latex_document() 在文档生命周期内不变，因此只在文档切换时
        # 调用即可（on_new_active_document / __init__），无需每次按键触发。
        visible = self.document.is_latex_document()
        self.view.wizard_button._base_visible = visible
        self.view.wizard_button.set_visible(visible)


    def update_buttons(self, workspace=None, parameter=None):
        if self.document == None: return

        # 搜索入口合并为一个按钮：无论是 search 还是 replace 模式，都显示按下。
        self.view.button_search.set_active(self.document.search.search_bar_mode in ('search', 'replace'))

        is_latex = self.document.is_latex_document()
        # _base_visible 让 reflow 区分"update_buttons 隐藏"和
        # "reflow overflow 隐藏"——reflow 只 overflow base-visible 的按钮，
        # 不会意外显示被 update_buttons 隐藏的非 latex 按钮。
        for btn in [self.view.beamer_button, self.view.bibliography_button,
                    self.view.text_button, self.view.quotes_button,
                    self.view.math_button, self.view.object_button,
                    self.view.italic_button, self.view.bold_button,
                    self.view.document_button]:
            btn._base_visible = is_latex
            btn.set_visible(is_latex)

        root_or_active_latex = self.workspace.get_root_or_active_latex_document()
        self.view.button_build_log.set_active(self.workspace.get_show_build_log())
        self.view.button_build_log.set_visible(root_or_active_latex)

        # latex 专属按钮可见性变化会改变 left_box 总宽度，需重新计算 overflow
        self.view.request_reflow()

    def on_build_state(self, build_system, message):
        '''构建状态变化时更新 Log 按钮样式：出错变红。'''
        if message == 'error':
            self.view.button_build_log.add_css_class('build-log-error')
        else:
            self._clear_build_log_error_style()

    def _clear_build_log_error_style(self):
        self.view.button_build_log.remove_css_class('build-log-error')

    def _refresh_build_log_error_style(self, document):
        '''根据文档当前的编译结果刷新 build_log 按钮错误样式：
        - 文档未编译过 / 编译无 error（error_count == 0）：清除红色
        - 文档上次编译产生了 error（error_count > 0）：显示红色
        用于文档切换与初始构造，使按钮状态始终跟随当前活动文档，
        而非残留上一个文档的视觉状态。'''
        has_error = (document is not None and document.is_latex_document()
                     and document.build_system.get_error_count() > 0)
        if has_error:
            self.view.button_build_log.add_css_class('build-log-error')
        else:
            self._clear_build_log_error_style()

    def on_build_log_button_clicked(self, toggle_button, parameter=None):
        self.workspace.set_show_build_log(toggle_button.get_active())

    def on_find_button_clicked(self, button=None):
        if button.get_active():
            self.workspace.actions.start_search()
        else:
            self.workspace.actions.stop_search()



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

from gi.repository import Gio

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager

import os.path


class WorkspacePresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.settings = ServiceLocator.get_settings()

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        self.workspace.connect('set_show_symbols_or_document_structure', self.on_set_show_symbols_or_document_structure)
        self.workspace.connect('set_show_preview_or_help', self.on_set_show_preview_or_help)
        self.workspace.connect('show_build_log_state_change', self.on_show_build_log_state_change)
        self.settings.connect('settings_changed', self.on_settings_changed)

        self.main_window.mode_stack.set_visible_child_name('welcome_screen')
        # 初始默认欢迎页模式：headerbar 迁到 welcome_overlay，否则无文档时
        # open/create 按钮不可见、用户无法开始编辑（详见 reparent_headerbar 注释）。
        self.main_window.reparent_headerbar(to_welcome=True)
        self.update_font()
        self.update_colors()
        self.setup_paneds()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item in ['font_string', 'use_system_font']:
            self.update_font()

        if item == 'color_scheme':
            self.update_colors()

    def on_new_document(self, workspace, document):
        self.main_window.document_stack.add_child(document.view)

    def on_document_removed(self, workspace, document):
        self.main_window.document_stack.remove(document.view)

        if self.workspace.active_document == None:
            self.main_window.mode_stack.set_visible_child_name('welcome_screen')
            # 回到欢迎页：headerbar 迁到 welcome_overlay，保证 open/create 可见。
            self.main_window.reparent_headerbar(to_welcome=True)

    def on_new_active_document(self, workspace, document):
        self.main_window.mode_stack.set_visible_child_name('documents')
        # 进入文档视图：headerbar 迁回 preview_paned_overlay，只覆盖右侧
        # 编辑器/预览区，保留左侧 sidebar 通顶设计。
        self.main_window.reparent_headerbar(to_welcome=False)
        self.main_window.document_stack.set_visible_child(document.view)
        self.focus_active_document()

        if document.is_latex_document():
            try: self.main_window.preview_paned_overlay.add_overlay(document.autocomplete.widget.view)
            except AttributeError: pass

        self.update_sidebar_visibility(False)
        # Pass-10: 切换文档时弹窗若已打开，刷新内容为新文档的 build_log
        # （不关闭重开，保留弹窗位置/尺寸）。
        self.refresh_build_log_if_open()
        self.update_preview_help_visibility(False)

    def on_root_state_change(self, workspace, state):
        self.update_build_log_visibility()
        self.update_preview_help_visibility(False)

    def on_new_inactive_document(self, workspace, document):
        if document.is_latex_document():
            try: self.main_window.preview_paned_overlay.remove_overlay(document.autocomplete.widget.view)
            except AttributeError: pass

    def on_set_show_symbols_or_document_structure(self, workspace):
        if self.workspace.show_symbols:
            self.main_window.sidebar.set_visible_child_name('symbols')
        elif self.workspace.show_document_structure:
            self.main_window.sidebar.set_visible_child_name('document_structure')
        self.focus_active_document()

        self.update_sidebar_visibility()

    def on_set_show_preview_or_help(self, workspace):
        if self.workspace.show_preview:
            self.main_window.preview_help_stack.set_visible_child_name('preview')
            self.focus_active_document()
        elif self.workspace.show_help:
            self.main_window.preview_help_stack.set_visible_child_name('help')
            if self.main_window.help_panel.stack.get_visible_child_name() == 'search':
                self.main_window.help_panel.search_entry.set_text('')
                self.main_window.help_panel.search_entry.grab_focus()
            else:
                self.focus_active_document()
        else:
            self.focus_active_document()
        self.update_preview_help_visibility()

    def on_show_build_log_state_change(self, workspace, show_build_log):
        self.update_build_log_visibility()

    def update_sidebar_visibility(self, animate=True):
        sidebar_visible_for_latex_docs = self.workspace.show_symbols or self.workspace.show_document_structure
        show_sidebar = self.workspace.get_active_latex_document() and sidebar_visible_for_latex_docs
        self.main_window.sidebar_split.set_show_sidebar(show_sidebar)

    def update_build_log_visibility(self, animate=True):
        '''Pass-10: 从 set_visible 底部面板改为 present/close 弹窗。

        show_build_log=True 时 present dialog（若尚未打开）；False 时 close。
        present 前先调 presenter.populate 刷新内容（覆盖 autoshow 触发时
        items 已更新但 view 未同步的情况，以及启动时恢复弹窗状态）。
        '''
        show_build_log = self.workspace.get_root_or_active_latex_document() and self.workspace.show_build_log
        build_log = self.workspace.build_log
        if show_build_log:
            if not build_log.is_open:
                # present 前刷新内容：确保打开的是当前文档的最新 build_log。
                # populate 会处理 document 为 None 的情况（显示 empty_label）。
                if build_log.view.presenter is not None:
                    build_log.view.presenter.populate()
                build_log.view.present()
                build_log.on_present()
        else:
            # close 幂等：未打开时 close 无副作用。
            build_log.view.close()

    def refresh_build_log_if_open(self):
        '''切换文档时：弹窗若打开，刷新内容；不自动开关。

        与 update_build_log_visibility 的区别：后者根据 workspace.show_build_log
        状态决定 present/close；前者仅在已打开时刷新内容，保留弹窗状态。
        '''
        build_log = self.workspace.build_log
        if build_log.is_open and build_log.view.presenter is not None:
            build_log.view.presenter.populate()

    def update_preview_help_visibility(self, animate=True):
        preview_help_visible_for_latex_docs = self.workspace.show_preview or self.workspace.show_help
        show_preview_help = self.workspace.get_root_or_active_latex_document() and preview_help_visible_for_latex_docs
        # preview_split 为 Adw.OverlaySplitView，set_show_sidebar() 自带滑入/滑出动画
        # （与 sidebar_split 一致），故 toggle preview / help 有滑入动画。
        self.main_window.preview_split.set_show_sidebar(show_preview_help)

    def focus_active_document(self):
        active_document = self.workspace.get_active_document()
        if active_document != None:
            active_document.view.source_view.grab_focus()

    def update_font(self):
        if self.settings.get_value('preferences', 'use_system_font'):
            FontManager.font_string = FontManager.default_font_string
        else:
            FontManager.font_string = self.settings.get_value('preferences', 'font_string')
        FontManager.propagate_font_setting()

    def update_colors(self):
        # 自定义主题系统已移除，改为跟随系统 Libadwaita 调色板。
        # 自绘控件通过 ColorManager 的内置色回退取色。
        try: self.workspace.help_panel.update_colors()
        except AttributeError: pass

    def setup_paneds(self):
        sidebar_visible_for_latex_docs = self.workspace.show_symbols or self.workspace.show_document_structure
        show_sidebar = self.workspace.get_active_latex_document() and sidebar_visible_for_latex_docs
        preview_help_visible_for_latex_docs = self.workspace.show_preview or self.workspace.show_help
        show_preview_help = self.workspace.get_root_or_active_latex_document() and preview_help_visible_for_latex_docs
        # Pass-10: build_log 弹窗化后，初始显隐走 update_build_log_visibility
        # （present/close dialog），不再用 set_visible + paned position。
        show_build_log = self.workspace.get_root_or_active_latex_document() and self.workspace.get_show_build_log()

        sidebar_fraction = self.workspace.settings.get_value('window_state', 'sidebar_width_fraction')
        preview_fraction = self.workspace.settings.get_value('window_state', 'preview_width_fraction')

        # 一次性迁移：旧版用像素 position（= 编辑器列宽度），新版用 fraction（= 预览占比）。
        # 仅当存在有效旧 position 时估算 fraction = 1 - position/window_width，并落盘。
        legacy_pos = self.workspace.settings.data.get('window_state', {}).get('preview_paned_position', -1)
        saved_width = self.workspace.settings.get_value('window_state', 'width')
        if legacy_pos > 0 and saved_width > 0:
            preview_fraction = 1.0 - (legacy_pos / saved_width)
            preview_fraction = min(max(preview_fraction, 0.2), 0.8)
            self.workspace.settings.set_value('window_state', 'preview_width_fraction', preview_fraction)
            try: del(self.workspace.settings.data['window_state']['preview_paned_position'])
            except KeyError: pass

        # sidebar / preview 宽度均按 fraction（Adw.OverlaySplitView）
        if isinstance(sidebar_fraction, (int, float)) and 0.0 < sidebar_fraction <= 1.0:
            self.main_window.sidebar_split.set_sidebar_width_fraction(sidebar_fraction)
        if isinstance(preview_fraction, (int, float)) and 0.0 < preview_fraction <= 1.0:
            self.main_window.preview_split.set_sidebar_width_fraction(preview_fraction)
        # build_log_paned_position 设置项已废弃（弹窗尺寸由 Adw.Dialog 自管理）。

        if self.workspace.show_symbols: self.main_window.sidebar.set_visible_child_name('symbols')
        elif self.workspace.show_document_structure: self.main_window.sidebar.set_visible_child_name('document_structure')

        if self.workspace.show_preview: self.main_window.preview_help_stack.set_visible_child_name('preview')
        elif self.workspace.show_help: self.main_window.preview_help_stack.set_visible_child_name('help')

        # 初始显隐（首次无动画）。build_log 走 update_build_log_visibility
        # （present dialog 而非 set_visible 面板）。
        if show_build_log:
            self.update_build_log_visibility()
        self.main_window.sidebar_split.set_show_sidebar(show_sidebar)
        self.main_window.preview_split.set_show_sidebar(show_preview_help)

        # 拖动分隔条时实时持久化到 settings（仅更新内存 dict，pickle 在关闭时落盘）
        self.main_window.sidebar_split.connect('notify::sidebar-width-fraction', self.on_sidebar_width_changed)
        self.main_window.preview_split.connect('notify::sidebar-width-fraction', self.on_preview_width_changed)

        self.main_window.headerbar.symbols_toggle.set_active(self.workspace.show_symbols)
        self.main_window.headerbar.document_structure_toggle.set_active(self.workspace.show_document_structure)
        self.main_window.headerbar.preview_toggle.set_active(self.workspace.show_preview)
        self.main_window.headerbar.help_toggle.set_active(self.workspace.show_help)

    def on_sidebar_width_changed(self, split, pspec):
        self.workspace.settings.set_value('window_state', 'sidebar_width_fraction', split.get_sidebar_width_fraction())

    def on_preview_width_changed(self, split, pspec):
        self.workspace.settings.set_value('window_state', 'preview_width_fraction', split.get_sidebar_width_fraction())



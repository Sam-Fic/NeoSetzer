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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

import setzer.dialogs.preferences.preferences_viewgtk as view
import setzer.dialogs.preferences.pages.page_build_system as page_build_system
import setzer.dialogs.preferences.pages.page_editor as page_editor
import setzer.dialogs.preferences.pages.page_appearance as page_appearance
import setzer.dialogs.preferences.pages.page_shortcuts as page_shortcuts
import setzer.dialogs.preferences.pages.page_snippets as page_snippets
from setzer.app.service_locator import ServiceLocator


class PreferencesDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        # 持有视图，按需 present（与 BuildLogDialog 范式一致）。原每次 run() 都
        # 重建 view + 5 个 page 的完整 Adw 控件树（样式匹配/a11y 注册开销大）
        # 并重连所有信号、重新 spawn 解释器检测线程。Adw.PreferencesDialog
        # (AdwDialog) 支持关闭后重复 present，故首次构造后复用，setup() 仅跑
        # 一次。preferences 设置仅能通过本对话框修改，不存在外部并发修改，
        # 无需在再次 present 前重新同步控件值。
        self.view = None

    def run(self):
        if self.view is None:
            self.setup()
        self.view.present(self.main_window)

    def setup(self):
        self.view = view.Preferences()

        self.page_appearance = page_appearance.PageGeneral(self, self.settings, self.main_window)
        self.page_editor = page_editor.PageEditor(self, self.settings, main_window=self.main_window)
        self.page_build_system = page_build_system.PageBuildSystem(self, self.settings)
        self.page_shortcuts = page_shortcuts.PageShortcuts(self, self.settings)
        self.page_snippets = page_snippets.PageSnippets(self, self.settings)

        self.view.add(self.page_appearance.view)
        self.view.add(self.page_editor.view)
        self.view.add(self.page_build_system.view)
        self.view.add(self.page_shortcuts.view)
        self.view.add(self.page_snippets.view)

        self.page_appearance.init()
        self.page_editor.init()
        self.page_build_system.init()
        self.page_shortcuts.init()
        self.page_snippets.init()

        # 应用主题（Appearance 页）切换后，Editor 页的方案网格需按新深浅过滤
        # 候选并刷新预览配色。Editor 页已自连 Adw.StyleManager.notify::dark，
        # 此处补连 theme_combo 的显式切换（含「Light/Dark」非 System 选择）。
        self.page_appearance.view.theme_combo.connect(
            'notify::selected',
            lambda *a: (self.page_editor.populate_scheme_flowbox(),
                        self.page_editor.apply_preview_scheme()))

    def on_check_button_toggle(self, button, preference_name):
        self.settings.set_value('preferences', preference_name, button.get_active())
        
    def on_radio_button_toggle(self, button, preference_name, value):
        self.settings.set_value('preferences', preference_name, value)

    def spin_button_changed(self, button, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, int(button.get_property('value')))

    def text_deleted(self, buffer, position, n_chars, preference_name):
        self.settings.set_value('preferences', preference_name, buffer.get_text())

    def text_inserted(self, buffer, position, chars, n_chars, preference_name):
        self.settings.set_value('preferences', preference_name, buffer.get_text())

    def on_interpreter_changed(self, button, preference_name, value):
        self.settings.set_value('preferences', preference_name, value)

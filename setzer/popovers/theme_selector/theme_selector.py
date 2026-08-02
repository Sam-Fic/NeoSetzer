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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib


class ThemeSelector(Gtk.Box):
    '''主题快速切换器，移植自 GNOME Builder / GNOME Text Editor 的 themeselector。

    三个圆形色块（跟随系统 / 浅色 / 深色）水平居中排列，样式完全由 CSS 绘制：
    - follow：左上白、右下深的对角渐变
    - light：纯白
    - dark：深灰
    选中项通过 accent 色描边 + 右下角 object-select-symbolic 勾选角标表示。

    注意：GTK4 Python 中 set_css_name() 替换类型名后，CSS 后代选择器
    （如 "themeselector checkbutton"）不会匹配到子 widget。因此这里用
    add_css_class('themeselector') 代替，CSS 中用 ".themeselector" 选择器。
    '''

    # (stored_value, css_class, 未翻译的 tooltip)
    # 注意：这里存原文而不是 _() 的结果——类体在模块导入时求值，
    # 而 gettext 的 _ 是启动时才注入 builtins 的，过早调用会 NameError。
    # 实际翻译推迟到 _build_ui() 中进行。
    THEME_MODES = [
        ('system', 'follow', 'Follow system style'),
        ('light',  'light',  'Light style'),
        ('dark',   'dark',   'Dark style'),
    ]

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # 用 add_css_class 而非 set_css_name —— GTK4 Python 中 set_css_name
        # 替换类型名后，CSS 后代选择器（"themeselector checkbutton"）不会匹配到子 widget。
        self.add_css_class('themeselector')
        self.set_halign(Gtk.Align.CENTER)

        self.buttons = []
        self._updating = False
        self._build_ui()
        self._update_selection()

    def _build_ui(self):
        '''构建三个圆形主题按钮。

        使用 Gtk.CheckButton 并让后两个 set_group(第一个)，形成单选组，
        这与上游 UI 文件中三个 GtkCheckButton 共用 group 的做法一致。
        CSS 会把 checkbutton 画成圆形色块、把内部 radio 画成右下角勾选角标。
        '''
        group = None
        for stored_value, css_class, tooltip in self.THEME_MODES:
            button = Gtk.CheckButton()
            button.add_css_class(css_class)
            button.set_tooltip_text(_(tooltip))
            button.set_can_focus(True)
            button.set_focus_on_click(False)

            if group is None:
                group = button
            else:
                button.set_group(group)

            button.connect('toggled', self._on_button_toggled, stored_value)
            self.append(button)
            self.buttons.append((button, stored_value))

    def _on_button_toggled(self, button, stored_value):
        # _update_selection() 里同步状态时会触发 toggled，需要忽略以免回写设置
        if self._updating or not button.get_active():
            return

        from setzer.app.service_locator import ServiceLocator
        settings = ServiceLocator.get_settings()
        if settings.get_value('preferences', 'app_theme_mode') == stored_value:
            return

        settings.set_value('preferences', 'app_theme_mode', stored_value)
        self._apply_theme(stored_value)

    def _apply_theme(self, value):
        '''应用主题，复用偏好设置页的实现，保证两处行为一致。'''
        from setzer.dialogs.preferences.pages.page_appearance import PageGeneral
        PageGeneral.apply_theme(value)

    def _update_selection(self):
        '''根据当前设置里的主题模式，同步按钮选中状态。'''
        from setzer.app.service_locator import ServiceLocator
        settings = ServiceLocator.get_settings()
        current_mode = settings.get_value('preferences', 'app_theme_mode')

        self._updating = True
        for button, stored_value in self.buttons:
            button.set_active(stored_value == current_mode)
        self._updating = False

    def update_from_settings(self, _settings, change_info):
        '''偏好设置变化时更新按钮状态。

        change_info 是 (section, item, value) 元组。
        '''
        section, item, _value = change_info
        if section == 'preferences' and item == 'app_theme_mode':
            self._update_selection()

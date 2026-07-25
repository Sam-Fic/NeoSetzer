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
from gi.repository import Gtk, Adw


class PageEditor(object):

    def __init__(self, preferences, settings):
        self.view = PageEditorView()
        self.preferences = preferences
        self.settings = settings

    def init(self):
        self.view.reset_button.connect('clicked', self.on_reset_clicked)

        self.view.option_spaces_instead_of_tabs.set_active(self.settings.get_value('preferences', 'spaces_instead_of_tabs'))
        self.view.option_spaces_instead_of_tabs.connect('notify::active', self.on_switch_toggled, 'spaces_instead_of_tabs')

        self.view.tab_width_spinbutton.set_property('value', self.settings.get_value('preferences', 'tab_width'))
        self.view.tab_width_spinbutton.connect('notify::value', self.preferences.spin_button_changed, 'tab_width')

        self.view.option_show_line_numbers.set_active(self.settings.get_value('preferences', 'show_line_numbers'))
        self.view.option_show_line_numbers.connect('notify::active', self.on_switch_toggled, 'show_line_numbers')

        self.view.option_line_wrapping.set_active(self.settings.get_value('preferences', 'enable_line_wrapping'))
        self.view.option_line_wrapping.connect('notify::active', self.on_switch_toggled, 'enable_line_wrapping')

        self.view.option_code_folding.set_active(self.settings.get_value('preferences', 'enable_code_folding'))
        self.view.option_code_folding.connect('notify::active', self.on_switch_toggled, 'enable_code_folding')

        self.view.option_highlight_current_line.set_active(self.settings.get_value('preferences', 'highlight_current_line'))
        self.view.option_highlight_current_line.connect('notify::active', self.on_switch_toggled, 'highlight_current_line')

        self.view.option_highlight_matching_brackets.set_active(self.settings.get_value('preferences', 'highlight_matching_brackets'))
        self.view.option_highlight_matching_brackets.connect('notify::active', self.on_switch_toggled, 'highlight_matching_brackets')

        self.view.option_auto_save_enabled.set_active(self.settings.get_value('preferences', 'auto_save_enabled'))
        self.view.option_auto_save_enabled.connect('notify::active', self.on_switch_toggled, 'auto_save_enabled')

        self.view.option_auto_reload_on_external_change.set_active(
            self.settings.get_value('preferences', 'auto_reload_on_external_change'))
        self.view.option_auto_reload_on_external_change.connect(
            'notify::active', self.on_switch_toggled, 'auto_reload_on_external_change')

        self.view.auto_save_delay_row.set_property('value', self.settings.get_value('preferences', 'auto_save_delay'))
        self.view.auto_save_delay_row.connect('notify::value', self.preferences.spin_button_changed, 'auto_save_delay')

        # 重置按钮由 Appearance 页统一接管（合并后 Editor 不再是独立页），
        # 故此处不再连接 editor 自身的 reset_button；on_reset_clicked 供
        # Appearance 的 reset 调用以重置编辑相关项。


    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

    def on_reset_clicked(self, button):
        defaults = self.settings.defaults['preferences']
        self.view.option_spaces_instead_of_tabs.set_active(defaults['spaces_instead_of_tabs'])
        self.view.tab_width_spinbutton.set_property('value', defaults['tab_width'])
        self.view.option_show_line_numbers.set_active(defaults['show_line_numbers'])
        self.view.option_line_wrapping.set_active(defaults['enable_line_wrapping'])
        self.view.option_code_folding.set_active(defaults['enable_code_folding'])
        self.view.option_highlight_current_line.set_active(defaults['highlight_current_line'])
        self.view.option_highlight_matching_brackets.set_active(defaults['highlight_matching_brackets'])
        self.view.option_auto_save_enabled.set_active(defaults['auto_save_enabled'])
        self.view.auto_save_delay_row.set_property('value', defaults['auto_save_delay'])
        self.view.option_auto_reload_on_external_change.set_active(
            defaults['auto_reload_on_external_change'])


class PageEditorView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Editor'))
        self.set_icon_name('accessories-text-editor-symbolic')

        group_tab_stops = Adw.PreferencesGroup()
        group_tab_stops.set_title(_('Tab Stops'))
        self.add(group_tab_stops)

        self.option_spaces_instead_of_tabs = Adw.SwitchRow()
        self.option_spaces_instead_of_tabs.set_title(_('Insert spaces instead of tabs'))
        group_tab_stops.add(self.option_spaces_instead_of_tabs)

        self.tab_width_row = Adw.SpinRow()
        self.tab_width_row.set_title(_('Tab Width'))
        adjustment = Gtk.Adjustment(value=1, lower=1, upper=8, step_increment=1)
        self.tab_width_row.set_adjustment(adjustment)
        group_tab_stops.add(self.tab_width_row)
        self.tab_width_spinbutton = self.tab_width_row

        group_line_numbers = Adw.PreferencesGroup()
        group_line_numbers.set_title(_('Line Numbers'))
        self.add(group_line_numbers)

        self.option_show_line_numbers = Adw.SwitchRow()
        self.option_show_line_numbers.set_title(_('Show line numbers'))
        group_line_numbers.add(self.option_show_line_numbers)

        group_line_wrapping = Adw.PreferencesGroup()
        group_line_wrapping.set_title(_('Line Wrapping'))
        self.add(group_line_wrapping)

        self.option_line_wrapping = Adw.SwitchRow()
        self.option_line_wrapping.set_title(_('Enable line wrapping'))
        group_line_wrapping.add(self.option_line_wrapping)

        group_code_folding = Adw.PreferencesGroup()
        group_code_folding.set_title(_('Code Folding'))
        self.add(group_code_folding)

        self.option_code_folding = Adw.SwitchRow()
        self.option_code_folding.set_title(_('Enable code folding'))
        group_code_folding.add(self.option_code_folding)

        group_highlighting = Adw.PreferencesGroup()
        group_highlighting.set_title(_('Highlighting'))
        self.add(group_highlighting)

        self.option_highlight_current_line = Adw.SwitchRow()
        self.option_highlight_current_line.set_title(_('Highlight current line'))
        group_highlighting.add(self.option_highlight_current_line)

        self.option_highlight_matching_brackets = Adw.SwitchRow()
        self.option_highlight_matching_brackets.set_title(_('Highlight matching brackets'))
        group_highlighting.add(self.option_highlight_matching_brackets)

        # 自动保存（崩溃恢复）：把缓冲区内容定时写入临时文件，
        # 应用崩溃后下次启动可恢复未保存的编辑。
        group_auto_save = Adw.PreferencesGroup()
        group_auto_save.set_title(_('Auto Save'))
        self.add(group_auto_save)

        self.option_auto_save_enabled = Adw.SwitchRow()
        self.option_auto_save_enabled.set_title(_('Enable auto save (crash recovery)'))
        self.option_auto_save_enabled.set_subtitle(_('Periodically save buffer to a temp file for crash recovery'))
        group_auto_save.add(self.option_auto_save_enabled)

        self.auto_save_delay_row = Adw.SpinRow()
        self.auto_save_delay_row.set_title(_('Auto save interval (seconds)'))
        adjustment_auto_save = Gtk.Adjustment(value=60, lower=10, upper=600, step_increment=5)
        self.auto_save_delay_row.set_adjustment(adjustment_auto_save)
        group_auto_save.add(self.auto_save_delay_row)

        # 外部变更：自动静默重载磁盘上被其他程序修改的文件。
        group_external_changes = Adw.PreferencesGroup()
        group_external_changes.set_title(_('External Changes'))
        self.add(group_external_changes)

        self.option_auto_reload_on_external_change = Adw.SwitchRow()
        self.option_auto_reload_on_external_change.set_title(_('Automatically reload changed files'))
        self.option_auto_reload_on_external_change.set_subtitle(
            _('Reload files modified by external applications without prompting'))
        group_external_changes.add(self.option_auto_reload_on_external_change)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        group_reset.add(self.reset_button)

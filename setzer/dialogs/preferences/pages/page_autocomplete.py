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
from gi.repository import Gtk, Adw, Gdk

from setzer.app.service_locator import ServiceLocator


# 补全弹窗内的导航键（报告 #6 遗留项）：在「自动补全」偏好页登记 Page Up/Down
# 等键位，让用户能发现并重映射弹窗内的键盘交互。顺序即显示顺序。
# 注意：必须放在函数里延迟求值——模块导入发生在 gettext 安装 _ 之前，
# 模块级直接调用 _() 会导致 NameError。
def _nav_key_rows():
    return [
        ('autocomplete_previous', _('Select previous suggestion'),
         _('Move the selection up one item in the completion popup.')),
        ('autocomplete_next', _('Select next suggestion'),
         _('Move the selection down one item in the completion popup.')),
        ('autocomplete_previous_page', _('Previous page'),
         _('Scroll the completion popup up one page.')),
        ('autocomplete_next_page', _('Next page'),
         _('Scroll the completion popup down one page.')),
        ('autocomplete_accept', _('Accept suggestion'),
         _('Insert the currently selected completion.')),
        ('autocomplete_cancel', _('Dismiss popup'),
         _('Close the completion popup without inserting anything.')),
    ]


class PageAutocomplete(object):

    def __init__(self, preferences, settings):
        self.view = PageAutocompleteView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()

    def init(self):
        self.view.option_autocomplete.set_active(self.settings.get_value('preferences', 'enable_autocomplete'))
        self.view.option_autocomplete.connect('notify::active', self.on_switch_toggled, 'enable_autocomplete')

        self.view.option_bracket_completion.set_active(self.settings.get_value('preferences', 'enable_bracket_completion'))
        self.view.option_bracket_completion.connect('notify::active', self.on_switch_toggled, 'enable_bracket_completion')

        self.view.option_selection_brackets.set_active(self.settings.get_value('preferences', 'bracket_selection'))
        self.view.option_selection_brackets.connect('notify::active', self.on_switch_toggled, 'bracket_selection')

        self.view.option_tab_jump_brackets.set_active(self.settings.get_value('preferences', 'tab_jump_brackets'))
        self.view.option_tab_jump_brackets.connect('notify::active', self.on_switch_toggled, 'tab_jump_brackets')

        self.view.option_update_matching_blocks.set_active(self.settings.get_value('preferences', 'update_matching_blocks'))
        self.view.option_update_matching_blocks.connect('notify::active', self.on_switch_toggled, 'update_matching_blocks')

        accel = self.settings.get_value('preferences', 'autocomplete_manual_trigger')
        self.view.trigger_button.set_label(self._accel_label(accel))
        self.view.trigger_button.connect('clicked', self.on_trigger_capture_start)
        self._capture_controller = None
        self._capture_setting = None
        self._capture_mode = None
        self._capture_button = None

        # 补全弹窗导航键（报告 #6 遗留项）：为每个导航动作生成一行捕获按钮。
        for setting_name, title, subtitle in _nav_key_rows():
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            button = Gtk.Button()
            button.set_valign(Gtk.Align.CENTER)
            button.set_label(self._accel_label(self.settings.get_value('preferences', setting_name)))
            button.connect('clicked', self.on_nav_capture_start, setting_name)
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self.view.nav_group.add(row)
            self.view.nav_buttons[setting_name] = button

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to Defaults?'),
            body=_('All autocomplete settings will be restored to their default values.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_reset_confirmed)

    def on_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reset':
            defaults = self.settings.defaults['preferences']
            self.view.option_autocomplete.set_active(defaults['enable_autocomplete'])
            self.view.option_bracket_completion.set_active(defaults['enable_bracket_completion'])
            self.view.option_selection_brackets.set_active(defaults['bracket_selection'])
            self.view.option_tab_jump_brackets.set_active(defaults['tab_jump_brackets'])
            self.view.option_update_matching_blocks.set_active(defaults['update_matching_blocks'])
            self.view.trigger_button.set_label(self._accel_label(defaults['autocomplete_manual_trigger']))
            self.settings.set_value('preferences', 'autocomplete_manual_trigger', defaults['autocomplete_manual_trigger'])
            # 复位所有补全弹窗导航键（报告 #6 遗留项）。
            for name in ('autocomplete_previous', 'autocomplete_next',
                         'autocomplete_previous_page', 'autocomplete_next_page',
                         'autocomplete_accept', 'autocomplete_cancel'):
                self.settings.set_value('preferences', name, defaults[name])
                self.view.nav_buttons[name].set_label(self._accel_label(defaults[name]))

    def _accel_label(self, accel):
        # GTK4 的 accelerator_parse 返回 (success, keyval, mods) 三元组。
        _success, keyval, mods = Gtk.accelerator_parse(accel)
        if keyval == 0:
            return _('Disabled')
        return Gtk.accelerator_get_label(keyval, mods)

    def on_trigger_capture_start(self, button):
        # 进入捕获模式：下一次按键被记为新的手动触发键（报告 #6/B）。
        self._start_capture(button, 'autocomplete_manual_trigger', 'trigger')

    def on_nav_capture_start(self, button, setting_name):
        # 进入捕获模式：下一次按键被记为对应补全弹窗导航键（报告 #6 遗留项）。
        self._start_capture(button, setting_name, 'nav')

    def _start_capture(self, button, setting_name, mode):
        # 进入捕获模式：下一次按键被记为 setting_name 对应的快捷键。
        if self._capture_controller is not None:
            self._cancel_capture()
            return
        self._capture_setting = setting_name
        self._capture_mode = mode
        self._capture_button = button
        button.set_label(_('Press a shortcut… (Esc to cancel)'))
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect('key-pressed', self.on_capture_keypress)
        self.view.add_controller(controller)
        self._capture_controller = controller

    def on_capture_keypress(self, controller, keyval, keycode, state):
        if keyval == Gdk.keyval_from_name('Escape'):
            self._cancel_capture()
            return True
        mask = Gtk.accelerator_get_default_mod_mask()
        # 裸空格（无修饰键）作为键位无意义（打字即触发），忽略。
        if keyval == Gdk.keyval_from_name('space') and (state & mask) == 0:
            return True
        # 单独的修饰键无意义，任何模式都忽略。
        if Gdk.keyval_is_modifier(keyval):
            return True
        if self._capture_mode == 'trigger':
            # 触发器不允许绑定到 Tab/Return/方向/Page 等导航或激活键。
            ignore = {'Tab', 'ISO_Left_Tab', 'Return', 'KP_Enter', 'Up', 'Down',
                      'Left', 'Right', 'Page_Up', 'Page_Down', 'Home', 'End',
                      'BackSpace', 'Delete'}
            if keyval in {Gdk.keyval_from_name(n) for n in ignore}:
                return True
        # nav 模式放行上述键，让用户可把 Page Up/Down、Return、Tab 等绑为导航键。
        accel = Gtk.accelerator_name(keyval, state & mask)
        # GTK4 的 accelerator_parse 返回 (success, keyval, mods) 三元组。
        _success, kv, m = Gtk.accelerator_parse(accel)
        if kv == 0:
            self._cancel_capture()
            return True
        self.settings.set_value('preferences', self._capture_setting, accel)
        self._capture_button.set_label(self._accel_label(accel))
        self._end_capture()
        return True

    def _end_capture(self):
        if self._capture_controller is not None:
            self.view.remove_controller(self._capture_controller)
            self._capture_controller = None
        self._capture_setting = None
        self._capture_mode = None
        self._capture_button = None

    def _cancel_capture(self):
        accel = self.settings.get_value('preferences', self._capture_setting)
        self._capture_button.set_label(self._accel_label(accel))
        self._end_capture()


class PageAutocompleteView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Autocomplete'))
        self.set_icon_name('edit-find-replace-symbolic')

        group_latex_commands = Adw.PreferencesGroup()
        group_latex_commands.set_title(_('LaTeX Commands'))
        self.add(group_latex_commands)

        self.option_autocomplete = Adw.SwitchRow()
        self.option_autocomplete.set_title(_('Suggest matching LaTeX Commands'))
        self.option_autocomplete.set_subtitle(_('Show a completion popup as you type LaTeX commands.'))
        group_latex_commands.add(self.option_autocomplete)

        # 手动触发补全的快捷键：按钮显示当前按键，点击进入捕获模式（报告 #6/B）。
        row_trigger = Adw.ActionRow()
        row_trigger.set_title(_('Manual trigger key'))
        row_trigger.set_subtitle(_('Press to capture a shortcut that opens the completion popup.'))
        self.trigger_button = Gtk.Button()
        self.trigger_button.set_valign(Gtk.Align.CENTER)
        row_trigger.add_suffix(self.trigger_button)
        group_latex_commands.add(row_trigger)

        # 补全弹窗导航键的行容器与按钮引用（报告 #6 遗留项）。
        self.nav_group = Adw.PreferencesGroup()
        self.nav_group.set_title(_('Completion Navigation'))
        self.nav_group.set_description(_('Keys used to move and confirm within the completion popup.'))
        self.add(self.nav_group)
        self.nav_buttons = dict()

        group_others = Adw.PreferencesGroup()
        group_others.set_title(_('Brackets and Blocks'))
        self.add(group_others)

        self.option_bracket_completion = Adw.SwitchRow()
        self.option_bracket_completion.set_title(_('Automatically add closing brackets'))
        self.option_bracket_completion.set_subtitle(_('Insert a matching closing bracket when you type an opening one.'))
        group_others.add(self.option_bracket_completion)

        self.option_selection_brackets = Adw.SwitchRow()
        self.option_selection_brackets.set_title(_('Add brackets to selected text, instead of replacing it with them'))
        self.option_selection_brackets.set_subtitle(_('Wrap the selected text in brackets, keeping it selected.'))
        group_others.add(self.option_selection_brackets)

        self.option_tab_jump_brackets = Adw.SwitchRow()
        self.option_tab_jump_brackets.set_title(_('Jump over closing brackets with Tab'))
        self.option_tab_jump_brackets.set_subtitle(_('Press Tab to move past an automatically inserted closing bracket.'))
        group_others.add(self.option_tab_jump_brackets)

        self.option_update_matching_blocks = Adw.SwitchRow()
        self.option_update_matching_blocks.set_title(_('Update matching begin / end blocks'))
        self.option_update_matching_blocks.set_subtitle(_('Keep matching \\begin{} / \\end{} blocks in sync when you edit one.'))
        group_others.add(self.option_update_matching_blocks)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

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

from setzer.app.service_locator import ServiceLocator


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
        group_latex_commands.add(self.option_autocomplete)

        group_others = Adw.PreferencesGroup()
        group_others.set_title(_('Brackets and Blocks'))
        self.add(group_others)

        self.option_bracket_completion = Adw.SwitchRow()
        self.option_bracket_completion.set_title(_('Automatically add closing brackets'))
        group_others.add(self.option_bracket_completion)

        self.option_selection_brackets = Adw.SwitchRow()
        self.option_selection_brackets.set_title(_('Add brackets to selected text, instead of replacing it with them'))
        group_others.add(self.option_selection_brackets)

        self.option_tab_jump_brackets = Adw.SwitchRow()
        self.option_tab_jump_brackets.set_title(_('Jump over closing brackets with Tab'))
        group_others.add(self.option_tab_jump_brackets)

        self.option_update_matching_blocks = Adw.SwitchRow()
        self.option_update_matching_blocks.set_title(_('Update matching begin / end blocks'))
        group_others.add(self.option_update_matching_blocks)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

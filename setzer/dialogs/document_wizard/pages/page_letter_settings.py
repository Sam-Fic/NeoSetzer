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
gi.require_version('Adw', '1')
from gi.repository import Gtk
from gi.repository import Adw

from setzer.dialogs.document_wizard.pages.page import Page, PageView

import os


class LetterSettingsPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = LetterSettingsPageView()

    def observe_view(self):
        def format_changed(combo, pspec):
            selected = combo.get_selected()
            if selected != Gtk.INVALID_LIST_POSITION:
                self.current_values['letter']['page_format'] = self.view.page_format_names[selected]

        def font_size_changed(row, pspec):
            self.current_values['letter']['font_size'] = int(row.get_value())

        def margin_changed(row, pspec, side):
            self.current_values['letter']['margin_' + side] = row.get_value()

        def option_toggled(row, pspec, key):
            self.current_values['letter'][key] = row.get_active()

        self.view.page_format_combo.connect('notify::selected', format_changed)
        self.view.font_size_entry.connect('notify::value', font_size_changed)
        self.view.option_twocolumn.connect('notify::active', option_toggled, 'option_twocolumn')
        self.view.option_landscape.connect('notify::active', option_toggled, 'is_landscape')
        self.view.option_default_margins.connect('notify::active', self.option_default_margins_toggled, 'default_margins')
        self.view.margins_button_left.connect('notify::value', margin_changed, 'left')
        self.view.margins_button_right.connect('notify::value', margin_changed, 'right')
        self.view.margins_button_top.connect('notify::value', margin_changed, 'top')
        self.view.margins_button_bottom.connect('notify::value', margin_changed, 'bottom')

    def option_default_margins_toggled(self, row, pspec=None, option_name=None):
        for spinrow in [self.view.margins_button_left, self.view.margins_button_right, self.view.margins_button_top, self.view.margins_button_bottom]:
            spinrow.set_sensitive(not row.get_active())
            if row.get_active():
                spinrow.set_value(3.5)
        if option_name != None:
            self.current_values['letter']['option_' + option_name] = row.get_active()

    def load_presets(self, presets):
        for setter_function, value_name in [
            (self.view.font_size_entry.set_value, 'font_size'),
            (self.view.option_twocolumn.set_active, 'option_twocolumn'),
            (self.view.option_landscape.set_active, 'is_landscape'),
            (self.view.margins_button_left.set_value, 'margin_left'),
            (self.view.margins_button_right.set_value, 'margin_right'),
            (self.view.margins_button_top.set_value, 'margin_top'),
            (self.view.margins_button_bottom.set_value, 'margin_bottom'),
            (self.view.option_default_margins.set_active, 'option_default_margins')
        ]:
            try:
                value = presets['letter'][value_name]
            except (TypeError, KeyError):
                value = self.current_values['letter'][value_name]
            setter_function(value)

        try: value = presets['letter']['page_format']
        except Exception: value = self.current_values['letter']['page_format']
        self.view.page_format_combo.set_selected(self.view.page_format_names.index(value))

        self.option_default_margins_toggled(self.view.option_default_margins)

    def on_activation(self):
        pass


class LetterSettingsPageView(PageView):

    def __init__(self):
        PageView.__init__(self)
        self.set_document_settings_page()

        self.headerbar_subtitle = _('Step') + ' 2: ' + _('Letter settings')

        self.group_page_format = Adw.PreferencesGroup()
        self.group_page_format.set_title(_('Page format'))
        self.group_page_format.add(self.page_format_combo)

        self.group_options = Adw.PreferencesGroup()
        self.group_options.set_title(_('Options'))
        self.group_options.add(self.option_landscape)
        self.group_options.add(self.option_twocolumn)

        self.group_font_size = Adw.PreferencesGroup()
        self.group_font_size.set_title(_('Font size'))
        self.group_font_size.add(self.font_size_entry)

        self.group_margins = Adw.PreferencesGroup()
        self.group_margins.set_title(_('Page margins'))
        self.group_margins.set_description(_('All values are in cm (1 inch ≅ 2.54 cm).'))
        self.group_margins.add(self.option_default_margins)
        self.group_margins.add(self.margins_button_left)
        self.group_margins.add(self.margins_button_right)
        self.group_margins.add(self.margins_button_top)
        self.group_margins.add(self.margins_button_bottom)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.append(self.group_page_format)
        self.content.append(self.group_options)
        self.content.append(self.group_font_size)
        self.content.append(self.group_margins)

        self.append(self.wrap_content(self.content))

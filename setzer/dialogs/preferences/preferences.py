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
from gi.repository import Gtk, Adw

import setzer.dialogs.preferences.preferences_viewgtk as view
import setzer.dialogs.preferences.pages.page_build_system as page_build_system
import setzer.dialogs.preferences.pages.page_editor as page_editor
import setzer.dialogs.preferences.pages.page_autocomplete as page_autocomplete
import setzer.dialogs.preferences.pages.page_appearance as page_appearance
import setzer.dialogs.preferences.pages.page_shortcuts as page_shortcuts
from setzer.app.service_locator import ServiceLocator


class PreferencesDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()

    def run(self):
        self.setup()
        self.view.present(self.main_window)

    def setup(self):
        self.view = view.Preferences()

        self.page_appearance = page_appearance.PageAppearanceColors(self, self.settings, self.main_window)
        self.page_build_system = page_build_system.PageBuildSystem(self, self.settings)
        self.page_editor = page_editor.PageEditor(self, self.settings)
        self.page_autocomplete = page_autocomplete.PageAutocomplete(self, self.settings)
        self.page_shortcuts = page_shortcuts.PageShortcuts(self, self.settings)

        self.view.add(self.page_appearance.view)
        self.view.add(self.page_build_system.view)
        self.view.add(self.page_autocomplete.view)
        self.view.add(self.page_shortcuts.view)

        self.page_appearance.init()
        self.page_build_system.init()
        self.page_editor.init()
        self.page_autocomplete.init()
        self.page_shortcuts.init()

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

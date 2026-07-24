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
from setzer.keyboard_shortcuts.shortcut_controller import ShortcutController
from setzer.popovers.popover_manager import PopoverManager


class ShortcutControllerApp(ShortcutController):

    def __init__(self):
        ShortcutController.__init__(self)

        self.main_window = ServiceLocator.get_main_window()
        self.workspace = ServiceLocator.get_workspace()
        self.actions = self.workspace.actions
        self.settings = ServiceLocator.get_settings()

        self.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        self.load_shortcuts()

    def load_shortcuts(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        
        self.create_and_add_shortcut(shortcuts.get('new_document', '<Control>n'), self.actions.new_latex_document)
        self.create_and_add_shortcut(shortcuts.get('open_document', '<Control>o'), self.actions.open_document_dialog)
        self.create_and_add_shortcut(shortcuts.get('save', '<Control>s'), self.actions.save)
        self.create_and_add_shortcut(shortcuts.get('save_as', '<Control><Shift>s'), self.actions.save_as)
        self.create_and_add_shortcut(shortcuts.get('close_document', '<Control>w'), self.actions.close_active_document)
        self.create_and_add_shortcut('<Control><Shift>t', self.actions.reopen_last_closed_document)
        self.create_and_add_shortcut(shortcuts.get('quit', '<Control>q'), self.actions.actions['quit'].activate)
        self.create_and_add_shortcut(shortcuts.get('show_shortcuts', '<Control>question'), self.actions.show_shortcuts_dialog)
        self.create_and_add_shortcut(shortcuts.get('show_open_docs', '<Control>t'), self.shortcut_show_open_docs)
        self.create_and_add_shortcut(shortcuts.get('switch_document', '<Control>Tab'), self.shortcut_switch_document)
        self.create_and_add_shortcut(shortcuts.get('show_document_chooser', '<Control><Shift>o'), self.shortcut_show_document_chooser)
        self.create_and_add_shortcut(shortcuts.get('zoom_in', '<Control>plus'), self.actions.zoom_in)
        self.create_and_add_shortcut(shortcuts.get('zoom_out', '<Control>minus'), self.actions.zoom_out)
        self.create_and_add_shortcut(shortcuts.get('reset_zoom', '<Control>0'), self.actions.reset_zoom)
        self.create_and_add_shortcut(shortcuts.get('find', '<Control>f'), self.actions.start_search)
        self.create_and_add_shortcut('<Control>l', self.actions.go_to_line)
        self.create_and_add_shortcut(shortcuts.get('find_and_replace', '<Control>h'), self.actions.start_search_and_replace)
        self.create_and_add_shortcut(shortcuts.get('find_next', '<Control>g'), self.actions.find_next)
        self.create_and_add_shortcut(shortcuts.get('find_previous', '<Control><Shift>g'), self.actions.find_previous)
        self.create_and_add_shortcut('F3', self.actions.find_next)
        self.create_and_add_shortcut('<Shift>F3', self.actions.find_previous)
        self.create_and_add_shortcut(shortcuts.get('help', 'F1'), self.shortcut_help)
        self.create_and_add_shortcut(shortcuts.get('document_structure', '<Control><Shift>b'), self.shortcut_document_structure_toggle)
        self.create_and_add_shortcut(shortcuts.get('symbols', '<Control><Shift>s'), self.shortcut_symbols_toggle)
        self.create_and_add_shortcut(shortcuts.get('save_and_build', 'F5'), self.actions.save_and_build)
        self.create_and_add_shortcut(shortcuts.get('build', 'F6'), self.actions.build)
        self.create_and_add_shortcut(shortcuts.get('forward_sync', 'F7'), self.actions.forward_sync)
        self.create_and_add_shortcut(shortcuts.get('build_log', '<Control><Shift>l'), self.shortcut_build_log)
        self.create_and_add_shortcut(shortcuts.get('preview', '<Control><Shift>p'), self.shortcut_preview)
        self.create_and_add_shortcut(shortcuts.get('hamburger_menu', 'F10'), self.shortcut_show_hamburger)

    def shortcut_show_document_chooser(self):
        if self.main_window.headerbar.open_document_button.get_sensitive():
            PopoverManager.get_popover('open_document').show()

    def shortcut_show_open_docs(self):
        if self.main_window.headerbar.center_button.get_sensitive():
            PopoverManager.get_popover('document_switcher').show()

    def shortcut_switch_document(self):
        self.workspace.switch_to_earliest_open_document()

    def shortcut_build_log(self):
        show_build_log = not self.workspace.get_show_build_log()
        self.workspace.set_show_build_log(show_build_log)

    def shortcut_preview(self):
        toggle = self.main_window.headerbar.preview_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())
        return True

    def shortcut_help(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.help_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())
        return True

    def shortcut_document_structure_toggle(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.document_structure_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())
        return True

    def shortcut_symbols_toggle(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.symbols_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())
        return True

    def shortcut_show_hamburger(self, accel_group=None, window=None, key=None, mask=None):
        self.main_window.headerbar.menu_button.popup()
        return True



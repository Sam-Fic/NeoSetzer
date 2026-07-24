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


class ShortcutControllerDocument(ShortcutController):

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
        
        self.create_and_add_shortcut(shortcuts.get('cut', '<Control>x'), self.actions.cut)
        self.create_and_add_shortcut(shortcuts.get('copy', '<Control>c'), self.actions.copy)
        self.create_and_add_shortcut(shortcuts.get('paste', '<Control>v'), self.actions.paste)
        self.create_and_add_shortcut(shortcuts.get('undo', '<Control>z'), self.actions.undo)
        self.create_and_add_shortcut(shortcuts.get('redo', '<Control><Shift>z'), self.actions.redo)
        self.create_and_add_shortcut('<Control>y', self.actions.redo)
        self.create_and_add_shortcut('<Control>d', self.actions.delete_line)
        self.create_and_add_shortcut(shortcuts.get('context_menu', '<Shift>F10'), self.actions.show_context_menu)



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
from gi.repository import Gtk, GLib, Gio

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_controller import ShortcutController


class ShortcutControllerLaTeX(ShortcutController):

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
        
        self.set_accels_for_insert_before_after_action(['\\textbf{', '}'], [shortcuts.get('bold', '<Control>b')])
        self.set_accels_for_insert_before_after_action(['\\textit{', '}'], [shortcuts.get('italic', '<Control>i')])
        self.set_accels_for_insert_before_after_action(['\\underline{', '}'], [shortcuts.get('underline', '<Control>u')])
        self.set_accels_for_insert_before_after_action(['\\texttt{', '}'], [shortcuts.get('typewriter', '<Control><Shift>y')])
        self.set_accels_for_insert_before_after_action(['\\emph{', '}'], [shortcuts.get('emphasized', '<Control><Shift>e')])
        self.set_accels_for_insert_before_after_action(['$ ', ' $'], [shortcuts.get('inline_math', '<Control>m')])
        self.set_accels_for_insert_before_after_action(['\\[ ', ' \\]'], [shortcuts.get('display_math', '<Control><Shift>m')])
        self.set_accels_for_insert_before_after_action(['\\begin{equation}\n\t', '\n\\end{equation}'], [shortcuts.get('equation', '<Control><Shift>n')])
        self.set_accels_for_insert_before_after_action(['\\begin{•}\n\t', '\n\\end{•}'], [shortcuts.get('environment', '<Control>e')])
        self.set_accels_for_insert_before_after_action(['_{', '}'], [shortcuts.get('subscript', '<Control><Shift>d')])
        self.set_accels_for_insert_before_after_action(['^{', '}'], [shortcuts.get('superscript', '<Control><Shift>u')])
        self.set_accels_for_insert_symbol_action(['\\frac{•}{•}'], [shortcuts.get('fraction', '<Alt><Shift>f')])
        self.set_accels_for_insert_symbol_action(['\\left •'], [shortcuts.get('left', '<Control><Shift>l')])
        self.set_accels_for_insert_symbol_action(['\\right •'], [shortcuts.get('right', '<Control><Shift>r')])
        self.set_accels_for_insert_symbol_action(['\\item •'], [shortcuts.get('list_item', '<Control><Shift>i')])
        self.set_accels_for_insert_symbol_action(['\\\\\n'], [shortcuts.get('new_line', '<Control>Return')])

        # fallback 与 settings.py 默认值 '<Control>slash' 保持一致：settings 始终提供
        # 该值，fallback 实际不会命中，但保持两边写法一致以避免混淆。
        self.create_and_add_shortcut(shortcuts.get('toggle_comment', '<Control>slash'), self.actions.toggle_comment)
        self.create_and_add_shortcut(shortcuts.get('quotation_marks', '<Control>quotedbl'), self.shortcut_quotes)

    def set_accels_for_insert_before_after_action(self, parameter, accels):
        self.main_window.app.set_accels_for_action(Gio.Action.print_detailed_name('win.insert-before-after', GLib.Variant('as', parameter)), accels)

    def set_accels_for_insert_symbol_action(self, parameter, accels):
        self.main_window.app.set_accels_for_action(Gio.Action.print_detailed_name('win.insert-symbol', GLib.Variant('as', parameter)), accels)

    def shortcut_quotes(self, accel_group=None, window=None, key=None, mask=None):
        self.main_window.shortcutsbar.quotes_button.activate()



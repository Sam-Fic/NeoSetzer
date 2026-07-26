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

        # 可配置快捷键：action_name -> (Gtk.Shortcut, callback)。
        # update_shortcut() 据此精准替换单个 action 的 trigger，无需重建整个
        # controller（避免销毁所有已注册快捷键、重新解析全部 trigger 字符串）。
        # 非配置快捷键（如 F3、Shift+F3）用 create_and_add_shortcut 直接注册，
        # 不在此表中——它们不会变化。
        self._configurable_shortcuts = {}

        self.load_shortcuts()

    def load_shortcuts(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']

        self._register_configurable('new_document', shortcuts.get('new_document', '<Control>n'), self.actions.new_latex_document)
        self._register_configurable('open_document', shortcuts.get('open_document', '<Control>o'), self.actions.open_document_dialog)
        self._register_configurable('save', shortcuts.get('save', '<Control>s'), self.actions.save)
        self._register_configurable('save_as', shortcuts.get('save_as', '<Control><Shift>s'), self.actions.save_as)
        self._register_configurable('close_document', shortcuts.get('close_document', '<Control>w'), self.actions.close_active_document)
        # 非配置：硬编码快捷键，不通过设置修改
        self.create_and_add_shortcut('<Control><Shift>t', self.actions.reopen_last_closed_document)
        self._register_configurable('quit', shortcuts.get('quit', '<Control>q'), self.actions.actions['quit'].activate)
        self._register_configurable('show_shortcuts', shortcuts.get('show_shortcuts', '<Control>question'), self.actions.show_shortcuts_dialog)
        self._register_configurable('show_open_docs', shortcuts.get('show_open_docs', '<Control>t'), self.shortcut_show_open_docs)
        self._register_configurable('switch_document', shortcuts.get('switch_document', '<Control>Tab'), self.shortcut_switch_document)
        self._register_configurable('show_document_chooser', shortcuts.get('show_document_chooser', '<Control><Shift>o'), self.shortcut_show_document_chooser)
        self._register_configurable('zoom_in', shortcuts.get('zoom_in', '<Control>plus'), self.actions.zoom_in)
        self._register_configurable('zoom_out', shortcuts.get('zoom_out', '<Control>minus'), self.actions.zoom_out)
        self._register_configurable('reset_zoom', shortcuts.get('reset_zoom', '<Control>0'), self.actions.reset_zoom)
        self._register_configurable('find', shortcuts.get('find', '<Control>f'), self.actions.start_search)
        # 非配置
        self.create_and_add_shortcut('<Control>l', self.actions.go_to_line)
        self._register_configurable('find_and_replace', shortcuts.get('find_and_replace', '<Control>h'), self.actions.start_search_and_replace)
        self._register_configurable('find_next', shortcuts.get('find_next', '<Control>g'), self.actions.find_next)
        self._register_configurable('find_previous', shortcuts.get('find_previous', '<Control><Shift>g'), self.actions.find_previous)
        # 非配置：F3/Shift+F3 不通过设置修改
        self.create_and_add_shortcut('F3', self.actions.find_next)
        self.create_and_add_shortcut('<Shift>F3', self.actions.find_previous)
        self._register_configurable('help', shortcuts.get('help', 'F1'), self.shortcut_help)
        self._register_configurable('document_structure', shortcuts.get('document_structure', '<Control><Shift>b'), self.shortcut_document_structure_toggle)
        self._register_configurable('symbols', shortcuts.get('symbols', '<Control><Shift>s'), self.shortcut_symbols_toggle)
        self._register_configurable('save_and_build', shortcuts.get('save_and_build', 'F5'), self.actions.save_and_build)
        self._register_configurable('build', shortcuts.get('build', 'F6'), self.actions.build)
        self._register_configurable('print', shortcuts.get('print', '<Control>p'), self.actions.print_document)
        self._register_configurable('forward_sync', shortcuts.get('forward_sync', 'F7'), self.actions.forward_sync)
        self._register_configurable('build_log', shortcuts.get('build_log', '<Control><Shift>l'), self.shortcut_build_log)
        self._register_configurable('preview', shortcuts.get('preview', '<Control><Shift>p'), self.shortcut_preview)
        self._register_configurable('hamburger_menu', shortcuts.get('hamburger_menu', 'F10'), self.shortcut_show_hamburger)

    def _register_configurable(self, action_name, trigger_string, callback):
        '''Register a user-configurable shortcut and track it by action_name
        so update_shortcut() can replace just its trigger later.'''
        shortcut = Gtk.Shortcut()
        shortcut.set_action(Gtk.CallbackAction.new(self.action, callback))
        shortcut.set_trigger(Gtk.ShortcutTrigger.parse_string(trigger_string))
        self.add_shortcut(shortcut)
        self._configurable_shortcuts[action_name] = (shortcut, callback)

    def update_shortcut(self, action_name, new_trigger_string):
        '''Replace the trigger for a single configurable action on this
        existing controller. Avoids destroying/recreating the entire
        ShortcutControllerApp (which would re-parse all triggers and
        re-add every Gtk.Shortcut).

        Returns True if the action was found and updated, False otherwise.
        Empty trigger_string removes the shortcut (action becomes unassigned).'''
        entry = self._configurable_shortcuts.get(action_name)
        if entry is None:
            return False
        old_shortcut, callback = entry
        self.remove_shortcut(old_shortcut)
        if new_trigger_string:
            new_shortcut = Gtk.Shortcut()
            new_shortcut.set_action(Gtk.CallbackAction.new(self.action, callback))
            new_shortcut.set_trigger(Gtk.ShortcutTrigger.parse_string(new_trigger_string))
            self.add_shortcut(new_shortcut)
            self._configurable_shortcuts[action_name] = (new_shortcut, callback)
        else:
            # 空快捷键 = 未分配，只移除不新增
            self._configurable_shortcuts[action_name] = (None, callback)
        return True

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
        toggle = self.main_window.headerbar.preview_help_toggle
        if toggle.get_sensitive():
            toggle.set_active(not toggle.get_active())
        return True

    def shortcut_help(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.preview_help_toggle
        if toggle.get_sensitive():
            if not toggle.get_active():
                toggle.set_active(True)
                self.workspace.set_show_preview_or_help(False, True)
            else:
                if self.workspace.show_help:
                    toggle.set_active(False)
                else:
                    self.workspace.set_show_preview_or_help(False, True)
        return True

    def shortcut_document_structure_toggle(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.sidebar_toggle
        if toggle.get_sensitive():
            if not toggle.get_active():
                toggle.set_active(True)
                self.workspace.set_show_symbols_or_document_structure(False, True)
            else:
                if self.workspace.show_document_structure:
                    toggle.set_active(False)
                else:
                    self.workspace.set_show_symbols_or_document_structure(False, True)
        return True

    def shortcut_symbols_toggle(self, accel_group=None, window=None, key=None, mask=None):
        toggle = self.main_window.headerbar.sidebar_toggle
        if toggle.get_sensitive():
            if not toggle.get_active():
                toggle.set_active(True)
                self.workspace.set_show_symbols_or_document_structure(True, False)
            else:
                if self.workspace.show_symbols:
                    toggle.set_active(False)
                else:
                    self.workspace.set_show_symbols_or_document_structure(True, False)
        return True

    def shortcut_show_hamburger(self, accel_group=None, window=None, key=None, mask=None):
        self.main_window.headerbar.menu_button.popup()
        return True



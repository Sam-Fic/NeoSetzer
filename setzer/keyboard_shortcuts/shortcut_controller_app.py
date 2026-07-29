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
        # 章节导航：Alt+Up / Alt+Down 跳到上/下一段。仅在非编辑（光标不在源码
        # 视图）时生效；在源码视图中 Alt+Up/Down 仍用于移动行（见 document 控制器）。
        self.create_and_add_shortcut('<Alt>Up', self.shortcut_prev_section)
        self.create_and_add_shortcut('<Alt>Down', self.shortcut_next_section)

    def load_shortcuts(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']

        self._register_configurable('new_document', shortcuts.get('new_document', '<Control>n'), self.actions.new_latex_document)
        self._register_configurable('open_document', shortcuts.get('open_document', '<Control>o'), self.actions.open_document_dialog)
        self._register_configurable('save', shortcuts.get('save', '<Control>s'), self.actions.save)
        self._register_configurable('save_as', shortcuts.get('save_as', '<Control><Shift>s'), self.actions.save_as)
        self._register_configurable('close_document', shortcuts.get('close_document', '<Control>w'), self.actions.close_active_document)
        # reopen_last_closed_document 现为可配置项（默认 Ctrl+Shift+T，浏览器式
        # "重开标签页"惯例）；此前为硬编码、用户无法改绑，现纳入快捷键编辑器。
        self._register_configurable('reopen_last_closed_document', shortcuts.get('reopen_last_closed_document', '<Control><Shift>t'), self.actions.reopen_last_closed_document)
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
        self._register_configurable('symbols', shortcuts.get('symbols', 'F8'), self.shortcut_symbols_toggle)
        self._register_configurable('save_and_build', shortcuts.get('save_and_build', 'F5'), self.actions.save_and_build)
        self._register_configurable('build', shortcuts.get('build', 'F6'), self.actions.build)
        self._register_configurable('print', shortcuts.get('print', '<Control>p'), self.actions.print_document)
        self._register_configurable('forward_sync', shortcuts.get('forward_sync', 'F7'), self.actions.forward_sync)
        self._register_configurable('build_log', shortcuts.get('build_log', 'F4'), self.shortcut_build_log)
        self._register_configurable('preview', shortcuts.get('preview', '<Control><Shift>p'), self.shortcut_preview)
        self._register_configurable('hamburger_menu', shortcuts.get('hamburger_menu', 'F10'), self.shortcut_show_hamburger)
        self._register_configurable('fullscreen', shortcuts.get('fullscreen', 'F11'), self.actions.toggle_fullscreen)
        self._register_configurable('show_preferences_dialog', shortcuts.get('show_preferences_dialog', '<Control>comma'), self.actions.show_preferences_dialog)
        self._register_configurable('show_about_dialog', shortcuts.get('show_about_dialog', ''), self.actions.show_about_dialog)
        self._register_configurable('close_all_documents', shortcuts.get('close_all_documents', '<Control><Shift>w'), self.actions.close_all)
        self._register_configurable('restore_session', shortcuts.get('restore_session', '<Control><Shift>j'), lambda: self.main_window.activate_action('restore-session'))

    def _register_configurable(self, action_name, trigger_string, callback):
        '''Register a user-configurable shortcut and track it by action_name
        so update_shortcut() can replace just its trigger later.

        若 trigger_string 为空（如 show_about_dialog 默认未绑定快捷键），
        只登记 callback、不创建实际 trigger：Gtk.ShortcutTrigger.parse_string('')
        返回 None，set_trigger(None) 无意义；动作仍可在偏好页里后续改绑。'''
        shortcut = Gtk.Shortcut()
        shortcut.set_action(Gtk.CallbackAction.new(self.action, callback))
        if trigger_string:
            trigger = self._parse_trigger(trigger_string)
            if trigger is not None:
                shortcut.set_trigger(trigger)
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
        if old_shortcut is not None:
            self.remove_shortcut(old_shortcut)
        if new_trigger_string:
            new_shortcut = Gtk.Shortcut()
            new_shortcut.set_action(Gtk.CallbackAction.new(self.action, callback))
            trigger = self._parse_trigger(new_trigger_string)
            if trigger is not None:
                new_shortcut.set_trigger(trigger)
                self.add_shortcut(new_shortcut)
            self._configurable_shortcuts[action_name] = (new_shortcut, callback)
        else:
            # 空快捷键 = 未分配，只移除不新增
            self._configurable_shortcuts[action_name] = (None, callback)
        return True

    def shortcut_show_document_chooser(self):
        if self.main_window.headerbar.open_document_button.get_visible():
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

    def shortcut_prev_section(self):
        # 在源码视图中 Alt+Up/Down 用于移动行，故编辑时不拦截，交回 document 控制器。
        if self._is_editing():
            return False
        self._navigate_section(-1)
        return True

    def shortcut_next_section(self):
        if self._is_editing():
            return False
        self._navigate_section(1)
        return True

    def _is_editing(self):
        document = self.workspace.get_active_document()
        if document is None:
            return False
        return document.source_view.has_focus()

    def _navigate_section(self, direction):
        sidebar = getattr(self.workspace, 'sidebar', None)
        if sidebar is None:
            return
        if getattr(self.workspace, 'show_symbols', False):
            page = sidebar.symbols_page
        else:
            page = sidebar.document_structure_page
        if direction < 0:
            page.on_prev_button_clicked(None)
        else:
            page.on_next_button_clicked(None)



#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic
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


class ShortcutController(Gtk.ShortcutController):

    def __init__(self):
        Gtk.ShortcutController.__init__(self)

    def _parse_trigger(self, trigger_string):
        try:
            trigger = Gtk.ShortcutTrigger.parse_string(trigger_string)
            if trigger is not None:
                return trigger
        except TypeError:
            pass

        try:
            _success, keyval, mods = Gtk.accelerator_parse(trigger_string)
            if _success:
                return Gtk.KeyvalTrigger.new(keyval, mods)
        except Exception:
            pass

        return None

    def create_and_add_shortcut(self, trigger_string, callback):
        shortcut = Gtk.Shortcut()

        shortcut.set_action(Gtk.CallbackAction.new(self.action, callback))

        trigger = self._parse_trigger(trigger_string)
        if trigger is not None:
            shortcut.set_trigger(trigger)

        self.add_shortcut(shortcut)

    def action(self, a, b, callback):
        result = callback()
        # 返回 False 表示本快捷键未处理，事件继续向下传播（如源码视图中的
        # Alt+Up/Down 由 document 控制器接管，用于移动行）。
        if result is False:
            return False
        return True



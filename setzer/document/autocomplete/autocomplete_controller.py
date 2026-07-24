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
from gi.repository import Gtk, Gdk

from setzer.app.service_locator import ServiceLocator


# on_keypress 在 autocomplete 激活时每次按键都跑，原实现每次调
# Gdk.keyval_from_name 做 C 查表。模块级预计算为整数常量后热路径只做整数比较。
_KEYVAL_TAB = Gdk.keyval_from_name('Tab')
_KEYVAL_ISO_LEFT_TAB = Gdk.keyval_from_name('ISO_Left_Tab')
_KEYVAL_RETURN = Gdk.keyval_from_name('Return')
_KEYVAL_ESCAPE = Gdk.keyval_from_name('Escape')
_KEYVAL_DOWN = Gdk.keyval_from_name('Down')
_KEYVAL_UP = Gdk.keyval_from_name('Up')
_KEYVAL_PAGE_DOWN = Gdk.keyval_from_name('Page_Down')
_KEYVAL_PAGE_UP = Gdk.keyval_from_name('Page_Up')


class AutocompleteController(object):

    def __init__(self, autocomplete, document):
        self.autocomplete = autocomplete
        self.document = document

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

    def on_keypress(self, controller, keyval, keycode, state):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if keyval in [_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB]:
            if state & modifiers == 0:
                if self.autocomplete.is_active:
                    self.autocomplete.tab()
                    return True
                else:
                    self.autocomplete.activate_if_possible()
                    if self.autocomplete.is_active:
                        return True

        if (state & modifiers, keyval) == (0, _KEYVAL_RETURN):
            if self.autocomplete.is_active:
                self.autocomplete.submit()
                return True

        if (state & modifiers, keyval) == (0, _KEYVAL_ESCAPE):
            self.autocomplete.deactivate()
            return True

        if (state & modifiers, keyval) == (0, _KEYVAL_DOWN):
            if self.autocomplete.is_active:
                self.autocomplete.select_next()
                return True

        if (state & modifiers, keyval) == (0, _KEYVAL_UP):
            if self.autocomplete.is_active:
                self.autocomplete.select_previous()
                return True

        if (state & modifiers, keyval) == (0, _KEYVAL_PAGE_DOWN):
            if self.autocomplete.is_active:
                self.autocomplete.page_down()
                return True

        if (state & modifiers, keyval) == (0, _KEYVAL_PAGE_UP):
            if self.autocomplete.is_active:
                self.autocomplete.page_up()
                return True

        return False



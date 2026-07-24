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

import os.path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk, GObject, Pango

from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


# on_keypress 每次按键都跑，Gdk.keyval_from_name 模块级预计算避免每次 C 查表。
_KEYVAL_TAB = Gdk.keyval_from_name('Tab')
_KEYVAL_ISO_LEFT_TAB = Gdk.keyval_from_name('ISO_Left_Tab')


class DocumentController(object):
    
    def __init__(self, document, document_view):

        self.document = document
        self.view = document_view

        self.deleted_on_disk_dialog_shown_after_last_save = False
        self.changed_on_disk_dialog_shown_after_last_change = False
        self.continue_save_date_loop = True
        self.zoom_threshold = 0
        # 保存 timeout id 以便文档关闭时移除。原实现仅置 continue_save_date_loop=False，
        # 定时器仍会再触发一次才退出；直接 remove 更及时。
        # 2000ms 而非 500ms：检测外部磁盘变更不需要亚秒级响应，2 秒足够
        # （VS Code/gedit/Kate 均用 2–5 秒）。per-document stat I/O 降低 75%。
        self._save_date_loop_timeout_id = GObject.timeout_add(2000, self.save_date_loop)

        self.primary_click_controller = Gtk.GestureClick()
        self.primary_click_controller.set_button(1)
        self.primary_click_controller.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self.primary_click_controller.connect('pressed', self.on_primary_buttonpress)
        self.view.source_view.add_controller(self.primary_click_controller)

        self.secondary_click_controller = Gtk.GestureClick()
        self.secondary_click_controller.set_button(3)
        self.secondary_click_controller.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self.secondary_click_controller.connect('pressed', self.on_secondary_buttonpress)
        self.view.source_view.add_controller(self.secondary_click_controller)

        self.scrolling_controller = Gtk.EventControllerScroll()
        self.scrolling_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.scrolling_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.KINETIC)
        self.scrolling_controller.connect('scroll', self.on_scroll)
        self.scrolling_controller.connect('decelerate', self.on_decelerate)
        self.view.scrolled_window.add_controller(self.scrolling_controller)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用，移除 500ms 轮询定时器。'''
        self.continue_save_date_loop = False
        if self._save_date_loop_timeout_id is not None:
            GLib.Source.remove(self._save_date_loop_timeout_id)
            self._save_date_loop_timeout_id = None

    def on_primary_buttonpress(self, controller, n_press, x, y):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
                GLib.idle_add(ServiceLocator.get_workspace().actions.forward_sync)

    def on_secondary_buttonpress(self, controller, n_press, x, y):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            ServiceLocator.get_workspace().context_menu.popup_at_cursor(x, y)
        controller.reset()

    def on_keypress(self, controller, keyval, keycode, state):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if keyval in [_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB]:
            if state & modifiers == 0:
                self.document.select_next_placeholder()
                if self.document.dot_selected():
                    return True

                if not self.document.settings.get_value('preferences', 'tab_jump_brackets'): return False
                chars_at_cursor = self.document.get_chars_at_cursor(2)
                if chars_at_cursor in ['\\}', '\\)', '\\]']: forward_chars = 2
                elif chars_at_cursor[0] in ['}', ')', ']']: forward_chars = 1
                else: return False

                insert_iter = self.document.source_buffer.get_iter_at_mark(self.document.source_buffer.get_insert())
                insert_iter.forward_chars(forward_chars)
                self.document.source_buffer.place_cursor(insert_iter)
                return True

        if (state & modifiers, keyval) == (Gdk.ModifierType.SHIFT_MASK, _KEYVAL_ISO_LEFT_TAB):
            self.document.select_previous_placeholder()
            if self.document.dot_selected():
                return True

        return False

    def on_scroll(self, controller, dx, dy):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
            if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
                self.zoom_threshold += dy
            else:
                self.zoom_threshold += dy * 0.05

            if self.zoom_threshold <= -1:
                font_desc = Pango.FontDescription.from_string(FontManager.font_string)
                font_desc.set_size(min(font_desc.get_size() * 1.1, 24 * Pango.SCALE))
                FontManager.font_string = font_desc.to_string()
                FontManager.propagate_font_setting()
                self.zoom_threshold = 0
            elif self.zoom_threshold >= 1:
                font_desc = Pango.FontDescription.from_string(FontManager.font_string)
                font_desc.set_size(max(font_desc.get_size() / 1.1, 6 * Pango.SCALE))
                FontManager.font_string = font_desc.to_string()
                FontManager.propagate_font_setting()
                self.zoom_threshold = 0
            return True
        return False

    def on_decelerate(self, controller, vel_x, vel_y):
        self.zoom_threshold = 0

    def save_date_loop(self):
        if self.document.filename == None: return True
        if self.deleted_on_disk_dialog_shown_after_last_save: return True
        if self.changed_on_disk_dialog_shown_after_last_change:
            return True

        if self.document.get_deleted_on_disk():
            self.deleted_on_disk_dialog_shown_after_last_save = True
            self.document.source_buffer.set_modified(True)
            DialogLocator.get_dialog('document_deleted_on_disk').run({'document': self.document})
        elif self.document.get_changed_on_disk():
            self.changed_on_disk_dialog_shown_after_last_change = True
            DialogLocator.get_dialog('document_changed_on_disk').run({'document': self.document}, self.changed_on_disk_cb)

        return self.continue_save_date_loop

    def changed_on_disk_cb(self, do_reload):
        if do_reload:
            self.document.populate_from_filename()
            self.document.source_buffer.set_modified(False)
        else:
            self.document.source_buffer.set_modified(True)
        self.changed_on_disk_dialog_shown_after_last_change = False
        self.document.update_save_date()



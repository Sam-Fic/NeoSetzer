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

import setzer.document.autocomplete.autocomplete_widget_viewgtk as autocomplete_view
from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


class AutocompleteWidget(object):

    def __init__(self, model):
        self.main_window = ServiceLocator.get_main_window()
        self.model = model
        self.document = self.model.document
        self.source_view = self.document.view.source_view
        self.source_buffer = self.model.document.source_buffer

        self.view = autocomplete_view.AutocompleteWidgetView(self)

        self.line_height = FontManager.get_line_height(self.source_view)
        self.char_width = FontManager.get_char_width(self.source_view)
        # 字体度量缓存：line_height / char_width 仅在 FontManager.font_string
        # 变化（字体/缩放改变）时重算，与 gutter.py 的缓存范式一致。
        self._last_font_string = FontManager.font_string
        self.height = None
        self.shortcutsbar_height = None
        self.x_position, self.y_position = (None, None)
        self.focus_hide = self.model.document.source_view.has_focus()
        # max_chars 缓存：queue_draw 在每次滚动/光标移动时调用 update_size →
        # get_max_chars，后者遍历全部 items（可能上百项）。但 max_chars 仅在
        # items 内容变化时改变，用 items 对象身份做缓存键避免重复遍历。
        self._max_chars_items = None
        self._max_chars_cache = 0

        self.focus_controller = Gtk.EventControllerFocus()
        self.focus_controller.connect('enter', self.on_focus_in)
        self.focus_controller.connect('leave', self.on_focus_out)
        self.model.document.source_view.add_controller(self.focus_controller)

        self.queue_draw()

    def on_focus_out(self, widget):
        self.focus_hide = True
        self.queue_draw()

    def on_focus_in(self, widget):
        self.focus_hide = False
        self.queue_draw()

    def queue_draw(self):
        self.update_size()
        self.update_position()
        self.update_margins()

        self.view.set_visible(self.model.is_active and self.position_is_visible() and not self.focus_hide)
        self.view.populate()

    def update_size(self):
        font_string = FontManager.font_string
        if font_string != self._last_font_string:
            self._last_font_string = font_string
            self.line_height = FontManager.get_line_height(self.source_view)
            self.char_width = FontManager.get_char_width(self.source_view)
        self.shortcutsbar_height = self.main_window.shortcutsbar.get_allocated_height()

        if self.model.items != None:
            self.height = min(len(self.model.items), 5) * self.line_height
            self.width = (5 + min(max(self.get_max_chars(), 25), 45)) * self.char_width
            self.view.set_size_request(self.width, self.height)

    def get_max_chars(self):
        items = self.model.items
        if items is None or len(items) == 0:
            return 0
        # items 在 update_suggestions 中被整体替换（self.items = ...），不就地修改。
        # 用 is（身份比较）做缓存键：同一对象直接返回缓存，新对象才重新遍历。
        # queue_draw 每次滚动都跑，但 items 仅在补全词变化时才换。
        if items is self._max_chars_items:
            return self._max_chars_cache
        self._max_chars_items = items
        self._max_chars_cache = max(len(item['command']) + len(item['dotlabels']) - 4 * item['dotlabels'].count('###') for item in items)
        return self._max_chars_cache

    def update_position(self):
        start_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        if self.model.current_word != None:
            start_iter.backward_chars(len(self.model.current_word))

        iter_location = self.source_view.get_iter_location(start_iter)
        x_offset = - self.document.view.scrolled_window.get_hadjustment().get_value()
        x_offset += self.document.view.margin.get_allocated_width()
        y_offset = - self.document.view.scrolled_window.get_vadjustment().get_value()
        self.x_position = x_offset + iter_location.x
        self.y_position = y_offset + iter_location.y + self.line_height

    def update_margins(self):
        vertical_cutoff = self.document.view.scrolled_window.get_allocated_height() - self.height - self.line_height
        horizontal_cutoff = self.main_window.preview_split.get_allocated_width() - self.view.get_allocated_width()

        if self.y_position >= self.line_height and self.y_position <= vertical_cutoff:
            self.view.set_margin_top(self.y_position + self.shortcutsbar_height)
        else:
            self.view.set_margin_top(self.y_position + self.shortcutsbar_height - self.height - self.line_height)

        if self.x_position >= 0 and self.x_position <= horizontal_cutoff:
            self.view.set_margin_start(self.x_position)
        else:
            self.view.set_margin_start(self.main_window.preview_split.get_allocated_width() - self.view.get_allocated_width())

    def position_is_visible(self):
        return ((self.y_position >= self.line_height) and
            (self.y_position <= self.document.view.scrolled_window.get_allocated_height()) and
            (self.x_position >= 0) and
            (self.x_position < self.main_window.preview_split.get_allocated_width()))

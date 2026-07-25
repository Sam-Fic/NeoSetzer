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

from setzer.widgets.search_entry.search_entry import SearchEntry


class SearchBar(Gtk.SearchBar):
    ''' Find / replace bar for the document editor.

    Built on the native ``Gtk.SearchBar``: it provides the standard reveal
    animation and search-bar chrome via ``set_search_mode``. The
    content is a vertical box holding the find row (entry + prev/next/close)
    and an optional replace row. The match counter is overlaid on the right
    side of the entry (a fixed margin, no dynamic width math).
    '''

    def __init__(self):
        Gtk.SearchBar.__init__(self)
        self.set_show_close_button(False)
        self.set_search_mode(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_spacing(6)
        content.set_margin_top(6)
        content.set_margin_bottom(6)
        content.set_margin_start(6)
        content.set_margin_end(6)

        # --- find row ---
        find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        find_row.set_spacing(6)

        # 左侧占位：与 replace 行的箭头等宽，使两行 entry 左缘对齐。
        find_spacer = Gtk.Box()
        find_spacer.set_size_request(16, -1)
        find_row.append(find_spacer)

        self.entry = SearchEntry()
        self.entry.set_hexpand(True)

        # Overlay the match counter at the right inside the entry.
        self.entry_overlay = Gtk.Overlay()
        self.entry_overlay.set_child(self.entry)
        self.match_counter = Gtk.Label()
        self.match_counter.set_halign(Gtk.Align.END)
        self.match_counter.set_valign(Gtk.Align.CENTER)
        self.match_counter.set_margin_end(6)
        self.match_counter.add_css_class('dim-label')
        self.entry_overlay.add_overlay(self.match_counter)
        find_row.append(self.entry_overlay)

        # --- match-option toggles (case / regex / whole-word) ---
        # 与 gedit / GNOME Text Editor 一致的右侧一排开关。
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        options_box.set_spacing(2)
        options_box.set_valign(Gtk.Align.CENTER)

        self.case_toggle = Gtk.ToggleButton.new_with_label('Aa')
        self.case_toggle.set_can_focus(False)
        self.case_toggle.set_hexpand(False)
        self.case_toggle.add_css_class('flat')
        self.case_toggle.add_css_class('search-toggle')
        self.case_toggle.set_tooltip_text(_('Match case'))
        options_box.append(self.case_toggle)

        self.regex_toggle = Gtk.ToggleButton.new_with_label('.*')
        self.regex_toggle.set_can_focus(False)
        self.regex_toggle.set_hexpand(False)
        self.regex_toggle.add_css_class('flat')
        self.regex_toggle.add_css_class('search-toggle')
        self.regex_toggle.set_tooltip_text(_('Use regular expressions'))
        options_box.append(self.regex_toggle)

        self.word_toggle = Gtk.ToggleButton.new_with_label('W')
        self.word_toggle.set_can_focus(False)
        self.word_toggle.set_hexpand(False)
        self.word_toggle.add_css_class('flat')
        self.word_toggle.add_css_class('search-toggle')
        self.word_toggle.set_tooltip_text(_('Match entire word'))
        options_box.append(self.word_toggle)

        self.selection_toggle = Gtk.ToggleButton.new_with_label('S')
        self.selection_toggle.set_can_focus(False)
        self.selection_toggle.set_hexpand(False)
        self.selection_toggle.add_css_class('flat')
        self.selection_toggle.add_css_class('search-toggle')
        self.selection_toggle.set_tooltip_text(_('Search in selection'))
        self.selection_toggle.set_visible(False)
        options_box.append(self.selection_toggle)

        find_row.append(options_box)

        self.prev_button = Gtk.Button(icon_name='go-up-symbolic')
        self.prev_button.set_can_focus(False)
        self.prev_button.set_tooltip_text(_('Previous result') + ' (Ctrl+Shift+G)')
        find_row.append(self.prev_button)

        self.next_button = Gtk.Button(icon_name='go-down-symbolic')
        self.next_button.set_can_focus(False)
        self.next_button.set_tooltip_text(_('Next result') + ' (Ctrl+G)')
        find_row.append(self.next_button)

        self.close_button = Gtk.Button(icon_name='window-close-symbolic')
        self.close_button.add_css_class('flat')
        self.close_button.set_can_focus(False)
        self.close_button.set_tooltip_text(_('Close search') + ' (Esc)')
        find_row.append(self.close_button)

        content.append(find_row)

        # --- replace row (revealed only in replace mode) ---
        # 用 Revealer 包住替换行，使其在切换 replace 模式时随 SearchBar
        # 一起平滑滑出/收起，而不是生硬地显隐。
        self.replace_revealer = Gtk.Revealer()
        self.replace_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.replace_revealer.set_transition_duration(200)

        self.replace_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.replace_wrapper.set_spacing(6)

        self.arrow = Gtk.Image(icon_name='go-next-symbolic')
        self.arrow.set_pixel_size(16)
        self.replace_wrapper.append(self.arrow)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_width_chars(4)
        self.replace_entry.set_hexpand(True)
        self.replace_entry.set_placeholder_text(_('Type replacement text…'))
        self.replace_wrapper.append(self.replace_entry)

        self.replace_button = Gtk.Button.new_with_label(_('Replace'))
        self.replace_button.set_can_focus(False)
        self.replace_button.set_tooltip_text(_('Replace selected result'))
        self.replace_button.set_sensitive(False)
        self.replace_wrapper.append(self.replace_button)

        self.replace_all_button = Gtk.Button.new_with_label(_('Replace All'))
        self.replace_all_button.set_can_focus(False)
        self.replace_all_button.set_tooltip_text(_('Replace all results'))
        self.replace_all_button.set_sensitive(False)
        self.replace_wrapper.append(self.replace_all_button)

        self.replace_revealer.set_child(self.replace_wrapper)
        content.append(self.replace_revealer)

        self.set_child(content)

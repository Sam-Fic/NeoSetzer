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
        # 间距改为 0：find/replace 两行之间的间距由 find_row 动态下边距控制，
        # 否则 replace_revealer 未展开时仍会占据 content 的 6px 间距，导致搜索
        # 栏上下外边距不对称。
        content.set_spacing(0)
        content.set_margin_top(6)
        content.set_margin_bottom(6)
        content.set_margin_start(6)
        content.set_margin_end(6)

        # --- find row ---
        self.find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.find_row.set_spacing(6)
        self.find_row.set_margin_bottom(0)

        # 左侧「搜索/替换」模式切换按钮（替代原来的固定箭头占位）。
        # 点击展开/收起 replace 行；图标与 replace 行左侧箭头一致，按下时表示
        # 当前处于替换模式。
        self.replace_mode_button = Gtk.ToggleButton()
        self.replace_mode_button.set_child(Gtk.Image(icon_name='go-next-symbolic'))
        self.replace_mode_button.set_can_focus(False)
        self.replace_mode_button.add_css_class('flat')
        self.replace_mode_button.set_tooltip_text(_('Search and replace'))
        self.replace_mode_button.connect('notify::active', self._on_replace_mode_active_notify)
        self.find_row.append(self.replace_mode_button)

        self.entry = SearchEntry()
        self.entry.set_hexpand(True)

        # Overlay the match counter at the right inside the entry.
        self.entry_overlay = Gtk.Overlay()
        self.entry_overlay.set_child(self.entry)
        self.match_counter = Gtk.Label()
        self.match_counter.set_halign(Gtk.Align.END)
        self.match_counter.set_valign(Gtk.Align.CENTER)
        # 默认仅靠右不贴边；当 entry 有内容、Gtk.SearchEntry 显示「清空」按钮时，
        # 由 _update_counter_margin 调大右边距避开它（见下方连接）。
        self.match_counter.set_margin_end(12)
        self.match_counter.add_css_class('dim-label')
        self.entry_overlay.add_overlay(self.match_counter)
        self.entry.connect('notify::text', self._update_counter_margin)
        self._update_counter_margin()
        self.find_row.append(self.entry_overlay)

        # --- match-option toggles (case / regex / whole-word / preserve-case) ---
        # 与 gedit / GNOME Text Editor 一致的右侧一排开关。
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        options_box.set_spacing(6)
        options_box.set_valign(Gtk.Align.CENTER)

        self.case_toggle = Gtk.ToggleButton()
        self.case_toggle.set_child(Gtk.Image(icon_name='xapp-text-case-symbolic'))
        self.case_toggle.set_can_focus(False)
        self.case_toggle.set_hexpand(False)
        self.case_toggle.add_css_class('flat')
        self.case_toggle.add_css_class('search-toggle')
        self.case_toggle.set_tooltip_text(_('Match case'))
        options_box.append(self.case_toggle)

        self.regex_toggle = Gtk.ToggleButton()
        self.regex_toggle.set_child(Gtk.Image(icon_name='xsi-use-regex-symbolic'))
        self.regex_toggle.set_can_focus(False)
        self.regex_toggle.set_hexpand(False)
        self.regex_toggle.add_css_class('flat')
        self.regex_toggle.add_css_class('search-toggle')
        self.regex_toggle.set_tooltip_text(_('Use regular expressions'))
        options_box.append(self.regex_toggle)

        self.word_toggle = Gtk.ToggleButton()
        self.word_toggle.set_child(Gtk.Image(icon_name='completion-word-symbolic'))
        self.word_toggle.set_can_focus(False)
        self.word_toggle.set_hexpand(False)
        self.word_toggle.add_css_class('flat')
        self.word_toggle.add_css_class('search-toggle')
        self.word_toggle.set_tooltip_text(_('Match entire word'))
        options_box.append(self.word_toggle)

        self.preserve_case_toggle = Gtk.ToggleButton()
        self.preserve_case_toggle.set_child(Gtk.Image(icon_name='font-select-symbolic'))
        self.preserve_case_toggle.set_can_focus(False)
        self.preserve_case_toggle.set_hexpand(False)
        self.preserve_case_toggle.add_css_class('flat')
        self.preserve_case_toggle.add_css_class('search-toggle')
        self.preserve_case_toggle.set_tooltip_text(_('Preserve case'))
        options_box.append(self.preserve_case_toggle)

        self.selection_toggle = Gtk.ToggleButton.new_with_label('S')
        self.selection_toggle.set_can_focus(False)
        self.selection_toggle.set_hexpand(False)
        self.selection_toggle.add_css_class('flat')
        self.selection_toggle.add_css_class('search-toggle')
        self.selection_toggle.set_tooltip_text(_('Search in selection'))
        self.selection_toggle.set_visible(False)
        options_box.append(self.selection_toggle)

        self.find_row.append(options_box)

        self.prev_button = Gtk.Button(icon_name='go-up-symbolic')
        self.prev_button.set_can_focus(False)
        self.prev_button.set_tooltip_text(_('Previous result') + ' (Ctrl+Shift+G)')
        self.find_row.append(self.prev_button)

        self.next_button = Gtk.Button(icon_name='go-down-symbolic')
        self.next_button.set_can_focus(False)
        self.next_button.set_tooltip_text(_('Next result') + ' (Ctrl+G)')
        self.find_row.append(self.next_button)

        self.close_button = Gtk.Button(icon_name='window-close-symbolic')
        self.close_button.add_css_class('flat')
        self.close_button.set_can_focus(False)
        self.close_button.set_tooltip_text(_('Close search') + ' (Esc)')
        self.find_row.append(self.close_button)

        content.append(self.find_row)

        # --- replace row (revealed only in replace mode) ---
        # 用 Revealer 包住替换行，使其在切换 replace 模式时随 SearchBar
        # 一起平滑滑出/收起，而不是生硬地显隐。
        self.replace_revealer = Gtk.Revealer()
        self.replace_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.replace_revealer.set_transition_duration(200)

        self.replace_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.replace_wrapper.set_spacing(6)

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

        self.replace_revealer.connect('notify::reveal-child', self._on_replace_revealed)
        self._on_replace_revealed()

        self.set_child(content)

    def _on_replace_mode_active_notify(self, button, gparam):
        '''切换按钮图标：未展开时显示向右箭头，展开后显示向下箭头。'''
        icon_name = 'go-down-symbolic' if button.get_active() else 'go-next-symbolic'
        button.get_child().set_from_icon_name(icon_name)

    def _update_counter_margin(self, *args):
        '''根据 entry 是否有内容动态调整计数提示的右外边距。

        Gtk.SearchEntry 仅在内容非空时显示右侧「清空」按钮；有按钮时把计数
        提示右移避开重叠，无按钮（空内容）时仅保持 12px 不贴边。'''
        if len(self.entry.get_text()) > 0:
            self.match_counter.set_margin_end(36)
        else:
            self.match_counter.set_margin_end(12)

    def _on_replace_revealed(self, *args):
        '''Replace 行展开/收起时动态控制 find_row 的下边距。

        收起时设为 0，避免 content 内部在 find_row 与隐藏的 replace_revealer 之间
        留下多余间距，保证搜索栏上下绿框区域等高；展开时恢复 6px 间距。'''
        if self.replace_revealer.get_reveal_child():
            self.find_row.set_margin_bottom(6)
        else:
            self.find_row.set_margin_bottom(0)

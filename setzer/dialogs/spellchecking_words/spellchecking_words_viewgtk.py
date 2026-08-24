#!/usr/bin/env python3
# coding: utf-8

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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class SpellCheckingWordsView(DialogView):
    '''管理「用户词典」与「会话忽略词」的对话框视图。

    两组词表都是动态列表：行由控制器按需增删，本视图只负责静态部分
    （标题栏、添加输入框、清空按钮）与空的动态列表容器。
    '''

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        self.set_content_width(440)
        self.set_content_height(560)
        self.headerbar.set_title_widget(Adw.WindowTitle(title=_('Spell Checking Words')))
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.close_button = Gtk.Button.new_with_mnemonic(_('_Close'))
        self.headerbar.pack_start(self.close_button)

        self.save_button = Gtk.Button.new_with_mnemonic(_('_Save'))
        self.save_button.add_css_class('suggested-action')
        self.headerbar.pack_end(self.save_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # --- 用户词典（持久化到 ~/.config/setzer/spellchecking_pwl.txt）---
        self.dictionary_group = Adw.PreferencesGroup()
        self.dictionary_group.set_title(_('User Dictionary'))
        self.dictionary_group.set_description(_(
            'Words stored permanently in your personal dictionary. '
            'They are saved to a file and will never be marked as misspelled.'))

        self.dictionary_add_entry = Adw.EntryRow()
        self.dictionary_add_entry.set_title(_('Add a word'))
        self.dictionary_add_button = Gtk.Button(label=_('Add'))
        self.dictionary_add_button.set_valign(Gtk.Align.CENTER)
        self.dictionary_add_button.add_css_class('suggested-action')
        self.dictionary_add_entry.add_suffix(self.dictionary_add_button)
        self.dictionary_group.add(self.dictionary_add_entry)
        content.append(self.dictionary_group)

        # --- 会话忽略词（仅本次会话，不落盘）---
        self.ignored_group = Adw.PreferencesGroup()
        self.ignored_group.set_title(_('Ignored Words'))
        self.ignored_group.set_description(_(
            'Words ignored for the current session. '
            'They are not saved and will be checked again after restart.'))

        self.ignored_clear_button = Gtk.Button(label=_('Clear All'))
        self.ignored_clear_button.set_halign(Gtk.Align.END)
        self.ignored_clear_button.set_margin_top(8)
        self.ignored_clear_button.set_margin_bottom(4)
        self.ignored_group.add(self.ignored_clear_button)
        content.append(self.ignored_group)

        # 词表可能很长：垂直方向放进滚动容器，避免对话框撑满全屏。
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(content)
        self.toolbar_view.set_content(scroll)

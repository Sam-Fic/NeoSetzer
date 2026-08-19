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

import setzer.dialogs.spellchecking_words.spellchecking_words_viewgtk as view_module
from setzer.document.spellchecking.spellchecking import SpellChecker


class SpellCheckingWordsDialog(object):
    '''管理「用户词典」与「会话忽略词」的对话框。

    编辑先缓冲在本地模型（_dict_words / _ignored_words），只有点击右上角
    Save 才整体写入数据层（并触发文档重查）；点击关闭/ESC 则放弃修改。
    '''

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = view_module.SpellCheckingWordsView(main_window)
        self._dict_words = set()
        self._ignored_words = set()
        self._dictionary_rows = []
        self._ignored_rows = []

        self.view.save_button.connect('clicked', self.on_save_clicked)
        self.view.close_button.connect('clicked', lambda *a: self.view.close())
        self.view.dictionary_add_button.connect('clicked', self.on_add_clicked)
        self.view.dictionary_add_entry.connect('activate', self.on_add_clicked)
        self.view.ignored_clear_button.connect('clicked', self.on_clear_ignored_clicked)

    def run(self):
        '''每次打开时从数据层重新加载，丢弃上次未保存的缓冲。'''
        self._dict_words = set(SpellChecker.get_user_dictionary_words())
        self._ignored_words = set(SpellChecker.get_session_ignored_words())
        self.refresh_dictionary()
        self.refresh_ignored()
        self.view.present(self.main_window)

    def on_save_clicked(self, button):
        '''将缓冲的改动整体提交到数据层，随后关闭对话框。'''
        SpellChecker.set_user_dictionary_words(self._dict_words)
        SpellChecker.set_session_ignored_words(self._ignored_words)
        self.view.close()

    # ---- 用户词典 ----

    def on_add_clicked(self, widget):
        word = self.view.dictionary_add_entry.get_text().strip()
        if not word:
            return
        low = word.lower()
        self._dict_words = {w for w in self._dict_words if w.lower() != low}
        self._dict_words.add(word)
        self.view.dictionary_add_entry.set_text('')
        self.refresh_dictionary()

    def on_remove_dictionary_clicked(self, button, word):
        self._dict_words.discard(word)
        self.refresh_dictionary()

    def refresh_dictionary(self):
        for row in self._dictionary_rows:
            self.view.dictionary_group.remove(row)
        self._dictionary_rows = []
        if not self._dict_words:
            self._add_empty_row(self.view.dictionary_group, self._dictionary_rows,
                                _('Your user dictionary is empty.'))
        for word in sorted(self._dict_words, key=str.lower):
            row = self._make_word_row(word, _('Remove from dictionary'),
                                      'user-trash-symbolic',
                                      self.on_remove_dictionary_clicked)
            self.view.dictionary_group.add(row)
            self._dictionary_rows.append(row)

    # ---- 会话忽略词 ----

    def on_unignore_clicked(self, button, word):
        self._ignored_words.discard(word)
        self.refresh_ignored()

    def on_clear_ignored_clicked(self, button):
        self._ignored_words.clear()
        self.refresh_ignored()

    def refresh_ignored(self):
        for row in self._ignored_rows:
            self.view.ignored_group.remove(row)
        self._ignored_rows = []
        if not self._ignored_words:
            self._add_empty_row(self.view.ignored_group, self._ignored_rows,
                                _('No words ignored this session.'))
        for word in sorted(self._ignored_words):
            row = self._make_word_row(word, _('Stop ignoring'),
                                      'user-trash-symbolic',
                                      self.on_unignore_clicked)
            self.view.ignored_group.add(row)
            self._ignored_rows.append(row)

    # ---- 通用行构造 ----

    def _make_word_row(self, word, tooltip, icon_name, callback):
        row = Adw.ActionRow()
        row.set_title(word)
        row.set_activatable(False)
        button = Gtk.Button()
        button.set_valign(Gtk.Align.CENTER)
        button.set_icon_name(icon_name)
        button.set_tooltip_text(tooltip)
        button.add_css_class('flat')
        button.connect('clicked', callback, word)
        row.add_suffix(button)
        return row

    def _add_empty_row(self, group, rows, text):
        row = Adw.ActionRow()
        row.set_title(text)
        row.set_activatable(False)
        group.add(row)
        rows.append(row)

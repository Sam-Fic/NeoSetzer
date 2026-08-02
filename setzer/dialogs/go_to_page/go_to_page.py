#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from gi.repository import Adw, Gtk


class GoToPageDialog(object):
    '''预览面板"跳转到页面"对话框，模式与 GoToLineDialog 一致。'''

    def __init__(self, main_window):
        self.main_window = main_window
        self.page_count = 0
        self.callback = None

    def run(self, page_count, callback):
        self.page_count = page_count
        self.callback = callback
        self._setup()
        self.view.choose(self.main_window, None, self._dialog_process_response)

    def _setup(self):
        self.view = Adw.AlertDialog(
            heading=_('Go to Page'),
            body=_('Enter a page number (1–{pages}).').format(pages=self.page_count))
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('go', _('Go'))
        self.view.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('go')
        self.view.set_close_response('cancel')

        entry = Gtk.Entry()
        entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        entry.set_placeholder_text(_('Page number'))
        entry.connect('insert-text', self._on_insert_text)
        entry.connect('activate', lambda e: self.view.response('go'))
        self.entry = entry
        self.view.set_extra_child(entry)

    def _on_insert_text(self, entry, text, length, position):
        filtered = ''.join(ch for ch in text if ch.isdigit())
        if filtered != text:
            entry.stop_emission_by_name('insert-text')
            if filtered:
                pos = entry.get_position()
                entry.insert_text(filtered, pos)

    def _dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id != 'go':
            return
        text = self.entry.get_text().strip()
        if not text:
            return
        try:
            page = int(text)
        except ValueError:
            return
        if page < 1 or page > self.page_count:
            return
        self.callback(page)

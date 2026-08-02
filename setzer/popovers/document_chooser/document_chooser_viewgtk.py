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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
from setzer.widgets.search_highlight import highlight


class DocumentChooserView(object):

    def __init__(self):
        self.dialog = Adw.PreferencesDialog()
        self.dialog.set_title(_('Open Document'))
        self.dialog.set_content_width(420)
        self.dialog.set_content_height(480)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_focusable(True)
        self.search_entry.set_hexpand(True)
        self.search_group = Adw.PreferencesGroup()
        self.search_group.add(self.search_entry)

        self.page = Adw.PreferencesPage()
        self.page.set_title(_('Recent Documents'))
        self.page.add(self.search_group)

        self.group = Adw.PreferencesGroup()
        self.page.add(self.group)

        self.no_results_group = Adw.PreferencesGroup()
        self.no_results_status = Adw.StatusPage()
        self.no_results_status.set_icon_name('edit-find-symbolic')
        self.no_results_status.set_title(_('No Results'))
        self.no_results_status.set_description(_('No documents match your search'))
        self.no_results_group.add(self.no_results_status)
        self.no_results_group.set_visible(False)
        self.page.add(self.no_results_group)

        self.other_group = Adw.PreferencesGroup()
        self.other_documents_row = Adw.ActionRow()
        self.other_documents_row.set_activatable(True)
        self.other_documents_row.set_title(_('Other Documents') + '...')
        self.other_documents_row.set_icon_name('document-open-symbolic')
        self.other_group.add(self.other_documents_row)
        self.page.add(self.other_group)

        self.dialog.add(self.page)

        self.items = []
        self.rows = []
        self.selected_row = None

    def update_items(self, items):
        self.items = []
        for folder, filename in items:
            self.items.append((folder, filename))

        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        for folder, filename in self.items:
            row = self.create_row(folder, filename)
            self.group.add(row)
            self.rows.append(row)
        self.search_filter()

    def create_row(self, folder, filename):
        row = Adw.ActionRow()
        row.set_activatable(True)
        # use-markup 让标题/副标题渲染 Pango markup，供搜索命中加粗使用。
        row.set_use_markup(True)
        row.set_title(highlight(filename, ''))
        row.set_subtitle(highlight(folder, ''))
        row.folder = folder
        row.filename = filename
        return row

    def search_filter(self):
        query_raw = self.search_entry.get_text()
        query = query_raw.lower()
        count = 0
        for row in self.rows:
            match = (query == '' or
                     query in row.filename.lower() or
                     query in row.folder.lower())
            if match:
                # 搜索命中高亮：标题(文件名)/副标题(目录)中匹配子串加粗。
                row.set_title(highlight(row.filename, query_raw))
                row.set_subtitle(highlight(row.folder, query_raw))
            row.set_visible(match)
            if match:
                count += 1
        if count == 0 and query != '':
            self.group.set_visible(False)
            self.no_results_group.set_visible(True)
        else:
            self.group.set_visible(True)
            self.no_results_group.set_visible(False)
        return count

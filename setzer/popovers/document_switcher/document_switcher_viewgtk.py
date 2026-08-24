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
# along with this program. If not see <http://www.gnu.org/licenses/>.

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
from setzer.widgets.search_highlight import highlight_fuzzy

import os.path

from setzer.app.service_locator import ServiceLocator


class DocumentSwitcherView(object):

    def __init__(self):
        self.dialog = Adw.PreferencesDialog()
        self.dialog.set_title(_('Open Documents'))
        self.dialog.set_content_width(420)
        self.dialog.set_content_height(480)

        self.page = Adw.PreferencesPage()

        # 搜索框：同时过滤已打开和最近文档。
        self.search_group = Adw.PreferencesGroup()
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_('Search documents…'))
        self.search_entry.set_hexpand(True)
        self.search_entry.set_margin_bottom(6)
        self.query = ''
        self.search_group.add(self.search_entry)
        self.page.add(self.search_group)

        # 已打开文档列表。
        self.open_group = Adw.PreferencesGroup()
        self.page.add(self.open_group)

        # 最近文档列表（自动排除已打开的文档）。
        self.recent_group = Adw.PreferencesGroup()
        self.page.add(self.recent_group)

        # 根文档操作：仅在 selection 模式下显示。
        self.root_group = Adw.PreferencesGroup()
        self.set_root_document_row = Adw.ActionRow()
        self.set_root_document_row.set_activatable(True)
        self.set_root_document_row.set_title(_('Set one Document as Root'))
        self.set_root_document_row.set_icon_name('document-properties-symbolic')
        self.set_root_document_row.set_tooltip_text(_('Designate a document as the build root'))
        self.root_group.add(self.set_root_document_row)

        self.unset_root_document_row = Adw.ActionRow()
        self.unset_root_document_row.set_activatable(True)
        self.unset_root_document_row.set_title(_('Unset Root Document'))
        self.unset_root_document_row.set_icon_name('edit-clear-symbolic')
        self.unset_root_document_row.set_tooltip_text(_('Remove the root document designation'))
        self.root_group.add(self.unset_root_document_row)
        self.root_group.set_visible(False)
        self.page.add(self.root_group)

        # 选择模式说明 + 取消按钮。
        self.explanation_group = Adw.PreferencesGroup()
        self.explanation_label = Gtk.Label(label=_('Click on a document in the list below to set it as root. The root document will get built, no matter which document you are currently editing, and it will always display in the .pdf preview. The build log will also refer to the root document. This is often useful for working on large projects where typically a top level document (the root) will contain multiple lower level files via include statements.'))
        self.explanation_label.set_wrap(True)
        self.explanation_label.set_xalign(0)
        self.explanation_label.add_css_class('dim-label')
        self.explanation_label.add_css_class('caption')
        self.explanation_label.set_margin_start(12)
        self.explanation_label.set_margin_end(12)
        self.explanation_label.set_margin_top(10)
        self.explanation_label.set_margin_bottom(6)
        self.explanation_group.add(self.explanation_label)

        self.cancel_button = Gtk.Button(label=_('Cancel'))
        self.cancel_button.set_tooltip_text(_('Exit root document selection mode'))
        self.cancel_button.set_halign(Gtk.Align.CENTER)
        self.cancel_button.set_margin_top(10)
        self.cancel_button.set_margin_bottom(6)
        self.cancel_button.set_hexpand(True)
        self.cancel_button.set_margin_start(12)
        self.cancel_button.set_margin_end(12)
        self.explanation_group.add(self.cancel_button)
        self.explanation_group.set_visible(False)
        self.page.add(self.explanation_group)

        # 其他文档按钮。
        self.other_group = Adw.PreferencesGroup()
        self.other_documents_row = Adw.ActionRow()
        self.other_documents_row.set_activatable(True)
        self.other_documents_row.set_title(_('Other Documents') + '...')
        self.other_documents_row.set_icon_name('document-open-symbolic')
        self.other_documents_row.set_tooltip_text(_('Open a document not in the recent list'))
        self.other_group.add(self.other_documents_row)
        self.page.add(self.other_group)

        # 空状态。
        self.empty_group = Adw.PreferencesGroup()
        self.empty_label = Gtk.Label(label=_('No matching documents'))
        self.empty_label.add_css_class('dim-label')
        self.empty_label.set_margin_top(12)
        self.empty_group.add(self.empty_label)
        self.empty_group.set_visible(False)
        self.page.add(self.empty_group)

        self.dialog.add(self.page)

        self.open_rows = []
        self.recent_rows = []

    def update_open_items(self, documents, root_selection_mode=False, active_document=None, query=''):
        visible = [d for d in documents if (not root_selection_mode or d.is_latex_document())]
        visible.sort(key=lambda val: -val.get_last_activated())
        if query:
            visible = [d for d in visible if self._fuzzy_match(query, d)]

        for row in self.open_rows:
            self.open_group.remove(row)
        self.open_rows = []
        for document in visible:
            row = self._create_open_row(document, root_selection_mode, document is active_document, query)
            self.open_group.add(row)
            self.open_rows.append(row)

        self.open_group.set_title(_('Open Documents') if visible else '')
        self.open_group.set_visible(True)
        self._update_empty_state(query)

    def update_recent_items(self, recently_opened_documents, open_documents, query=''):
        open_filenames = set()
        for doc in open_documents:
            fn = doc.get_filename()
            if fn:
                open_filenames.add(fn)

        items = []
        for item in recently_opened_documents.values():
            fn = item['filename']
            if fn not in open_filenames and os.path.isfile(fn):
                items.append(item)
        items.sort(key=lambda val: -val['date'])

        if query:
            q = query.lower()
            items = [item for item in items
                     if q in os.path.basename(item['filename']).lower()
                     or q in os.path.dirname(item['filename']).lower()]

        for row in self.recent_rows:
            self.recent_group.remove(row)
        self.recent_rows = []
        for item in items:
            row = self._create_recent_row(item['filename'], query)
            self.recent_group.add(row)
            self.recent_rows.append(row)

        self.recent_group.set_title(_('Recent Documents') if items else '')
        self.recent_group.set_visible(len(self.recent_rows) > 0)
        self._update_empty_state(query)

    def _update_empty_state(self, query=''):
        total = len(self.open_rows) + len(self.recent_rows)
        self.empty_group.set_visible(bool(query) and total == 0)

    # ---- row builders ----

    def _create_open_row(self, document, root_selection_mode, is_active=False, query=''):
        row = Adw.ActionRow()
        row.set_activatable(True)
        row.document = document

        doc_type = document.get_document_type()
        icon_name = {'latex': 'document-latex-symbolic',
                     'bibtex': 'document-bibtex-symbolic'}.get(doc_type, 'document-other-symbolic')
        icon = Gtk.Image(icon_name=icon_name)
        row.add_prefix(icon)

        modified_suffix = '*' if document.source_buffer.get_modified() else ''
        displayname = document.get_displayname()
        basename = os.path.basename(displayname)
        row.set_use_markup(True)
        row.set_title(highlight_fuzzy(basename + modified_suffix, query))

        row.set_tooltip_text(document.get_filename() or displayname)
        directory = os.path.dirname(document.get_filename() or displayname)
        if directory:
            row.set_subtitle(highlight_fuzzy(directory, query))

        if is_active:
            row.add_css_class('accent')

        if document.get_is_root():
            root_icon = Gtk.Image(icon_name='starred-symbolic')
            root_icon.set_tooltip_text(_('Root document'))
            row.add_suffix(root_icon)

        close_button = Gtk.Button.new_from_icon_name('window-close-symbolic')
        close_button.set_has_frame(False)
        close_button.set_valign(Gtk.Align.CENTER)
        close_button.set_tooltip_text(_('Close document'))
        close_button.add_css_class('flat')
        close_button.row = row
        row.add_suffix(close_button)
        row.close_button = close_button

        if root_selection_mode and document.get_is_root():
            select_icon = Gtk.Image(icon_name='object-select-symbolic')
            row.add_suffix(select_icon)

        return row

    def _create_recent_row(self, filename, query=''):
        row = Adw.ActionRow()
        row.set_activatable(True)
        row.set_use_markup(True)
        row.filename = filename

        basename = os.path.basename(filename)
        directory = os.path.dirname(filename)
        row.set_title(highlight_fuzzy(basename, query))
        if directory:
            row.set_subtitle(highlight_fuzzy(directory, query))
        row.set_tooltip_text(filename)

        return row

    # ---- search helpers ----

    def _fuzzy_match(self, query, document):
        q = query.lower()
        if self._subseq(q, document.get_displayname().lower()):
            return True
        fn = document.get_filename()
        if fn and self._subseq(q, fn.lower()):
            return True
        return False

    @staticmethod
    def _subseq(query, text):
        i = 0
        for ch in query:
            i = text.find(ch, i)
            if i == -1:
                return False
            i += 1
        return True

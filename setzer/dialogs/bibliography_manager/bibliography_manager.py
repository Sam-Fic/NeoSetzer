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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''Integrated, source-preserving BibTeX entry manager for Setzer.'''

from __future__ import annotations

import builtins
import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, Gtk

from setzer.app.latex_db import LaTeXDB
from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.document.bibtex.entry_store import (
    BibTeXEntry,
    BibTeXEntryError,
    BibTeXEntryStore,
)
from setzer.document.bibtex.file_session import (
    BibTeXExternalChangeError,
    BibTeXFileSession,
)


def _(message: str) -> str:
    '''Look up a runtime gettext translation with a test-safe fallback.'''
    return getattr(builtins, '_', lambda value: value)(message)


FIELD_LABELS = {
    'author': _('Author'),
    'title': _('Title'),
    'year': _('Year'),
    'journal': _('Journal'),
    'booktitle': _('Book Title'),
    'publisher': _('Publisher'),
    'volume': _('Volume'),
    'number': _('Number'),
    'pages': _('Pages'),
    'doi': _('DOI'),
    'url': _('URL'),
    'editor': _('Editor'),
    'edition': _('Edition'),
    'address': _('Address'),
    'month': _('Month'),
    'note': _('Note'),
    'series': _('Series'),
    'institution': _('Institution'),
    'school': _('School'),
    'howpublished': _('How Published'),
    'keywords': _('Keywords'),
}


class BibliographyManagerDialog(DialogView):
    '''Browse, safely edit, and cite entries from project bibliography files.'''

    def __init__(self, main_window, workspace):
        DialogView.__init__(self, main_window)
        self.main_window = main_window
        self.workspace = workspace
        self.document = None
        self.sources = []
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''
        self.selected_entry = None
        self.editing_key = None
        self.entry_rows = []

        self.set_title(_('Manage Bibliography'))
        self.set_content_width(1040)
        self.set_content_height(680)
        self._build_view()
        self.connect('closed', self._on_closed)

    def _build_view(self):
        self.banner = Adw.Banner()
        self.banner.set_revealed(False)
        self.banner.set_button_label(_('Reload'))
        self.banner.connect('button-clicked', self._on_banner_reload)
        self.topbox.append(self.banner)

        header = Gtk.Box(spacing=8)
        header.set_margin_top(12)
        header.set_margin_bottom(8)
        header.set_margin_start(18)
        header.set_margin_end(18)
        self.topbox.append(header)

        self.source_model = Gtk.StringList.new([])
        self.source_selector = Gtk.DropDown.new(self.source_model, None)
        self.source_selector.set_hexpand(True)
        self.source_selector.connect('notify::selected', self._on_source_selected)
        header.append(self.source_selector)

        self.open_button = Gtk.Button(label=_('Open BibTeX File…'))
        self.open_button.set_icon_name('document-open-symbolic')
        self.open_button.set_tooltip_text(_('Open an existing BibTeX file'))
        self.open_button.connect('clicked', self._on_open_file)
        header.append(self.open_button)

        self.add_button = Gtk.Button(label=_('Add Entry'))
        self.add_button.set_icon_name('list-add-symbolic')
        self.add_button.set_tooltip_text(_('Create a new bibliography entry'))
        self.add_button.add_css_class('suggested-action')
        self.add_button.connect('clicked', self._on_add_entry)
        header.append(self.add_button)

        self.format_button = Gtk.Button(label=_('Format Bibliography'))
        self.format_button.set_icon_name('format-justify-fill-symbolic')
        self.format_button.set_tooltip_text(
            _('Rewrite all entries with sorted fields and aligned values'))
        self.format_button.connect('clicked', self._on_format_bibliography)
        header.append(self.format_button)

        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(370)
        self.paned.set_vexpand(True)
        self.topbox.append(self.paned)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_margin_start(18)
        left.set_margin_bottom(18)
        left.set_margin_end(8)
        self.paned.set_start_child(left)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_('Search citation key, title, author, or year'))
        self.search_entry.connect('search-changed', self._on_search_changed)
        left.append(self.search_entry)

        self.sort_keys = ('key', 'title', 'author', 'year')
        self.sort_model = Gtk.StringList.new([
            _('Sort by Citation Key'),
            _('Sort by Title'),
            _('Sort by Author'),
            _('Sort by Year'),
        ])
        self.sort_selector = Gtk.DropDown.new(self.sort_model, None)
        self.sort_selector.set_tooltip_text(_('Sort bibliography entries'))
        self.sort_selector.connect('notify::selected', self._on_sort_selected)
        left.append(self.sort_selector)

        self.entry_list = Gtk.ListBox()
        self.entry_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.entry_list.add_css_class('boxed-list')
        self.entry_list.connect('row-selected', self._on_entry_selected)
        entry_scroller = Gtk.ScrolledWindow()
        entry_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        entry_scroller.set_vexpand(True)
        entry_scroller.set_child(self.entry_list)
        left.append(entry_scroller)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        right.set_margin_end(18)
        right.set_margin_bottom(18)
        right.set_margin_start(8)
        self.paned.set_end_child(right)

        self.empty_page = Adw.StatusPage()
        self.empty_page.set_icon_name('library-symbolic')
        self.empty_page.set_title(_('Choose a BibTeX File'))
        self.empty_page.set_description(_('Choose a bibliography associated with the current document, or open a BibTeX file.'))
        self.empty_page.set_vexpand(True)
        right.append(self.empty_page)

        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.details_box.set_visible(False)
        right.append(self.details_box)

        self.details_title = Gtk.Label()
        self.details_title.add_css_class('title-2')
        self.details_title.set_halign(Gtk.Align.START)
        self.details_title.set_wrap(True)
        self.details_box.append(self.details_title)

        self.details_subtitle = Gtk.Label()
        self.details_subtitle.add_css_class('dim-label')
        self.details_subtitle.set_halign(Gtk.Align.START)
        self.details_subtitle.set_wrap(True)
        self.details_box.append(self.details_subtitle)

        self.details_view = Gtk.TextView()
        self.details_view.set_editable(False)
        self.details_view.set_cursor_visible(False)
        self.details_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.details_view.set_monospace(True)
        self.details_view.set_top_margin(8)
        self.details_view.set_bottom_margin(8)
        self.details_view.set_left_margin(8)
        self.details_view.set_right_margin(8)
        details_scroller = Gtk.ScrolledWindow()
        details_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        details_scroller.set_vexpand(True)
        details_scroller.set_child(self.details_view)
        details_scroller.add_css_class('preview-card')
        details_scroller.set_overflow(Gtk.Overflow.HIDDEN)
        self.details_box.append(details_scroller)

        actions = Gtk.Box(spacing=8)
        actions.set_halign(Gtk.Align.END)
        self.insert_button = Gtk.Button(label=_('Insert Citation'))
        self.insert_button.set_icon_name('insert-text-symbolic')
        self.insert_button.set_tooltip_text(_('Insert a \\cite command for this entry'))
        self.insert_button.connect('clicked', self._on_insert_citation)
        actions.append(self.insert_button)
        self.edit_button = Gtk.Button(label=_('Edit Entry'))
        self.edit_button.set_icon_name('document-edit-symbolic')
        self.edit_button.set_tooltip_text(_('Edit the selected bibliography entry'))
        self.edit_button.connect('clicked', self._on_edit_entry)
        actions.append(self.edit_button)
        self.delete_button = Gtk.Button(label=_('Delete Entry'))
        self.delete_button.set_icon_name('user-trash-symbolic')
        self.delete_button.set_tooltip_text(_('Remove the selected bibliography entry'))
        self.delete_button.add_css_class('destructive-action')
        self.delete_button.connect('clicked', self._on_delete_entry)
        actions.append(self.delete_button)
        self.details_box.append(actions)

        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.form_box.set_visible(False)
        right.append(self.form_box)
        self._build_form()

    def _build_form(self):
        title = Gtk.Label()
        title.set_markup(f'<b>{_('Entry Details')}</b>')
        title.set_halign(Gtk.Align.START)
        self.form_box.append(title)

        form_scroller = Gtk.ScrolledWindow()
        form_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroller.set_vexpand(True)
        self.form_box.append(form_scroller)
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        form_scroller.set_child(form)

        self.type_entry = Gtk.Entry()
        self.type_entry.set_placeholder_text(_('Entry type, for example article'))
        form.append(self._labeled_widget(_('Entry Type'), self.type_entry))
        self.key_entry = Gtk.Entry()
        self.key_entry.set_placeholder_text(_('Unique citation key'))
        form.append(self._labeled_widget(_('Citation Key'), self.key_entry))

        self.field_entries = {}
        for field in BibTeXEntryStore.common_fields():
            entry = Gtk.Entry()
            entry.set_placeholder_text(field)
            self.field_entries[field] = entry
            form.append(self._labeled_widget(FIELD_LABELS[field], entry))

        self.extra_fields = Gtk.TextView()
        self.extra_fields.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.extra_fields.set_monospace(True)
        self.extra_fields.set_vexpand(True)
        extra_scroller = Gtk.ScrolledWindow()
        extra_scroller.set_min_content_height(110)
        extra_scroller.set_child(self.extra_fields)
        form.append(self._labeled_widget(
            _('Additional Fields'), extra_scroller,
            _('One field per line: name = value'),
        ))

        buttons = Gtk.Box(spacing=8)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_('Cancel'))
        cancel.connect('clicked', self._on_cancel_edit)
        buttons.append(cancel)
        save = Gtk.Button(label=_('Save Entry'))
        save.add_css_class('suggested-action')
        save.connect('clicked', self._on_save_entry)
        buttons.append(save)
        self.form_box.append(buttons)

    @staticmethod
    def _labeled_widget(label, widget, description=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        heading = Gtk.Label(label=label)
        heading.set_halign(Gtk.Align.START)
        heading.add_css_class('heading')
        box.append(heading)
        if description:
            hint = Gtk.Label(label=description)
            hint.set_halign(Gtk.Align.START)
            hint.add_css_class('dim-label')
            hint.set_wrap(True)
            box.append(hint)
        box.append(widget)
        return box

    def run(self, document):
        '''Present the manager for the current LaTeX or BibTeX document.'''
        self.document = document
        self.search_entry.set_text('')
        self._populate_sources()
        Adw.Dialog.present(self, self.main_window)

    def _on_closed(self, dialog):
        self._reset_editing()
        self.document = None
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''

    def _populate_sources(self):
        self.sources = self._collect_sources()
        self.source_model.splice(0, self.source_model.get_n_items(), [])
        for source in self.sources:
            self.source_model.append(source['label'])
        self.source_selector.set_sensitive(bool(self.sources))
        self.add_button.set_sensitive(bool(self.sources))
        self.format_button.set_sensitive(bool(self.sources))
        if self.sources:
            self.source_selector.set_selected(0)
            self._load_source(self.sources[0])
        else:
            self._clear_source()

    def _collect_sources(self):
        result = []
        seen = set()

        def add_source(path, label=None, document=None):
            normalized = os.path.realpath(path) if path else None
            identity = normalized or f'untitled:{id(document)}'
            if identity in seen:
                return
            seen.add(identity)
            result.append({
                'path': normalized,
                'label': label or (os.path.basename(normalized) if normalized else _('Untitled BibTeX File')),
                'document': document,
            })

        active = self.document
        if active is not None and active.is_bibtex_document():
            add_source(active.get_filename(), document=active)
        if active is not None and active.is_latex_document() and active.get_filename():
            base_directory = active.get_dirname()
            for filename in sorted(active.parser.symbols.get('bibliographies', set())):
                path = os.path.realpath(os.path.join(base_directory, filename))
                if os.path.isfile(path):
                    add_source(path)
        for document in self.workspace.open_documents:
            if document.is_bibtex_document():
                add_source(document.get_filename(), document=document)
        return result

    def _on_source_selected(self, selector, parameter):
        selected = selector.get_selected()
        if selected >= len(self.sources):
            return
        self._load_source(self.sources[selected])

    def _load_source(self, source):
        try:
            self.selected_source = source
            target_document = self._document_for_path(source.get('path')) or source.get('document')
            source['document'] = target_document
            self.file_session = None
            if target_document is not None:
                text = self._document_text(target_document)
            else:
                self.file_session = BibTeXFileSession(source['path'])
                text = self.file_session.text
            self.loaded_text = text
            self.store = BibTeXEntryStore(text)
            self._show_message('', False)
            if self.store.diagnostics:
                self._show_message('\n'.join(self.store.diagnostics), True)
            self._refresh_entry_rows()
            self.add_button.set_sensitive(True)
            self.format_button.set_sensitive(True)
        except (OSError, UnicodeError, BibTeXEntryError) as error:
            self._clear_source()
            self._show_message(str(error), True)

    def _clear_source(self):
        self.selected_source = None
        self.file_session = None
        self.store = None
        self.loaded_text = ''
        self.selected_entry = None
        self._clear_entry_rows()
        self._set_detail_visibility(False)
        self.empty_page.set_visible(True)
        self.add_button.set_sensitive(False)
        self.format_button.set_sensitive(False)

    def _refresh_entry_rows(self):
        self._clear_entry_rows()
        if self.store is None:
            return
        sort_by = self.sort_keys[self.sort_selector.get_selected()]
        for entry in self.store.list_entries(self.search_entry.get_text(), sort_by=sort_by):
            row = Gtk.ListBoxRow()
            row.entry = entry
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            key = Gtk.Label(label=entry.key)
            key.set_halign(Gtk.Align.START)
            key.add_css_class('heading')
            box.append(key)
            summary = Gtk.Label(label=self._entry_summary(entry))
            summary.set_halign(Gtk.Align.START)
            summary.set_ellipsize(3)
            summary.add_css_class('dim-label')
            box.append(summary)
            row.set_child(box)
            self.entry_list.append(row)
            self.entry_rows.append(row)
        if self.entry_rows:
            self.entry_list.select_row(self.entry_rows[0])
        else:
            self.selected_entry = None
            self._set_detail_visibility(False)
            self.empty_page.set_visible(True)
            self.empty_page.set_title(_('No Bibliography Entries Found'))
            self.empty_page.set_description(_('Add an entry or adjust the search query.'))

    def _clear_entry_rows(self):
        child = self.entry_list.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.entry_list.remove(child)
            child = following
        self.entry_rows = []

    @staticmethod
    def _entry_summary(entry: BibTeXEntry):
        parts = [entry.entry_type]
        if entry.get('title'):
            parts.append(entry.get('title'))
        if entry.get('author'):
            parts.append(entry.get('author'))
        if entry.get('year'):
            parts.append(entry.get('year'))
        return ' · '.join(parts)

    def _on_search_changed(self, entry):
        self._refresh_entry_rows()

    def _on_sort_selected(self, selector, parameter):
        if self.store is not None:
            self._refresh_entry_rows()

    def _on_entry_selected(self, listbox, row):
        if row is None:
            return
        self.selected_entry = row.entry
        self._reset_editing()
        self.empty_page.set_visible(False)
        self._set_detail_visibility(True)
        entry = self.selected_entry
        self.details_title.set_text(entry.get('title') or entry.key)
        self.details_subtitle.set_text(f'{entry.key} · {entry.entry_type}')
        details = [f'@{entry.entry_type}{{{entry.key},']
        details.extend(f'  {name} = {{{value}}}' for name, value in entry.fields)
        details.append('}')
        self.details_view.get_buffer().set_text('\n'.join(details))
        self.insert_button.set_sensitive(self.document is not None and self.document.is_latex_document())

    def _set_detail_visibility(self, visible):
        self.details_box.set_visible(visible)
        self.form_box.set_visible(False)

    def _on_add_entry(self, button):
        if self.store is None:
            return
        self.selected_entry = None
        self.editing_key = None
        self._set_detail_visibility(False)
        self.empty_page.set_visible(False)
        self.form_box.set_visible(True)
        self.type_entry.set_text('article')
        self.key_entry.set_text('')
        for field in self.field_entries.values():
            field.set_text('')
        self.extra_fields.get_buffer().set_text('')
        self.key_entry.grab_focus()

    def _on_edit_entry(self, button):
        if self.selected_entry is None:
            return
        entry = self.selected_entry
        self.editing_key = entry.key
        self._set_detail_visibility(False)
        self.form_box.set_visible(True)
        self.type_entry.set_text(entry.entry_type)
        self.key_entry.set_text(entry.key)
        fields = entry.field_map
        for name, field in self.field_entries.items():
            field.set_text(fields.pop(name, ''))
        extra = '\n'.join(f'{name} = {value}' for name, value in fields.items())
        self.extra_fields.get_buffer().set_text(extra)
        self.key_entry.grab_focus()

    def _on_cancel_edit(self, button):
        self._reset_editing()
        if self.selected_entry is not None:
            self._set_detail_visibility(True)
        else:
            self.empty_page.set_visible(True)

    def _reset_editing(self):
        self.editing_key = None
        self.form_box.set_visible(False)

    def _on_save_entry(self, button):
        if self.store is None:
            return
        try:
            fields = self._collect_form_fields()
            if self.editing_key is None:
                updated_text = self.store.add_entry(
                    self.type_entry.get_text(), self.key_entry.get_text(), fields)
            else:
                updated_text = self.store.update_entry(
                    self.editing_key, self.type_entry.get_text(), self.key_entry.get_text(), fields)
            self._apply_text(updated_text)
            key = self.key_entry.get_text().strip()
            self._load_source(self.selected_source)
            for row in self.entry_rows:
                if row.entry.key == key:
                    self.entry_list.select_row(row)
                    break
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _collect_form_fields(self):
        fields = {name: entry.get_text() for name, entry in self.field_entries.items()}
        start, end = self.extra_fields.get_buffer().get_bounds()
        extra = self.extra_fields.get_buffer().get_text(start, end, True)
        for line in extra.splitlines():
            if not line.strip():
                continue
            if '=' not in line:
                raise BibTeXEntryError(_('Additional fields must use “name = value”'))
            name, value = line.split('=', 1)
            name = name.strip().lower()
            if name in fields and fields[name].strip():
                raise BibTeXEntryError(_('The field “{field}” occurs more than once').format(field=name))
            fields[name] = value.strip()
        return fields

    def _on_delete_entry(self, button):
        if self.selected_entry is None:
            return
        key = self.selected_entry.key
        confirmation = Adw.AlertDialog(
            heading=_('Delete BibTeX Entry?'),
            body=_('Delete “{key}” from this bibliography? This can be undone when the file is open in Setzer.').format(key=key),
        )
        confirmation.add_response('cancel', _('Cancel'))
        confirmation.add_response('delete', _('Delete'))
        confirmation.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        confirmation.set_default_response('cancel')
        confirmation.set_close_response('cancel')
        confirmation.connect('response', self._on_delete_response, key)
        confirmation.present(self)

    def _on_delete_response(self, dialog, response, key):
        if response != 'delete' or self.store is None:
            return
        try:
            self._apply_text(self.store.delete_entry(key))
            self._load_source(self.selected_source)
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _on_format_bibliography(self, button):
        if self.store is None:
            return
        confirmation = Adw.AlertDialog(
            heading=_('Format Bibliography?'),
            body=_('Rewrite every entry with fields in a consistent order and aligned values. Comments and everything outside entries stay unchanged. This can be undone when the file is open in Setzer.'),
        )
        confirmation.add_response('cancel', _('Cancel'))
        confirmation.add_response('format', _('Format'))
        confirmation.set_response_appearance('format', Adw.ResponseAppearance.SUGGESTED)
        confirmation.set_default_response('cancel')
        confirmation.set_close_response('cancel')
        confirmation.connect('response', self._on_format_response)
        confirmation.present(self)

    def _on_format_response(self, dialog, response):
        if response != 'format' or self.store is None:
            return
        try:
            formatted = self.store.format_bibliography()
            if formatted == self.loaded_text:
                self._show_message(_('The bibliography already uses the canonical entry style'), False)
                return
            selected_key = self.selected_entry.key if self.selected_entry is not None else None
            self._apply_text(formatted)
            self._load_source(self.selected_source)
            if selected_key is not None:
                for row in self.entry_rows:
                    if row.entry.key == selected_key:
                        self.entry_list.select_row(row)
                        break
        except (BibTeXEntryError, BibTeXExternalChangeError, OSError, UnicodeError) as error:
            self._show_message(str(error), True)

    def _on_insert_citation(self, button):
        if self.selected_entry is None or self.document is None or not self.document.is_latex_document():
            return
        buffer = self.document.source_buffer
        buffer.begin_user_action()
        try:
            buffer.insert_at_cursor('\\cite{' + self.selected_entry.key + '}')
        finally:
            buffer.end_user_action()
        self.document.scroll_cursor_onscreen()
        LaTeXDB.schedule_parse_included_files()
        self._show_message(_('Citation inserted'), False)

    def _apply_text(self, text):
        target_document = self.selected_source.get('document') if self.selected_source else None
        if target_document is not None:
            current_text = self._document_text(target_document)
            if current_text != self.loaded_text:
                raise BibTeXExternalChangeError(_('The open BibTeX document changed. Reload before saving.'))
            buffer = target_document.source_buffer
            buffer.begin_user_action()
            try:
                buffer.set_text(text)
            finally:
                buffer.end_user_action()
            parser = getattr(target_document, 'parser', None)
            if parser is not None and hasattr(parser, 'parse_symbols'):
                parser.parse_symbols(text)
        elif self.file_session is not None:
            self.file_session.write_text(text)
        else:
            raise BibTeXEntryError(_('No BibTeX file is selected'))
        LaTeXDB.schedule_parse_included_files()

    @staticmethod
    def _document_text(document):
        start, end = document.source_buffer.get_bounds()
        return document.source_buffer.get_text(start, end, True)

    def _document_for_path(self, pathname):
        if not pathname:
            return None
        normalized = os.path.realpath(pathname)
        for document in self.workspace.open_documents:
            if document.is_bibtex_document() and document.get_filename() and \
                    os.path.realpath(document.get_filename()) == normalized:
                return document
        return None

    def _on_open_file(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Open BibTeX File'))
        filter = Gtk.FileFilter()
        filter.set_name(_('BibTeX Files'))
        filter.add_suffix('bib')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter)
        dialog.set_filters(filters)
        dialog.open(self.main_window, None, self._on_open_file_finished)

    def _on_open_file_finished(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            path = file.get_path()
            if not path:
                return
            self._add_external_source(path)
        except Exception as error:
            # Gtk.DialogError.CANCELLED is normal and should remain silent.
            if 'cancel' not in str(error).lower():
                self._show_message(str(error), True)

    def _add_external_source(self, path):
        normalized = os.path.realpath(path)
        for index, source in enumerate(self.sources):
            if source.get('path') == normalized:
                self.source_selector.set_selected(index)
                return
        self.sources.append({
            'path': normalized,
            'label': os.path.basename(normalized),
            'document': self._document_for_path(normalized),
        })
        self.source_model.append(os.path.basename(normalized))
        self.source_selector.set_selected(len(self.sources) - 1)

    def _on_banner_reload(self, banner):
        if self.selected_source is not None:
            self._load_source(self.selected_source)

    def _show_message(self, message, warning):
        self.banner.set_title(message)
        self.banner.set_button_label(_('Reload') if warning else '')
        self.banner.set_revealed(bool(message))

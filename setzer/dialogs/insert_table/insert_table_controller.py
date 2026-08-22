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

import builtins

import gi
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk

from setzer.dialogs.insert_table.insert_table_viewgtk import InsertTableView
from setzer.dialogs.insert_table.table_generator import (
    TableImportError,
    TableSpec,
    parse_table_text,
    resize_cells,
    resize_merges,
)


def _(message):
    return getattr(builtins, '_', lambda value: value)(message)


class InsertTableController:
    '''Coordinate the table generator view and the active LaTeX document.'''

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = InsertTableView(main_window)
        self.document = None
        self.cell_merges = ()
        self._resizing = False
        self._file_chooser = None
        self._connect_static_signals()
        self._connect_grid_signals()

    def _connect_static_signals(self):
        self.view.cancel_button.connect('clicked', self._on_cancel)
        self.view.copy_button.connect('clicked', self._on_copy)
        self.view.insert_button.connect('clicked', self._on_insert)
        self.view.paste_data_button.connect('clicked', self._on_paste_data)
        self.view.import_file_button.connect('clicked', self._on_import_file)
        self.view.rows_row.connect('notify::value', self._on_size_changed)
        self.view.columns_row.connect('notify::value', self._on_size_changed)
        self.view.add_merge_button.connect('clicked', self._on_add_merge)
        self.view.style_row.connect('notify::selected', self._on_options_changed)
        self.view.header_switch.connect('notify::active', self._on_header_changed)
        self.view.repeat_header_switch.connect('notify::active', self._on_repeat_header_changed)
        self.view.environment_row.connect('notify::selected', self._on_environment_changed)
        self.view.table_switch.connect('notify::active', self._on_table_switch_changed)
        self.view.placement_row.connect('notify::selected', self._on_options_changed)
        self.view.center_switch.connect('notify::active', self._on_options_changed)
        self.view.caption_row.connect('changed', self._on_options_changed)
        self.view.label_row.connect('changed', self._on_options_changed)

    def _connect_grid_signals(self):
        for entries in self.view.cell_entries:
            for entry in entries:
                entry.connect('changed', self._on_options_changed)
        for row in self.view.alignment_rows:
            row.connect('notify::selected', self._on_options_changed)

    def open(self, document):
        '''Reset the transient form and present it for an active LaTeX document.'''

        self.document = document
        self._resizing = True
        self.cell_merges = ()
        self.view.reset()
        self._resizing = False
        self._connect_grid_signals()
        self._sync_merge_view()
        self.view.set_environment_sensitive()
        self._refresh_preview()
        self.view.present(self.main_window)
        if self.view.cell_entries:
            self.view.cell_entries[0][0].grab_focus()

    def _on_cancel(self, button):
        self.view.close()
        self._restore_editor_focus()

    def _on_paste_data(self, button):
        display = Gdk.Display.get_default()
        if display is None:
            self.view.set_import_status(_('The clipboard is unavailable.'), is_error=True)
            return
        display.get_clipboard().read_text_async(None, self._on_clipboard_text_ready)

    def _on_clipboard_text_ready(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            self.view.set_import_status(_('Could not read text from the clipboard.'), is_error=True)
            return
        if text is None:
            self.view.set_import_status(_('The clipboard does not contain text table data.'), is_error=True)
            return
        self._apply_import_text(text)

    def _on_import_file(self, button):
        chooser = Gtk.FileChooserNative.new(
            _('Import CSV/TSV File'),
            self.main_window,
            Gtk.FileChooserAction.OPEN,
            _('_Import'),
            _('_Cancel'),
        )
        filter = Gtk.FileFilter()
        filter.set_name(_('CSV and TSV files'))
        filter.add_pattern('*.csv')
        filter.add_pattern('*.tsv')
        filter.add_pattern('*.txt')
        chooser.add_filter(filter)
        chooser.connect('response', self._on_import_file_response)
        self._file_chooser = chooser
        chooser.show()

    def _on_import_file_response(self, chooser, response):
        self._file_chooser = None
        if response != Gtk.ResponseType.ACCEPT:
            chooser.destroy()
            return
        file = chooser.get_file()
        chooser.destroy()
        if file is None:
            self.view.set_import_status(_('No file was selected for table import.'), is_error=True)
            return
        file.load_contents_async(None, self._on_import_file_contents_ready)

    def _on_import_file_contents_ready(self, file, result):
        try:
            success, contents, etag = file.load_contents_finish(result)
        except GLib.Error:
            self.view.set_import_status(_('Could not read the selected table file.'), is_error=True)
            return
        if not success:
            self.view.set_import_status(_('Could not read the selected table file.'), is_error=True)
            return
        try:
            text = contents.decode('utf-8-sig')
        except UnicodeDecodeError:
            self.view.set_import_status(_('The selected table file must be UTF-8 encoded.'), is_error=True)
            return
        self._apply_import_text(text)

    def _apply_import_text(self, text):
        '''Replace the editable grid only after a complete parse has succeeded.'''

        try:
            imported = parse_table_text(text, self.view.get_import_format())
        except TableImportError as error:
            self.view.set_import_status(self._import_error_message(error), is_error=True)
            return False

        removed_merges = len(self.cell_merges)
        self._resizing = True
        self.view.rows_row.set_value(imported.rows)
        self.view.columns_row.set_value(imported.columns)
        self.view.set_cells(imported.cells, imported.rows, imported.columns)
        self.view.set_merge_limits(imported.rows, imported.columns)
        self.cell_merges = ()
        self._resizing = False
        self._connect_grid_signals()
        self._sync_merge_view()
        self.view.set_environment_sensitive()
        self._refresh_preview()
        message = _('Imported {rows} rows and {columns} columns.').format(
            rows=imported.rows, columns=imported.columns)
        if removed_merges:
            message += ' ' + _('Cleared {count} merged ranges because imported data replaces the grid.').format(
                count=removed_merges)
        self.view.set_import_status(message)
        return True

    def _import_error_message(self, error):
        message = str(error)
        if message.startswith('Imported table dimensions'):
            return _('Imported data exceeds the maximum of 30 rows and 12 columns.')
        if message == 'No table data was found':
            return _('No table data was found in the imported text.')
        return _('The imported TSV/CSV data is invalid.')

    def _on_size_changed(self, row, pspec):
        if self._resizing:
            return
        rows = int(self.view.rows_row.get_value())
        columns = int(self.view.columns_row.get_value())
        cells = resize_cells(self.view.get_cells(), rows, columns)
        current_alignments = self.view.get_alignments()
        alignments = tuple(
            current_alignments[column_index] if column_index < len(current_alignments)
            else ('l' if column_index == 0 else 'c')
            for column_index in range(columns)
        )
        self.cell_merges = resize_merges(self.cell_merges, rows, columns)
        self._resizing = True
        self.view.set_cells(cells, rows, columns, alignments)
        self.view.set_merge_limits(rows, columns)
        self._resizing = False
        self._connect_grid_signals()
        self._sync_merge_view()
        self.view.set_environment_sensitive()
        self._ensure_merge_compatibility()
        self._refresh_preview()

    def _on_add_merge(self, button):
        try:
            candidate = self.view.get_merge_draft()
            spec = self._get_spec(self.cell_merges + (candidate,))
        except (TypeError, ValueError):
            self.view.set_merge_error(_(
                'The selected range must fit inside the table, span at least two cells, and not overlap another merged range.'))
            return
        self.cell_merges = spec.cell_merges
        self.view.set_merge_error()
        self._sync_merge_view()
        self._refresh_preview()

    def _on_remove_merge(self, merge):
        self.cell_merges = tuple(item for item in self.cell_merges if item != merge)
        self.view.set_merge_error()
        self._sync_merge_view()
        self._refresh_preview()

    def _sync_merge_view(self):
        self.view.set_cell_merges(self.cell_merges, self._on_remove_merge)
        self.view.set_merge_coverage(self.cell_merges)

    def _on_header_changed(self, row, pspec):
        self.view.set_environment_sensitive()
        self._ensure_merge_compatibility()
        self._refresh_preview()

    def _on_repeat_header_changed(self, row, pspec):
        self._ensure_merge_compatibility()
        self._refresh_preview()

    def _on_environment_changed(self, row, pspec):
        self.view.set_environment_sensitive()
        self._ensure_merge_compatibility()
        self._refresh_preview()

    def _on_table_switch_changed(self, row, pspec):
        self.view.set_wrapper_sensitive()
        self._refresh_preview()

    def _on_options_changed(self, *args):
        if not self._resizing:
            self._refresh_preview()

    def _ensure_merge_compatibility(self):
        incompatible = (
            self.view.get_environment() == 'longtable'
            and self.view.header_switch.get_active()
            and self.view.repeat_header_switch.get_active()
            and any(merge.row == 0 and merge.row_span > 1 for merge in self.cell_merges)
        )
        if incompatible:
            self.view.repeat_header_switch.set_active(False)
            self.view.set_merge_error(_(
                'Header repetition was disabled because a first-row merge spans multiple rows.'))
            return False
        return True

    def _get_spec(self, cell_merges=None):
        return TableSpec(
            rows=int(self.view.rows_row.get_value()),
            columns=int(self.view.columns_row.get_value()),
            cells=self.view.get_cells(),
            alignments=self.view.get_alignments(),
            cell_merges=self.cell_merges if cell_merges is None else cell_merges,
            style=self.view.get_style(),
            environment=self.view.get_environment(),
            header_row=self.view.header_switch.get_active(),
            repeat_header=self.view.repeat_header_switch.get_active(),
            use_table_environment=self.view.table_switch.get_active(),
            placement=self.view.get_placement(),
            centered=self.view.center_switch.get_active(),
            caption=self.view.caption_row.get_text(),
            label=self.view.label_row.get_text(),
        )

    def _refresh_preview(self):
        try:
            self.view.set_preview(self._get_spec().render())
        except ValueError as error:
            self.view.set_merge_error(str(error))

    def _on_copy(self, button):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(self._get_spec().render())

    def _on_insert(self, button):
        if self.document is None:
            return
        spec = self._get_spec()
        if spec.required_packages:
            self.document.add_packages(spec.required_packages)
        self.document.insert_symbol_at_cursor(spec.render())
        self.view.close()
        self._restore_editor_focus()

    def _restore_editor_focus(self):
        if self.document is not None:
            self.document.view.source_view.grab_focus()

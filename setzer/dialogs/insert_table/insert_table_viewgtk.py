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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.dialogs.insert_table.table_generator import (
    ALIGNMENTS,
    CellMerge,
    ENVIRONMENT_LONGTABLE,
    ENVIRONMENT_TABULAR,
    IMPORT_FORMAT_AUTO,
    IMPORT_FORMAT_CSV_COMMA,
    IMPORT_FORMAT_CSV_SEMICOLON,
    IMPORT_FORMAT_TSV,
    MAX_CELL_MERGES,
    MAX_COLUMNS,
    MAX_ROWS,
    PLACEMENTS,
    STYLE_BOOKTABS,
    STYLE_PLAIN,
)


class InsertTableView(DialogView):
    '''GTK4/libadwaita view for configuring and previewing a LaTeX table.'''

    STYLE_OPTIONS = (
        (STYLE_PLAIN, 'Plain rules'),
        (STYLE_BOOKTABS, 'Booktabs'),
    )
    ALIGNMENT_OPTIONS = (
        ('l', 'Left'),
        ('c', 'Center'),
        ('r', 'Right'),
    )
    ENVIRONMENT_OPTIONS = (
        (ENVIRONMENT_TABULAR, 'Standard table'),
        (ENVIRONMENT_LONGTABLE, 'Long table (multiple pages)'),
    )
    IMPORT_OPTIONS = (
        (IMPORT_FORMAT_AUTO, 'Auto (TSV when tabs are present)'),
        (IMPORT_FORMAT_TSV, 'TSV (tab-separated)'),
        (IMPORT_FORMAT_CSV_COMMA, 'CSV (comma-separated)'),
        (IMPORT_FORMAT_CSV_SEMICOLON, 'CSV (semicolon-separated)'),
    )
    PLACEMENT_OPTIONS = (
        ('htbp', 'htbp (here, top, bottom, page) — default'),
        ('ht', 'ht (here, top)'),
        ('h', 'h (here only)'),
        ('t', 't (top)'),
        ('b', 'b (bottom)'),
        ('p', 'p (separate float page)'),
        ('H', 'H (forced in place; needs float package)'),
        ('h!', 'h! (here, override restrictions)'),
    )

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_content_width(760)
        self.set_content_height(760)
        self.cell_entries = []
        self.alignment_rows = []
        self.merge_rows = []

        self.headerbar.set_title_widget(Gtk.Label(label=_('Insert Table')))
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        self.cancel_button.set_tooltip_text(_('Close the dialog without inserting anything'))
        self.headerbar.pack_start(self.cancel_button)
        self.insert_button = Gtk.Button.new_with_mnemonic(_('_Insert'))
        self.insert_button.add_css_class('suggested-action')
        self.insert_button.set_tooltip_text(_('Generate the LaTeX code and insert it at the cursor'))
        self.headerbar.pack_end(self.insert_button)
        self.copy_button = Gtk.Button.new_with_mnemonic(_('_Copy LaTeX'))
        self.copy_button.set_tooltip_text(_('Copy the generated LaTeX without changing the document'))
        self.headerbar.pack_end(self.copy_button)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        scrolled.set_child(content)
        self.topbox.append(scrolled)

        size_group = Adw.PreferencesGroup()
        size_group.set_title(_('Table Size'))
        self.rows_row = Adw.SpinRow()
        self.rows_row.set_title(_('Rows'))
        self.rows_row.set_subtitle(_('Number of table rows'))
        self.rows_row.set_adjustment(Gtk.Adjustment(
            value=3, lower=1, upper=MAX_ROWS, step_increment=1, page_increment=5))
        size_group.add(self.rows_row)
        self.columns_row = Adw.SpinRow()
        self.columns_row.set_title(_('Columns'))
        self.columns_row.set_subtitle(_('Number of table columns'))
        self.columns_row.set_adjustment(Gtk.Adjustment(
            value=3, lower=1, upper=MAX_COLUMNS, step_increment=1, page_increment=1))
        size_group.add(self.columns_row)
        content.append(size_group)

        data_group = Adw.PreferencesGroup()
        data_group.set_title(_('Table Data'))
        data_group.set_description(_('Enter cell content as LaTeX. Use Tab and Shift+Tab to move between cells.'))
        self.grid_scrolled = Gtk.ScrolledWindow()
        self.grid_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.grid_scrolled.set_min_content_height(180)
        self.grid_scrolled.set_max_content_height(320)
        self.cell_grid = Gtk.Grid()
        self.cell_grid.set_column_spacing(6)
        self.cell_grid.set_row_spacing(6)
        self.cell_grid.set_margin_top(8)
        self.cell_grid.set_margin_bottom(8)
        self.cell_grid.set_margin_start(8)
        self.cell_grid.set_margin_end(8)
        self.grid_scrolled.set_child(self.cell_grid)
        data_group.add(self.grid_scrolled)
        self.import_format_row = Adw.ComboRow()
        self.import_format_row.set_title(_('Import format'))
        self.import_format_row.set_subtitle(_('Auto uses TSV when the text contains tabs; otherwise it uses comma CSV'))
        self.import_format_row.set_model(Gtk.StringList.new([
            _('Auto (TSV when tabs are present)'),
            _('TSV (tab-separated)'),
            _('CSV (comma-separated)'),
            _('CSV (semicolon-separated)'),
        ]))
        data_group.add(self.import_format_row)
        import_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.paste_data_button = Gtk.Button.new_with_mnemonic(_('_Paste TSV/CSV'))
        self.paste_data_button.set_tooltip_text(_('Replace the grid with text from the clipboard'))
        import_actions.append(self.paste_data_button)
        self.import_file_button = Gtk.Button.new_with_mnemonic(_('_Import CSV/TSV File'))
        self.import_file_button.set_tooltip_text(_('Replace the grid with UTF-8 CSV or TSV data from a file'))
        import_actions.append(self.import_file_button)
        data_group.add(import_actions)
        self.import_status = Gtk.Label()
        self.import_status.set_halign(Gtk.Align.START)
        self.import_status.set_wrap(True)
        self.import_status.add_css_class('dim-label')
        data_group.add(self.import_status)
        content.append(data_group)

        self.columns_group = Adw.PreferencesGroup()
        self.columns_group.set_title(_('Column Alignment'))
        content.append(self.columns_group)

        self.merges_group = Adw.PreferencesGroup()
        self.merges_group.set_title(_('Merge Cells'))
        self.merges_group.set_description(_('Merge a rectangular range. The top-left cell supplies the generated LaTeX content.'))
        self.merge_row_row = Adw.SpinRow()
        self.merge_row_row.set_title(_('Start row'))
        self.merge_row_row.set_adjustment(Gtk.Adjustment(value=1, lower=1, upper=MAX_ROWS, step_increment=1, page_increment=1))
        self.merges_group.add(self.merge_row_row)
        self.merge_column_row = Adw.SpinRow()
        self.merge_column_row.set_title(_('Start column'))
        self.merge_column_row.set_adjustment(Gtk.Adjustment(value=1, lower=1, upper=MAX_COLUMNS, step_increment=1, page_increment=1))
        self.merges_group.add(self.merge_column_row)
        self.merge_row_span_row = Adw.SpinRow()
        self.merge_row_span_row.set_title(_('Rows to merge'))
        self.merge_row_span_row.set_adjustment(Gtk.Adjustment(value=1, lower=1, upper=MAX_ROWS, step_increment=1, page_increment=1))
        self.merges_group.add(self.merge_row_span_row)
        self.merge_column_span_row = Adw.SpinRow()
        self.merge_column_span_row.set_title(_('Columns to merge'))
        self.merge_column_span_row.set_adjustment(Gtk.Adjustment(value=2, lower=1, upper=MAX_COLUMNS, step_increment=1, page_increment=1))
        self.merges_group.add(self.merge_column_span_row)
        self.add_merge_button = Gtk.Button.new_with_mnemonic(_('_Add Merge'))
        self.add_merge_button.set_halign(Gtk.Align.START)
        self.add_merge_button.set_margin_top(6)
        self.add_merge_button.set_tooltip_text(_('Add the selected cell range to the table'))
        self.merges_group.add(self.add_merge_button)
        self.edit_merge_button = Gtk.Button.new_with_mnemonic(_('_Update Merge'))
        self.edit_merge_button.set_halign(Gtk.Align.START)
        self.edit_merge_button.set_margin_top(6)
        self.edit_merge_button.set_tooltip_text(_('Replace the selected merged range with the values above'))
        self.merges_group.add(self.edit_merge_button)
        self.cancel_merge_edit_button = Gtk.Button.new_with_mnemonic(_('_Cancel Merge Edit'))
        self.cancel_merge_edit_button.set_halign(Gtk.Align.START)
        self.cancel_merge_edit_button.set_margin_top(6)
        self.cancel_merge_edit_button.set_tooltip_text(_('Stop editing the selected merged range'))
        self.merges_group.add(self.cancel_merge_edit_button)
        self.merge_status = Gtk.Label()
        self.merge_status.set_halign(Gtk.Align.START)
        self.merge_status.set_wrap(True)
        self.merge_status.add_css_class('error')
        self.merges_group.add(self.merge_status)
        self.merge_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.merges_group.add(self.merge_list)
        content.append(self.merges_group)

        appearance_group = Adw.PreferencesGroup()
        appearance_group.set_title(_('Appearance'))
        self.style_row = Adw.ComboRow()
        self.style_row.set_title(_('Rule style'))
        self.style_row.set_subtitle(_('Plain rules use \\hline; Booktabs uses professional horizontal rules'))
        self.style_row.set_model(Gtk.StringList.new([
            _('Plain rules'),
            _('Booktabs'),
        ]))
        appearance_group.add(self.style_row)
        self.header_switch = Adw.SwitchRow()
        self.header_switch.set_title(_('Use first row as header'))
        self.header_switch.set_subtitle(_('Add a separating rule below the first row with Booktabs'))
        self.header_switch.set_active(True)
        appearance_group.add(self.header_switch)
        self.repeat_header_switch = Adw.SwitchRow()
        self.repeat_header_switch.set_title(_('Repeat header on following pages'))
        self.repeat_header_switch.set_subtitle(_('Repeat the first row when a long table continues on a new page'))
        self.repeat_header_switch.set_active(True)
        appearance_group.add(self.repeat_header_switch)
        content.append(appearance_group)

        wrapper_group = Adw.PreferencesGroup()
        wrapper_group.set_title(_('Table Environment'))
        self.environment_row = Adw.ComboRow()
        self.environment_row.set_title(_('Output environment'))
        self.environment_row.set_subtitle(_('Long tables may continue across pages and are not floating tables'))
        self.environment_row.set_model(Gtk.StringList.new([
            _('Standard table'),
            _('Long table (multiple pages)'),
        ]))
        wrapper_group.add(self.environment_row)
        self.longtable_note = Adw.ActionRow()
        self.longtable_note.set_title(_('Long tables cannot use a table float'))
        self.longtable_note.set_subtitle(_('Caption and label are placed inside longtable; float placement and centering are unavailable.'))
        self.longtable_note.add_css_class('property')
        wrapper_group.add(self.longtable_note)
        self.table_switch = Adw.SwitchRow()
        self.table_switch.set_title(_('Use table environment'))
        self.table_switch.set_subtitle(_('Wrap tabular in a floating table with caption and label support'))
        self.table_switch.set_active(True)
        wrapper_group.add(self.table_switch)
        self.placement_row = Adw.ComboRow()
        self.placement_row.set_title(_('Float placement'))
        self.placement_row.set_model(Gtk.StringList.new([
            _('htbp (here, top, bottom, page) — default'),
            _('ht (here, top)'),
            _('h (here only)'),
            _('t (top)'),
            _('b (bottom)'),
            _('p (separate float page)'),
            _('H (forced in place; needs float package)'),
            _('h! (here, override restrictions)'),
        ]))
        wrapper_group.add(self.placement_row)
        self.center_switch = Adw.SwitchRow()
        self.center_switch.set_title(_('Center the table'))
        self.center_switch.set_active(True)
        wrapper_group.add(self.center_switch)
        content.append(wrapper_group)

        text_group = Adw.PreferencesGroup()
        text_group.set_title(_('Caption & Label'))
        self.caption_row = Adw.EntryRow()
        self.caption_row.set_title(_('Caption'))
        self.caption_row.set_tooltip_text(_('Text displayed above the table; leave empty to omit it'))
        text_group.add(self.caption_row)
        self.label_row = Adw.EntryRow()
        self.label_row.set_title(_('Label (for \\ref)'))
        self.label_row.set_tooltip_text(_('Identifier used with \\ref to cross-reference this table'))
        self.label_row.set_text('tab:')
        text_group.add(self.label_row)
        content.append(text_group)

        preview_group = Adw.PreferencesGroup()
        preview_group.set_title(_('LaTeX Preview'))
        self.preview = Gtk.TextView()
        self.preview.set_editable(False)
        self.preview.set_cursor_visible(False)
        self.preview.set_monospace(True)
        self.preview.set_wrap_mode(Gtk.WrapMode.NONE)
        self.preview.set_top_margin(8)
        self.preview.set_bottom_margin(8)
        self.preview.set_left_margin(8)
        self.preview.set_right_margin(8)
        preview_scrolled = Gtk.ScrolledWindow()
        preview_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scrolled.set_min_content_height(160)
        preview_scrolled.set_max_content_height(260)
        preview_scrolled.set_child(self.preview)
        preview_scrolled.add_css_class('preview-card')
        preview_scrolled.set_overflow(Gtk.Overflow.HIDDEN)
        preview_group.add(preview_scrolled)
        content.append(preview_group)

        self.reset()

    def reset(self):
        self.rows_row.set_value(3)
        self.columns_row.set_value(3)
        self.style_row.set_selected(0)
        self.header_switch.set_active(True)
        self.repeat_header_switch.set_active(True)
        self.environment_row.set_selected(0)
        self.table_switch.set_active(True)
        self.placement_row.set_selected(0)
        self.center_switch.set_active(True)
        self.caption_row.set_text('')
        self.label_row.set_text('tab:')
        self.import_format_row.set_selected(0)
        self.set_import_status()
        self.set_cells((), 3, 3)
        self.set_merge_limits(3, 3)
        self.set_cell_merges(())
        self.set_merge_editor()
        self.set_environment_sensitive()
        self.set_preview('')

    def set_cells(self, cells, rows, columns, alignments=()):
        '''Rebuild the grid, preserving values supplied by the controller.'''

        child = self.cell_grid.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.cell_grid.remove(child)
            child = next_child
        self.cell_entries = []

        for column_index in range(columns):
            label = Gtk.Label(label=str(column_index + 1))
            label.add_css_class('dim-label')
            label.set_halign(Gtk.Align.CENTER)
            self.cell_grid.attach(label, column_index + 1, 0, 1, 1)

        for row_index in range(rows):
            row_label = Gtk.Label(label=str(row_index + 1))
            row_label.add_css_class('dim-label')
            row_label.set_halign(Gtk.Align.CENTER)
            self.cell_grid.attach(row_label, 0, row_index + 1, 1, 1)
            entries = []
            for column_index in range(columns):
                entry = Gtk.Entry()
                entry.set_hexpand(True)
                entry.set_width_chars(12)
                value = (cells[row_index][column_index]
                         if row_index < len(cells) and column_index < len(cells[row_index])
                         else '')
                entry.set_text(value)
                entry.set_tooltip_text(_('Row {row}, column {column}').format(
                    row=row_index + 1, column=column_index + 1))
                self.cell_grid.attach(entry, column_index + 1, row_index + 1, 1, 1)
                entries.append(entry)
            self.cell_entries.append(entries)

        self.set_alignments(alignments, columns)

    def set_alignments(self, alignments, columns):
        for row in self.alignment_rows:
            self.columns_group.remove(row)
        self.alignment_rows = []
        model = Gtk.StringList.new([
            _('Left'),
            _('Center'),
            _('Right'),
        ])
        for column_index in range(columns):
            row = Adw.ComboRow()
            row.set_title(_('Column {column}').format(column=column_index + 1))
            row.set_model(model)
            value = alignments[column_index] if column_index < len(alignments) else (
                'l' if column_index == 0 else 'c')
            row.set_selected(ALIGNMENTS.index(value))
            self.columns_group.add(row)
            self.alignment_rows.append(row)

    def set_merge_limits(self, rows, columns):
        self.merge_row_row.get_adjustment().set_upper(rows)
        self.merge_column_row.get_adjustment().set_upper(columns)
        self.merge_row_span_row.get_adjustment().set_upper(rows)
        self.merge_column_span_row.get_adjustment().set_upper(columns)
        self.merge_row_row.set_value(min(self.merge_row_row.get_value(), rows))
        self.merge_column_row.set_value(min(self.merge_column_row.get_value(), columns))
        self.merge_row_span_row.set_value(min(self.merge_row_span_row.get_value(), rows))
        self.merge_column_span_row.set_value(min(self.merge_column_span_row.get_value(), columns))

    def get_merge_draft(self):
        return CellMerge(
            row=int(self.merge_row_row.get_value()) - 1,
            column=int(self.merge_column_row.get_value()) - 1,
            row_span=int(self.merge_row_span_row.get_value()),
            column_span=int(self.merge_column_span_row.get_value()),
        )

    def set_cell_merges(self, merges, on_remove=None, on_edit=None):
        child = self.merge_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.merge_list.remove(child)
            child = next_child
        self.merge_rows = []
        if not merges:
            placeholder = Gtk.Label(label=_('No merged cells'))
            placeholder.add_css_class('dim-label')
            placeholder.set_halign(Gtk.Align.START)
            self.merge_list.append(placeholder)
            return
        for merge in merges:
            row = Adw.ActionRow()
            row.set_title(_('Merged range: row {row}, column {column}').format(
                row=merge.row + 1, column=merge.column + 1))
            row.set_subtitle(_('{rows} rows × {columns} columns').format(
                rows=merge.row_span, columns=merge.column_span))
            edit_button = Gtk.Button.new_from_icon_name('document-edit-symbolic')
            edit_button.set_tooltip_text(_('Edit this merged range'))
            if on_edit is not None:
                edit_button.connect('clicked', lambda button, item=merge: on_edit(item))
            row.add_suffix(edit_button)
            remove_button = Gtk.Button.new_from_icon_name('user-trash-symbolic')
            remove_button.set_tooltip_text(_('Remove this merged range'))
            if on_remove is not None:
                remove_button.connect('clicked', lambda button, item=merge: on_remove(item))
            row.add_suffix(remove_button)
            self.merge_list.append(row)
            self.merge_rows.append(row)

    def set_merge_editor(self, merge=None):
        editing = merge is not None
        self.add_merge_button.set_visible(not editing)
        self.edit_merge_button.set_visible(editing)
        self.cancel_merge_edit_button.set_visible(editing)
        if editing:
            self.merge_row_row.set_value(merge.row + 1)
            self.merge_column_row.set_value(merge.column + 1)
            self.merge_row_span_row.set_value(merge.row_span)
            self.merge_column_span_row.set_value(merge.column_span)

    def set_merge_status(self, message='', is_error=False):
        self.merge_status.set_text(message)
        self.merge_status.set_visible(bool(message))
        if is_error:
            self.merge_status.add_css_class('error')
        else:
            self.merge_status.remove_css_class('error')
            self.merge_status.add_css_class('dim-label')

    def set_merge_error(self, message=''):
        self.set_merge_status(message, is_error=bool(message))

    def set_merge_coverage(self, merges):
        for row_index, entries in enumerate(self.cell_entries):
            for column_index, entry in enumerate(entries):
                covered = any(
                    merge.covers(row_index, column_index)
                    and (merge.row, merge.column) != (row_index, column_index)
                    for merge in merges)
                entry.set_sensitive(not covered)
                if covered:
                    entry.set_tooltip_text(_('Covered by a merged cell'))
                else:
                    entry.set_tooltip_text(_('Row {row}, column {column}').format(
                        row=row_index + 1, column=column_index + 1))

    def get_import_format(self):
        return self.IMPORT_OPTIONS[self.import_format_row.get_selected()][0]

    def set_import_status(self, message='', is_error=False):
        self.import_status.set_text(message)
        self.import_status.set_visible(bool(message))
        if is_error:
            self.import_status.remove_css_class('dim-label')
            self.import_status.add_css_class('error')
        else:
            self.import_status.remove_css_class('error')
            self.import_status.add_css_class('dim-label')

    def get_cells(self):
        return tuple(tuple(entry.get_text() for entry in row) for row in self.cell_entries)

    def get_alignments(self):
        return tuple(ALIGNMENTS[row.get_selected()] for row in self.alignment_rows)

    def get_style(self):
        return self.STYLE_OPTIONS[self.style_row.get_selected()][0]

    def get_environment(self):
        return self.ENVIRONMENT_OPTIONS[self.environment_row.get_selected()][0]

    def get_placement(self):
        return PLACEMENTS[self.placement_row.get_selected()]

    def set_preview(self, text):
        self.preview.get_buffer().set_text(text)

    def set_environment_sensitive(self):
        longtable = self.get_environment() == ENVIRONMENT_LONGTABLE
        if longtable:
            self.table_switch.set_active(False)
        self.table_switch.set_sensitive(not longtable)
        self.longtable_note.set_visible(longtable)
        self.set_wrapper_sensitive()

    def set_wrapper_sensitive(self):
        longtable = self.get_environment() == ENVIRONMENT_LONGTABLE
        wrapper_enabled = not longtable and self.table_switch.get_active()
        self.placement_row.set_sensitive(wrapper_enabled)
        self.center_switch.set_sensitive(wrapper_enabled)
        self.caption_row.set_sensitive(longtable or wrapper_enabled)
        self.label_row.set_sensitive(longtable or wrapper_enabled)
        self.repeat_header_switch.set_sensitive(
            longtable and self.header_switch.get_active()
            and int(self.rows_row.get_value()) > 1)

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
        content.append(data_group)

        columns_group = Adw.PreferencesGroup()
        columns_group.set_title(_('Column Alignment'))
        self.alignment_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        columns_group.add(self.alignment_box)
        content.append(columns_group)

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
        content.append(appearance_group)

        wrapper_group = Adw.PreferencesGroup()
        wrapper_group.set_title(_('Table Environment'))
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
        preview_group.add(preview_scrolled)
        content.append(preview_group)

        self.reset()

    def reset(self):
        self.rows_row.set_value(3)
        self.columns_row.set_value(3)
        self.style_row.set_selected(0)
        self.header_switch.set_active(True)
        self.table_switch.set_active(True)
        self.placement_row.set_selected(0)
        self.center_switch.set_active(True)
        self.caption_row.set_text('')
        self.label_row.set_text('tab:')
        self.set_cells((), 3, 3)
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
        child = self.alignment_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.alignment_box.remove(child)
            child = next_child
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
            self.alignment_box.append(row)
            self.alignment_rows.append(row)

    def get_cells(self):
        return tuple(tuple(entry.get_text() for entry in row) for row in self.cell_entries)

    def get_alignments(self):
        return tuple(ALIGNMENTS[row.get_selected()] for row in self.alignment_rows)

    def get_style(self):
        return self.STYLE_OPTIONS[self.style_row.get_selected()][0]

    def get_placement(self):
        return PLACEMENTS[self.placement_row.get_selected()]

    def set_preview(self, text):
        self.preview.get_buffer().set_text(text)

    def set_wrapper_sensitive(self):
        enabled = self.table_switch.get_active()
        self.placement_row.set_sensitive(enabled)
        self.center_switch.set_sensitive(enabled)
        self.caption_row.set_sensitive(enabled)
        self.label_row.set_sensitive(enabled)

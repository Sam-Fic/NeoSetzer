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
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.dialogs.insert_matrix.matrix_generator import (
    ALIGNMENTS,
    ENVIRONMENT_MATRIX_STAR,
    ENVIRONMENTS,
    MAX_COLUMNS,
    MAX_ROWS,
)


class InsertMatrixView(DialogView):
    '''GTK4/libadwaita view for configuring and previewing a LaTeX matrix.'''

    ALIGNMENT_OPTIONS = (
        ('c', 'Center'),
        ('l', 'Left'),
        ('r', 'Right'),
    )
    ENVIRONMENT_OPTIONS = (
        (ENVIRONMENTS[0], 'pmatrix — round brackets ( )'),
        (ENVIRONMENTS[1], 'bmatrix — square brackets [ ]'),
        (ENVIRONMENTS[2], 'Bmatrix — curly braces { }'),
        (ENVIRONMENTS[3], 'vmatrix — vertical bars | |'),
        (ENVIRONMENTS[4], 'Vmatrix — double vertical bars ‖ ‖'),
        (ENVIRONMENTS[5], 'matrix — no delimiters'),
        (ENVIRONMENTS[6], 'matrix* — no delimiters, column alignment (mathtools)'),
    )

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_content_width(560)
        self.set_content_height(520)

        self.headerbar.set_title_widget(Gtk.Label(label=_('Insert Matrix')))
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
        size_group.set_title(_('Matrix Size'))
        self.rows_row = Adw.SpinRow()
        self.rows_row.set_title(_('Rows'))
        self.rows_row.set_subtitle(_('Number of matrix rows'))
        self.rows_row.set_adjustment(Gtk.Adjustment(
            value=2, lower=1, upper=MAX_ROWS, step_increment=1, page_increment=5))
        size_group.add(self.rows_row)
        self.columns_row = Adw.SpinRow()
        self.columns_row.set_title(_('Columns'))
        self.columns_row.set_subtitle(_('Number of matrix columns'))
        self.columns_row.set_adjustment(Gtk.Adjustment(
            value=2, lower=1, upper=MAX_COLUMNS, step_increment=1, page_increment=1))
        size_group.add(self.columns_row)
        content.append(size_group)

        environment_group = Adw.PreferencesGroup()
        environment_group.set_title(_('Environment'))
        self.environment_row = Adw.ComboRow()
        self.environment_row.set_title(_('Matrix environment'))
        self.environment_row.set_subtitle(_('Bracketed variants come from amsmath; matrix* requires mathtools'))
        self.environment_row.set_model(Gtk.StringList.new([
            label for environment, label in self.ENVIRONMENT_OPTIONS
        ]))
        environment_group.add(self.environment_row)
        self.alignment_row = Adw.ComboRow()
        self.alignment_row.set_title(_('Column alignment'))
        self.alignment_row.set_subtitle(_('Only matrix* supports an alignment argument'))
        self.alignment_row.set_model(Gtk.StringList.new([
            _('Center'),
            _('Left'),
            _('Right'),
        ]))
        environment_group.add(self.alignment_row)
        self.packages_note = Adw.ActionRow()
        self.packages_note.set_title(_('Required packages are added automatically'))
        self.packages_note.set_subtitle(_('amsmath for bracketed matrices; mathtools for matrix*.'))
        self.packages_note.add_css_class('property')
        environment_group.add(self.packages_note)
        content.append(environment_group)

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
        preview_scrolled.set_hexpand(True)
        preview_scrolled.set_min_content_height(140)
        preview_scrolled.set_max_content_height(220)
        preview_scrolled.set_child(self.preview)
        preview_scrolled.add_css_class('preview-card')
        preview_scrolled.set_overflow(Gtk.Overflow.HIDDEN)
        preview_group.add(preview_scrolled)
        content.append(preview_group)

        self.reset()

    def reset(self):
        self.rows_row.set_value(2)
        self.columns_row.set_value(2)
        self.environment_row.set_selected(0)
        self.alignment_row.set_selected(0)
        self.set_environment_sensitive()
        self.set_preview('')

    def get_environment(self):
        return self.ENVIRONMENT_OPTIONS[self.environment_row.get_selected()][0]

    def get_alignment(self):
        return self.ALIGNMENT_OPTIONS[self.alignment_row.get_selected()][0]

    def set_preview(self, text):
        self.preview.get_buffer().set_text(text)

    def set_environment_sensitive(self):
        self.alignment_row.set_sensitive(self.get_environment() == ENVIRONMENT_MATRIX_STAR)

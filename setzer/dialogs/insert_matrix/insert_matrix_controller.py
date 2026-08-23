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
from gi.repository import Gdk, Gtk

from setzer.dialogs.insert_matrix.insert_matrix_viewgtk import InsertMatrixView
from setzer.dialogs.insert_matrix.matrix_generator import MatrixSpec


def _(message):
    return getattr(builtins, '_', lambda value: value)(message)


class InsertMatrixController:
    '''Coordinate the matrix generator view and the active LaTeX document.'''

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = InsertMatrixView(main_window)
        self.document = None
        self._connect_signals()

    def _connect_signals(self):
        self.view.cancel_button.connect('clicked', self._on_cancel)
        self.view.copy_button.connect('clicked', self._on_copy)
        self.view.insert_button.connect('clicked', self._on_insert)
        self.view.rows_row.connect('notify::value', self._refresh_preview)
        self.view.columns_row.connect('notify::value', self._refresh_preview)
        self.view.environment_row.connect('notify::selected', self._on_environment_changed)
        self.view.alignment_row.connect('notify::selected', self._refresh_preview)

    def open(self, document):
        '''Reset the transient form and present it for an active LaTeX document.'''

        self.document = document
        self.view.reset()
        self._refresh_preview()
        self.view.present(self.main_window)
        self.view.rows_row.grab_focus()

    def _get_spec(self):
        return MatrixSpec(
            rows=int(self.view.rows_row.get_value()),
            columns=int(self.view.columns_row.get_value()),
            environment=self.view.get_environment(),
            alignment=self.view.get_alignment(),
        )

    def _refresh_preview(self, *args):
        try:
            self.view.set_preview(self._get_spec().render())
        except ValueError as error:
            self.view.set_preview('')

    def _on_environment_changed(self, row, pspec):
        self.view.set_environment_sensitive()
        self._refresh_preview()

    def _on_cancel(self, button):
        self.view.close()
        self._restore_editor_focus()

    def _on_copy(self, button):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(self._get_spec().render())

    def _on_insert(self, button):
        if self.document is None:
            return
        spec = self._get_spec()
        self.document.add_packages(spec.required_packages)
        self.document.insert_symbol_at_cursor(spec.render())
        self.view.close()
        self._restore_editor_focus()

    def _restore_editor_focus(self):
        if self.document is not None:
            self.document.view.source_view.grab_focus()

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

import setzer.dialogs.document_properties.document_properties_viewgtk as view
from setzer.settings.document_settings import DocumentSettings
from setzer.app.service_locator import ServiceLocator


class DocumentPropertiesDialog(object):
    '''Per-document properties dialog: lets users override global preferences
    for the active document. Each setting has a "Follow global default" option
    (override = None) alongside explicit values.'''

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        self.view = None
        self.document = None

        self._interpreter_options = [
            (_('Follow global default'), None),
            ('XeLaTeX', 'xelatex'),
            ('pdfLaTeX', 'pdflatex'),
            ('LuaLaTeX', 'lualatex'),
            ('Tectonic', 'tectonic'),
            ('latexmk', 'latexmk'),
        ]

        self._indent_options = [
            (_('Follow global default'), None),
            (_('Spaces'), True),
            (_('Tabs'), False),
        ]

        self._tab_width_options = [
            (_('Follow global default'), None),
            ('2', 2),
            ('4', 4),
            ('8', 8),
        ]

    def run(self, document):
        self.document = document
        if self.view is None:
            self.setup()
        self._sync_controls()
        self.view.present(self.main_window)

    def setup(self):
        self.view = view.DocumentPropertiesView(self.main_window)

        self.view.close_button.connect('clicked', lambda *a: self.view.close())
        self.view.apply_button.connect('clicked', self._on_apply)

        # Build System group
        self._switch_override_build = self.view.switch_override_build
        self._combo_interpreter = self.view.combo_interpreter
        self._switch_auto_build = self.view.switch_auto_build
        self._switch_use_latexmk = self.view.switch_use_latexmk
        self._switch_cleanup = self.view.switch_cleanup

        # Editor group
        self._combo_indent_mode = self.view.combo_indent_mode
        self._combo_tab_width = self.view.combo_tab_width

        self._switch_override_build.connect('notify::active', self._on_build_override_toggled)

    def _on_build_override_toggled(self, switch, pspec):
        self._sync_build_switches()

    def _sync_controls(self):
        '''Load current overrides (or global defaults) into the UI controls.'''
        doc = self.document
        s = self.settings

        # --- Build System ---
        # Master switch: ON if any per-document build option is overridden
        any_build_override = (
            DocumentSettings.get_document_override(doc, 'auto_build') is not None or
            DocumentSettings.get_document_override(doc, 'use_latexmk') is not None or
            DocumentSettings.get_document_override(doc, 'cleanup_build_files') is not None
        )
        self._switch_override_build.handler_block_by_func(self._on_build_override_toggled)
        self._switch_override_build.set_active(any_build_override)
        self._switch_override_build.handler_unblock_by_func(self._on_build_override_toggled)
        self._sync_build_switches()

        # Interpreter (keeps its own "Follow global default" option)
        interp = DocumentSettings.get_effective_value(doc, s, 'latex_interpreter')
        interp_global = s.get_value('preferences', 'latex_interpreter')
        interp_override = DocumentSettings.get_document_override(doc, 'latex_interpreter')
        self._sync_combo(self._combo_interpreter, interp_override, interp_global,
                         self._interpreter_options, interp)

        # --- Editor ---
        # Indent mode
        use_spaces = DocumentSettings.get_effective_value(doc, s, 'spaces_instead_of_tabs')
        indent_override = DocumentSettings.get_document_override(doc, 'spaces_instead_of_tabs')
        self._sync_combo(self._combo_indent_mode, indent_override, None,
                         self._indent_options, use_spaces)

        # Tab width
        tab_width = DocumentSettings.get_effective_value(doc, s, 'tab_width')
        tab_width_override = DocumentSettings.get_document_override(doc, 'tab_width')
        self._sync_combo(self._combo_tab_width, tab_width_override, None,
                         self._tab_width_options, tab_width)

    def _sync_combo(self, combo, override, global_value, options, effective):
        '''Set combo box active item based on override state.'''
        if override is None:
            combo.set_selected(0)
        else:
            for i, (label, value) in enumerate(options):
                if value == override:
                    combo.set_selected(i)
                    return
            combo.set_selected(0)

    def _sync_build_switches(self):
        '''Sync the three build option switches based on the master override switch.'''
        doc = self.document
        s = self.settings
        master_active = self._switch_override_build.get_active()

        self._switch_auto_build.set_sensitive(master_active)
        self._switch_use_latexmk.set_sensitive(master_active)
        self._switch_cleanup.set_sensitive(master_active)

        if master_active:
            # Document-level: use override if present, otherwise fall back to global default
            auto_build = DocumentSettings.get_document_override(doc, 'auto_build')
            if auto_build is None:
                auto_build = s.get_value('preferences', 'auto_build')
            self._switch_auto_build.set_active(auto_build)

            use_latexmk = DocumentSettings.get_document_override(doc, 'use_latexmk')
            if use_latexmk is None:
                use_latexmk = s.get_value('preferences', 'use_latexmk')
            self._switch_use_latexmk.set_active(use_latexmk)

            cleanup = DocumentSettings.get_document_override(doc, 'cleanup_build_files')
            if cleanup is None:
                cleanup = s.get_value('preferences', 'cleanup_build_files')
            self._switch_cleanup.set_active(cleanup)
        else:
            # Follow global default: show effective (global) value and disable switches
            self._switch_auto_build.set_active(
                DocumentSettings.get_effective_value(doc, s, 'auto_build'))
            self._switch_use_latexmk.set_active(
                DocumentSettings.get_effective_value(doc, s, 'use_latexmk'))
            self._switch_cleanup.set_active(
                DocumentSettings.get_effective_value(doc, s, 'cleanup_build_files'))

    def _on_apply(self, button):
        '''Save overrides to DocumentSettings.'''
        doc = self.document

        # --- Build System ---
        # Interpreter
        interp_idx = self._combo_interpreter.get_selected()
        if interp_idx == 0:
            DocumentSettings.set_document_override(doc, 'latex_interpreter', None)
        else:
            value = self._interpreter_options[interp_idx][1]
            DocumentSettings.set_document_override(doc, 'latex_interpreter', value)

        # Build options: either save all three as document overrides or clear them
        if self._switch_override_build.get_active():
            DocumentSettings.set_document_override(doc, 'auto_build',
                                                   self._switch_auto_build.get_active())
            DocumentSettings.set_document_override(doc, 'use_latexmk',
                                                   self._switch_use_latexmk.get_active())
            DocumentSettings.set_document_override(doc, 'cleanup_build_files',
                                                   self._switch_cleanup.get_active())
        else:
            DocumentSettings.set_document_override(doc, 'auto_build', None)
            DocumentSettings.set_document_override(doc, 'use_latexmk', None)
            DocumentSettings.set_document_override(doc, 'cleanup_build_files', None)

        # --- Editor ---
        # Indent mode
        indent_idx = self._combo_indent_mode.get_selected()
        if indent_idx == 0:
            DocumentSettings.set_document_override(doc, 'spaces_instead_of_tabs', None)
        else:
            value = self._indent_options[indent_idx][1]
            DocumentSettings.set_document_override(doc, 'spaces_instead_of_tabs', value)

        # Tab width
        tw_idx = self._combo_tab_width.get_selected()
        if tw_idx == 0:
            DocumentSettings.set_document_override(doc, 'tab_width', None)
        else:
            value = self._tab_width_options[tw_idx][1]
            DocumentSettings.set_document_override(doc, 'tab_width', value)

        self.view.close()

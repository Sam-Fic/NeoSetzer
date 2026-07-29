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
        self._combo_interpreter = self.view.combo_interpreter
        self._switch_auto_build = self.view.switch_auto_build
        self._switch_use_latexmk = self.view.switch_use_latexmk
        self._switch_cleanup = self.view.switch_cleanup

        # Editor group
        self._combo_indent_mode = self.view.combo_indent_mode
        self._combo_tab_width = self.view.combo_tab_width

        # Connect switch notify::active to clear inherited state when user toggles
        self._switch_auto_build.connect('notify::active', self._on_switch_toggled)
        self._switch_use_latexmk.connect('notify::active', self._on_switch_toggled)
        self._switch_cleanup.connect('notify::active', self._on_switch_toggled)

    def _on_switch_toggled(self, switch, pspec):
        '''When user manually toggles a switch, clear the inherited state.'''
        if hasattr(switch, '_is_inherited') and switch._is_inherited:
            switch._is_inherited = False
            switch.remove_css_class('dim-label')

    def _sync_controls(self):
        '''Load current overrides (or global defaults) into the UI controls.'''
        doc = self.document
        s = self.settings

        # --- Build System ---
        # Interpreter
        interp = DocumentSettings.get_effective_value(doc, s, 'latex_interpreter')
        interp_global = s.get_value('preferences', 'latex_interpreter')
        interp_override = DocumentSettings.get_document_override(doc, 'latex_interpreter')
        self._sync_combo(self._combo_interpreter, interp_override, interp_global,
                         self._interpreter_options, interp)

        # Auto build
        auto_build = DocumentSettings.get_effective_value(doc, s, 'auto_build')
        auto_build_override = DocumentSettings.get_document_override(doc, 'auto_build')
        self._sync_switch(self._switch_auto_build, auto_build_override, auto_build)

        # Use latexmk
        use_latexmk = DocumentSettings.get_effective_value(doc, s, 'use_latexmk')
        use_latexmk_override = DocumentSettings.get_document_override(doc, 'use_latexmk')
        self._sync_switch(self._switch_use_latexmk, use_latexmk_override, use_latexmk)

        # Cleanup
        cleanup = DocumentSettings.get_effective_value(doc, s, 'cleanup_build_files')
        cleanup_override = DocumentSettings.get_document_override(doc, 'cleanup_build_files')
        self._sync_switch(self._switch_cleanup, cleanup_override, cleanup)

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
            # Use the "Follow global" option (index 0)
            combo.set_active(0)
        else:
            # Find the matching option
            for i, (label, value) in enumerate(options):
                if value == override:
                    combo.set_active(i)
                    return
            combo.set_active(0)

    def _sync_switch(self, switch, override, effective):
        '''Set switch state based on override state.'''
        if override is None:
            # For "follow global", we show the effective value but mark it as inherited
            switch.set_active(effective)
            switch._is_inherited = True
            switch.add_css_class('dim-label')
        else:
            switch.set_active(override)
            switch._is_inherited = False
            switch.remove_css_class('dim-label')

    def _on_apply(self, button):
        '''Save overrides to DocumentSettings.'''
        doc = self.document

        # --- Build System ---
        # Interpreter
        interp_idx = self._combo_interpreter.get_active()
        if interp_idx == 0:
            DocumentSettings.set_document_override(doc, 'latex_interpreter', None)
        else:
            value = self._interpreter_options[interp_idx][1]
            DocumentSettings.set_document_override(doc, 'latex_interpreter', value)

        # Auto build
        if getattr(self._switch_auto_build, '_is_inherited', False):
            DocumentSettings.set_document_override(doc, 'auto_build', None)
        else:
            DocumentSettings.set_document_override(doc, 'auto_build',
                                                   self._switch_auto_build.get_active())

        # Use latexmk
        if getattr(self._switch_use_latexmk, '_is_inherited', False):
            DocumentSettings.set_document_override(doc, 'use_latexmk', None)
        else:
            DocumentSettings.set_document_override(doc, 'use_latexmk',
                                                   self._switch_use_latexmk.get_active())

        # Cleanup
        if getattr(self._switch_cleanup, '_is_inherited', False):
            DocumentSettings.set_document_override(doc, 'cleanup_build_files', None)
        else:
            DocumentSettings.set_document_override(doc, 'cleanup_build_files',
                                                   self._switch_cleanup.get_active())

        # --- Editor ---
        # Indent mode
        indent_idx = self._combo_indent_mode.get_active()
        if indent_idx == 0:
            DocumentSettings.set_document_override(doc, 'spaces_instead_of_tabs', None)
        else:
            value = self._indent_options[indent_idx][1]
            DocumentSettings.set_document_override(doc, 'spaces_instead_of_tabs', value)

        # Tab width
        tw_idx = self._combo_tab_width.get_active()
        if tw_idx == 0:
            DocumentSettings.set_document_override(doc, 'tab_width', None)
        else:
            value = self._tab_width_options[tw_idx][1]
            DocumentSettings.set_document_override(doc, 'tab_width', value)

        self.view.close()

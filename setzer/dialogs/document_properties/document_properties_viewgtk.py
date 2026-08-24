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
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class DocumentPropertiesView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        interpreter_labels = [
            _('Follow global default'),
            'XeLaTeX',
            'pdfLaTeX',
            'LuaLaTeX',
            'Tectonic',
            'latexmk',
        ]

        indent_labels = [
            _('Follow global default'),
            _('Spaces'),
            _('Tabs'),
        ]

        tab_width_labels = [
            _('Follow global default'),
            '2',
            '4',
            '8',
        ]

        self.set_content_width(500)
        self.headerbar.set_title_widget(Adw.WindowTitle(title=_('Document Properties')))
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.close_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        self.headerbar.pack_start(self.close_button)

        self.apply_button = Gtk.Button.new_with_mnemonic(_('_Apply'))
        self.apply_button.add_css_class('suggested-action')
        self.headerbar.pack_end(self.apply_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # --- Build System group ---
        build_group = Adw.PreferencesGroup()
        build_group.set_title(_('Build System'))

        # Interpreter row
        interp_row = Adw.ComboRow()
        interp_row.set_title(_('LaTeX Interpreter'))
        interp_row.set_subtitle(_('Engine used to compile this document'))
        interp_model = Gtk.StringList.new(interpreter_labels)
        interp_row.set_model(interp_model)
        self.combo_interpreter = interp_row
        build_group.add(interp_row)

        # Override build options row
        override_row = Adw.SwitchRow()
        override_row.set_title(_('Override build options'))
        override_row.set_subtitle(_('Use document-specific settings instead of global defaults'))
        self.switch_override_build = override_row
        build_group.add(override_row)

        # Auto build row
        auto_build_row = Adw.SwitchRow()
        auto_build_row.set_title(_('Auto Build'))
        auto_build_row.set_subtitle(_('Automatically build when the document changes'))
        self.switch_auto_build = auto_build_row
        build_group.add(auto_build_row)

        # Use latexmk row
        latexmk_row = Adw.SwitchRow()
        latexmk_row.set_title(_('Use latexmk'))
        latexmk_row.set_subtitle(_('Use latexmk instead of running the engine directly'))
        self.switch_use_latexmk = latexmk_row
        build_group.add(latexmk_row)

        # Cleanup row
        cleanup_row = Adw.SwitchRow()
        cleanup_row.set_title(_('Cleanup Build Files'))
        cleanup_row.set_subtitle(_('Automatically remove auxiliary files after building'))
        self.switch_cleanup = cleanup_row
        build_group.add(cleanup_row)

        content.append(build_group)

        # --- Editor group ---
        editor_group = Adw.PreferencesGroup()
        editor_group.set_title(_('Editor'))

        # Indent mode row
        indent_row = Adw.ComboRow()
        indent_row.set_title(_('Indent Mode'))
        indent_row.set_subtitle(_('Use spaces or tabs for indentation'))
        indent_model = Gtk.StringList.new(indent_labels)
        indent_row.set_model(indent_model)
        self.combo_indent_mode = indent_row
        editor_group.add(indent_row)

        # Tab width row
        tab_width_row = Adw.ComboRow()
        tab_width_row.set_title(_('Tab Width'))
        tab_width_row.set_subtitle(_('Number of spaces per indent level'))
        tab_width_model = Gtk.StringList.new(tab_width_labels)
        tab_width_row.set_model(tab_width_model)
        self.combo_tab_width = tab_width_row
        editor_group.add(tab_width_row)

        content.append(editor_group)

        self.topbox.append(content)

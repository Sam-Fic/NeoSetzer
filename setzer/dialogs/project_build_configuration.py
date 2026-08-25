#!/usr/bin/env python3
# coding: utf-8

'''GTK editor for a project's optional `.neosetzer/build.json` file.'''

import os
import shlex

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.project.build_configuration import ProjectBuildConfiguration


class ProjectBuildConfigurationDialog:
    '''Small, explicit editor for settings shared by a LaTeX project.'''

    _interpreter_values = (None, 'pdflatex', 'xelatex', 'lualatex', 'tectonic')
    _tristate_values = (None, True, False)
    _shell_values = (None, 'disable', 'restricted', 'enable')
    _bibliography_values = (None, 'auto', 'bibtex', 'biber')

    def __init__(self, parent, document):
        self.parent = parent
        self.document = document
        self.configuration = (
            ProjectBuildConfiguration.discover(document.get_filename())
            or ProjectBuildConfiguration(
                os.path.dirname(document.get_filename())))
        self.dialog = Adw.Dialog()
        self.dialog.set_title(_('Project Build Configuration'))
        self.dialog.set_content_width(540)
        self._build_view()
        self._load_values()

    def present(self):
        self.dialog.present(self.parent)

    def _build_view(self):
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        heading = Adw.WindowTitle(
            title=_('Project Build Configuration'),
            subtitle=_('Stored in .neosetzer/build.json for this project'))
        content.append(heading)

        group = Adw.PreferencesGroup()
        group.set_title(_('Build'))
        group.set_description(
            _('Leave a setting on “Follow global default” to inherit it.'))

        self.root_document = Adw.EntryRow()
        self.root_document.set_title(_('Root document'))
        self.root_document.set_show_apply_button(False)
        self.root_document.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        self.root_document.set_text('')
        self.root_document.set_tooltip_text(
            _('Relative .tex path, for example main.tex'))
        group.add(self.root_document)

        self.output_directory = Adw.EntryRow()
        self.output_directory.set_title(_('Output directory'))
        self.output_directory.set_show_apply_button(False)
        self.output_directory.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        self.output_directory.set_tooltip_text(
            _('Relative path, for example build'))
        group.add(self.output_directory)

        self.interpreter = self._combo_row(
            _('LaTeX interpreter'),
            (_('Follow global default'), 'pdfLaTeX', 'XeLaTeX', 'LuaLaTeX',
             'Tectonic'))
        group.add(self.interpreter)

        self.use_latexmk = self._combo_row(
            _('Use latexmk'),
            (_('Follow global default'), _('Enabled'), _('Disabled')))
        group.add(self.use_latexmk)

        self.cleanup = self._combo_row(
            _('Cleanup build files'),
            (_('Follow global default'), _('Enabled'), _('Disabled')))
        group.add(self.cleanup)

        self.shell_mode = self._combo_row(
            _('Shell commands'),
            (_('Follow global default'), _('Disabled'), _('Restricted'),
             _('Enabled')))
        group.add(self.shell_mode)

        self.bibliography = self._combo_row(
            _('Bibliography backend'),
            (_('Follow log detection'), _('Automatic'), 'BibTeX', 'Biber'))
        group.add(self.bibliography)

        self.additional_arguments = Adw.EntryRow()
        self.additional_arguments.set_title(_('Additional arguments'))
        self.additional_arguments.set_show_apply_button(False)
        self.additional_arguments.set_tooltip_text(
            _('Space-separated compiler arguments; output and shell flags are controlled above.'))
        group.add(self.additional_arguments)
        content.append(group)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        cancel.connect('clicked', lambda *_: self.dialog.close())
        save = Gtk.Button.new_with_mnemonic(_('_Save configuration'))
        save.add_css_class('suggested-action')
        save.connect('clicked', self._on_save)
        buttons.append(cancel)
        buttons.append(save)
        content.append(buttons)
        self.dialog.set_child(content)

    @staticmethod
    def _combo_row(title, labels):
        row = Adw.ComboRow()
        row.set_title(title)
        row.set_model(Gtk.StringList.new(labels))
        return row

    @staticmethod
    def _select(row, values, value):
        try:
            row.set_selected(values.index(value))
        except ValueError:
            row.set_selected(0)

    def _load_values(self):
        values = self.configuration.load()
        self.root_document.set_text(values['root_document'] or '')
        self.output_directory.set_text(values['output_directory'] or '')
        self.additional_arguments.set_text(' '.join(values['additional_arguments']))
        self._select(self.interpreter, self._interpreter_values,
                     values['interpreter'])
        self._select(self.use_latexmk, self._tristate_values,
                     values['use_latexmk'])
        self._select(self.cleanup, self._tristate_values,
                     values['cleanup_build_files'])
        self._select(self.shell_mode, self._shell_values, values['shell_mode'])
        self._select(self.bibliography, self._bibliography_values,
                     values['bibliography_backend'])

    def _on_save(self, *_args):
        arguments = self.additional_arguments.get_text().strip()
        try:
            additional_arguments = tuple(shlex.split(arguments)) if arguments else ()
            self.configuration.save({
                'root_document': self.root_document.get_text().strip() or None,
                'output_directory': self.output_directory.get_text().strip() or None,
                'interpreter': self._interpreter_values[self.interpreter.get_selected()],
                'use_latexmk': self._tristate_values[self.use_latexmk.get_selected()],
                'cleanup_build_files': self._tristate_values[self.cleanup.get_selected()],
                'shell_mode': self._shell_values[self.shell_mode.get_selected()],
                'bibliography_backend': self._bibliography_values[
                    self.bibliography.get_selected()],
                'additional_arguments': additional_arguments,
            })
        except OSError:
            # The dialog stays open; a later save retry remains possible.
            return
        self.dialog.close()

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

import os

from gi.repository import Adw, Gtk

from setzer.app.service_locator import ServiceLocator


class WelcomeScreen(object):
    '''Welcome screen presenter.

    Drives the view returned by WelcomeScreenView(): it keeps the
    recent-documents list in sync with the workspace and wires the
    quick-action buttons (New LaTeX / New BibTeX / Template Wizard) to
    the workspace actions.

    activate()/deactivate() are retained as no-ops for call-site
    compatibility.
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().welcome_screen
        self.is_active = False

        # quick-action buttons
        self.view.new_latex_button.connect('clicked', self.on_new_latex_clicked)
        self.view.new_bibtex_button.connect('clicked', self.on_new_bibtex_clicked)
        self.view.wizard_button.connect('clicked', self.on_wizard_clicked)

        # open a recent document when its row is activated
        self.view.recent_listbox.connect('row-activated', self.on_recent_row_activated)

        # keep the recent list in sync with the workspace
        self.workspace.connect('update_recently_opened_documents', self.on_recently_opened_changed)

        self.refresh_recent_documents()
        self.activate()

    def activate(self):
        self.is_active = True

    def deactivate(self):
        self.is_active = False

    # --- quick actions ---

    def on_new_latex_clicked(self, button):
        self.workspace.actions.new_latex_document()

    def on_new_bibtex_clicked(self, button):
        self.workspace.actions.new_bibtex_document()

    def on_wizard_clicked(self, button):
        # The document wizard inserts a template into the active document,
        # so first create and activate a blank LaTeX document, then open it.
        document = self.workspace.create_latex_document()
        self.workspace.add_document(document)
        self.workspace.set_active_document(document)
        self.workspace.actions.start_wizard()

    # --- recent documents ---

    def on_recently_opened_changed(self, workspace, recently_opened_documents):
        self.refresh_recent_documents()

    def on_recent_row_activated(self, listbox, row):
        filename = getattr(row, 'filename', None)
        if filename is not None:
            import os.path
            if not os.path.isfile(filename):
                self.workspace.remove_recently_opened_document(filename)
                return
            self.workspace.open_document_by_filename(filename)

    def refresh_recent_documents(self):
        listbox = self.view.recent_listbox
        # clear existing rows
        while (child := listbox.get_first_child()) is not None:
            listbox.remove(child)

        documents = self.workspace.recently_opened_documents.values()
        # most-recently-used first
        documents = sorted(documents, key=lambda val: val['date'], reverse=True)

        if len(documents) == 0:
            self.view.empty_label.set_visible(True)
            return

        self.view.empty_label.set_visible(False)
        for doc in documents:
            filename = doc['filename']
            row = Adw.ActionRow()
            row.filename = filename
            row.set_title(os.path.basename(filename))
            row.set_subtitle(os.path.dirname(filename))
            row.set_activatable(True)

            if filename.endswith('.bib'):
                icon_name = 'document-bibtex-symbolic'
            else:
                icon_name = 'document-latex-symbolic'
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(16)
            row.add_prefix(icon)

            open_icon = Gtk.Image.new_from_icon_name('go-next-symbolic')
            row.add_suffix(open_icon)

            listbox.append(row)

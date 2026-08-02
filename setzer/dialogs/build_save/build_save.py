#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
gi.require_version('Adw', '1')
from gi.repository import Adw


class BuildSaveDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace

    def run(self, document):
        self.setup(document)
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, document):
        self.view = Adw.AlertDialog(
            heading=_('Document "{document}" has no filename.').format(document=document.get_displayname()),
            body=_('Please save your document to a file, so the build system knows where to put the .pdf (it will be in the same folder as your document). A file chooser will appear for you to select a location and filename.'))
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('save', _('Save document now'))
        self.view.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('save')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'save':
            self.workspace.actions.save_as()

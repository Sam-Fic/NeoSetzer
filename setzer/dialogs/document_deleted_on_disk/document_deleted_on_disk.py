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
gi.require_version('Adw', '1')
from gi.repository import Adw


class DocumentDeletedOnDiskDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.parameters = None

    def run(self, parameters):
        if parameters['document'] == None: return

        self.parameters = parameters

        self.setup(self.parameters['document'])
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, document):
        self.document = document
        self.view = Adw.AlertDialog(
            heading=_('Document »{document}« was deleted from disk or moved.').format(document=document.get_displayname()),
            body=_('If you close it or close Setzer without saving, this document will be lost.'))
        self.view.add_response('save_as', _('Save As…'))
        self.view.add_response('ok', _('Ok'))
        self.view.set_response_appearance('save_as', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('save_as')
        self.view.set_close_response('ok')

    def dialog_process_response(self, dialog, result):
        response = dialog.choose_finish(result)
        if response == 'save_as':
            # 延迟导入避免与 dialog_locator 的循环导入
            # （dialog_locator 顶层 import 本模块）。
            from setzer.dialogs.dialog_locator import DialogLocator
            DialogLocator.get_dialog('save_document').run(self.document)

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''GTK4 presenter for creating and opening a user-owned example project.'''

from __future__ import annotations

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from setzer.app.service_locator import ServiceLocator
from setzer.example_project.project_store import ExampleProjectError, ExampleProjectStore


class ExampleProjectOpener:
    '''Choose a destination, create a safe project copy, and open its root.''' 

    def __init__(self, workspace, parent_window):
        self.workspace = workspace
        self.parent_window = parent_window
        self.folder_dialog = None
        self._on_opened = None

    def choose_and_open(self, on_opened=None):
        '''Ask for a parent folder before creating an example project copy.'''
        self._on_opened = on_opened
        self.folder_dialog = Gtk.FileDialog()
        self.folder_dialog.set_modal(True)
        self.folder_dialog.set_title(_('Choose a Folder for the Example Project'))

        documents_directory = GLib.get_user_special_dir(
            GLib.UserDirectory.DIRECTORY_DOCUMENTS)
        initial_directory = documents_directory or os.path.expanduser('~')
        self.folder_dialog.set_initial_folder(Gio.File.new_for_path(initial_directory))
        self.folder_dialog.select_folder(
            self.parent_window, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            # User cancellation is not an error and requires no feedback.
            return
        finally:
            self.folder_dialog = None

        if folder is None or folder.get_path() is None:
            return

        source_directory = os.path.join(
            ServiceLocator.get_resources_path(), 'example_project')
        try:
            main_document = ExampleProjectStore(
                source_directory, folder.get_path()).create()
        except ExampleProjectError as error:
            self._show_toast(str(error), 5)
            return

        self.workspace.open_document_by_filename_with_spinner(main_document)
        self._show_toast(_('Example project created'), 3)
        if self._on_opened is not None:
            self._on_opened()

    def _show_toast(self, message, timeout):
        if self.parent_window is None or not hasattr(self.parent_window, 'toast_overlay'):
            return
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.parent_window.toast_overlay.add_toast(toast)

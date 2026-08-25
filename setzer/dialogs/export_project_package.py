#!/usr/bin/env python3
# coding: utf-8

'''User-facing safe export of a LaTeX project's source package.'''

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from setzer.project.package_export import ProjectPackageExporter


class ExportProjectPackageDialog:

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.exporter = None
        self.plan = None

    def run(self, document):
        if document is None or document.get_filename() is None:
            self._show_toast(_('Save a LaTeX document before exporting its project.'))
            return
        self.exporter = ProjectPackageExporter(document.get_filename())
        self.plan = self.exporter.create_plan()
        if self.plan.missing_files:
            self._show_toast(
                _('Project package will record {count} missing dependency file(s).').format(
                    count=len(self.plan.missing_files)))
        dialog = Gtk.FileDialog()
        dialog.set_modal(True)
        dialog.set_title(_('Export Project Package As…'))
        dialog.set_initial_name(self.plan.archive_root + '.zip')
        dialog.set_initial_folder(Gio.File.new_for_path(self.plan.project_root))
        zip_filter = Gtk.FileFilter()
        zip_filter.add_mime_type('application/zip')
        zip_filter.add_pattern('*.zip')
        zip_filter.set_name(_('ZIP Archives'))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(zip_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(zip_filter)
        dialog.save(self.main_window, None, self._on_response)

    def _on_response(self, dialog, result):
        try:
            destination = dialog.save_finish(result)
        except GLib.Error:
            return
        if destination is None:
            return
        try:
            exported = self.exporter.export(destination.get_path(), self.plan)
        except FileExistsError:
            self._show_toast(_('A file with that name already exists; choose a new name.'))
        except Exception as error:
            self._show_toast(_('Could not export project package: {error}').format(
                error=str(error)))
        else:
            self._show_toast(_('Project package exported to: {filename}').format(
                filename=os.path.basename(exported)))

    def _show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        self.main_window.toast_overlay.add_toast(toast)

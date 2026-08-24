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
from gi.repository import Gtk, Gdk, Gio, GLib

import os.path

from setzer.helpers.observable import Observable
from setzer.popovers.document_switcher.document_switcher_viewgtk import DocumentSwitcherView
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.service_locator import ServiceLocator


class DocumentSwitcher(Observable):

    def __init__(self, workspace):
        Observable.__init__(self)
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = DocumentSwitcherView()
        self.view.search_entry.connect('search-changed', self.on_search_changed)

        self.root_selection_mode = False
        self._is_visible = False
        self._dirty = False

        self._register_context_actions()

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('update_recently_opened_documents', self.on_update_recently_opened_documents)

        self.view.dialog.connect('closed', self.on_dialog_closed)
        self.view.set_root_document_row.connect('activated', self.set_selection_mode)
        self.view.unset_root_document_row.connect('activated', self.unset_root_document)
        self.view.cancel_button.connect('clicked', self.activate_normal_mode)
        self.view.other_documents_row.connect('activated', self.on_other_docs_clicked)

        self._rebuild_rows()
        self._update_root_buttons()

    # ---- context menu ----

    def _register_context_actions(self):
        main_window = self.main_window

        def add_action(name, callback):
            action = Gio.SimpleAction.new(name, GLib.VariantType('s'))
            action.connect('activate', callback)
            main_window.add_action(action)

        add_action('tab-ctx-copy-path', self._on_copy_path)
        add_action('tab-ctx-copy-relative-path', self._on_copy_relative_path)
        add_action('tab-ctx-open-folder', self._on_open_containing_folder)
        add_action('tab-ctx-close-others', self._on_close_others)

    def _on_copy_path(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
            return
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(filename)

    def _on_copy_relative_path(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
            return
        base_dir = None
        root_doc = self.workspace.root_document
        if root_doc is not None and root_doc.get_filename() is not None:
            base_dir = root_doc.get_dirname()
        else:
            active = self.workspace.get_active_document()
            if active is not None and active.get_filename() is not None:
                base_dir = active.get_dirname()
        if base_dir is None:
            relpath = filename
        else:
            try:
                relpath = os.path.relpath(filename, base_dir)
            except ValueError:
                relpath = filename
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(relpath)

    def _on_open_containing_folder(self, action, parameter):
        filename = parameter.get_string()
        if not filename:
            return
        folder = os.path.dirname(filename)
        if not folder:
            return
        try:
            folder_uri = GLib.filename_to_uri(folder)
        except Exception:
            return
        if not folder_uri.endswith('/'):
            folder_uri += '/'
        try:
            Gio.AppInfo.launch_default_for_uri(folder_uri)
        except Exception:
            pass

    def _on_close_others(self, action, parameter):
        keep_filename = parameter.get_string()
        to_close = [d for d in list(self.workspace.open_documents)
                    if d.get_filename() != keep_filename]
        for document in to_close:
            if not document.source_buffer.get_modified():
                self.workspace.actions.push_closed_document(document.get_filename())
                self.workspace.remove_document(document)

    def _build_context_menu(self, document):
        menu = Gio.Menu()
        filename = document.get_filename()
        has_path = filename is not None

        if has_path:
            section_file = Gio.Menu()
            item_copy_path = Gio.MenuItem.new(_('Copy Path'), 'win.tab-ctx-copy-path')
            item_copy_path.set_action_and_target_value('win.tab-ctx-copy-path',
                                                        GLib.Variant('s', filename))
            section_file.append_item(item_copy_path)

            item_copy_rel = Gio.MenuItem.new(_('Copy Relative Path'), 'win.tab-ctx-copy-relative-path')
            item_copy_rel.set_action_and_target_value('win.tab-ctx-copy-relative-path',
                                                       GLib.Variant('s', filename))
            section_file.append_item(item_copy_rel)

            item_open_folder = Gio.MenuItem.new(_('Open Containing Folder'), 'win.tab-ctx-open-folder')
            item_open_folder.set_action_and_target_value('win.tab-ctx-open-folder',
                                                          GLib.Variant('s', filename))
            section_file.append_item(item_open_folder)
            menu.append_section(None, section_file)

        section_close = Gio.Menu()
        item_close_others = Gio.MenuItem.new(_('Close Others'), 'win.tab-ctx-close-others')
        item_close_others.set_action_and_target_value('win.tab-ctx-close-others',
                                                       GLib.Variant('s', filename or ''))
        section_close.append_item(item_close_others)
        menu.append_section(None, section_close)

        return menu

    def _show_context_menu(self, row, document, x, y):
        menu_model = self._build_context_menu(document)
        popover = Gtk.PopoverMenu()
        popover.set_has_arrow(False)
        popover.set_menu_model(menu_model)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.set_parent(row)
        popover.connect('map', lambda p: p.grab_focus())
        popover.connect('closed', lambda p: p.unparent())
        popover.popup()

    def _on_row_right_click(self, gesture, n_press, x, y):
        if n_press != 1:
            return
        row = gesture.get_widget()
        document = getattr(row, 'document', None)
        if document is None:
            return
        self._show_context_menu(row, document, int(x), int(y))
        gesture.reset()

    # ---- open / close ----

    def open(self):
        self._is_visible = True
        self.view.query = ''
        self.view.search_entry.set_text('')
        if self._dirty:
            self._dirty = False
            self._rebuild_rows()
        self.view.dialog.present(self.main_window)

    def on_dialog_closed(self, dialog=None):
        self._is_visible = False
        active_document = self.workspace.get_active_document()
        if active_document is not None:
            active_document.view.source_view.grab_focus()
        self.activate_normal_mode()

    def _show_switcher(self):
        self._is_visible = True
        if self._dirty:
            self._dirty = False
            self._rebuild_rows()
        self.view.dialog.present(self.main_window)

    # ---- rebuild ----

    def _rebuild_rows(self):
        query = self.view.query
        active_doc = self.workspace.get_active_document()

        self.view.update_open_items(
            self.workspace.open_documents,
            self.root_selection_mode,
            active_doc,
            query,
        )
        for row in self.view.open_rows:
            row.connect('activated', self.on_row_activated)
            row.close_button.connect('clicked', self.on_close_button_clicked)
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            gesture.set_propagation_phase(Gtk.PropagationPhase.TARGET)
            gesture.connect('pressed', self._on_row_right_click)
            row.add_controller(gesture)

        self.view.update_recent_items(
            self.workspace.recently_opened_documents,
            self.workspace.open_documents,
            query,
        )
        for row in self.view.recent_rows:
            row.connect('activated', self.on_recent_row_activated)

        self._update_root_buttons()

    # ---- signal handlers ----

    def on_search_changed(self, entry):
        self.view.query = entry.get_text().strip().lower()
        self._rebuild_rows()

    def on_new_document(self, workspace, document):
        document.connect('filename_change', self.on_name_change)
        document.connect('displayname_change', self.on_name_change)
        document.connect('modified_changed', self.on_modified_changed)
        document.connect('is_root_changed', self.on_is_root_changed)
        self._mark_dirty()

    def on_document_removed(self, workspace, document):
        document.disconnect('filename_change', self.on_name_change)
        document.disconnect('displayname_change', self.on_name_change)
        document.disconnect('modified_changed', self.on_modified_changed)
        document.disconnect('is_root_changed', self.on_is_root_changed)
        self._mark_dirty()

    def on_new_active_document(self, workspace, document):
        self._mark_dirty()

    def on_name_change(self, document, name=None):
        self._mark_dirty()

    def on_is_root_changed(self, document, is_root):
        self._mark_dirty()

    def on_modified_changed(self, document):
        self._mark_dirty()

    def on_update_recently_opened_documents(self, workspace, recently_opened_documents):
        self._mark_dirty()

    def _mark_dirty(self):
        if not self._is_visible:
            self._dirty = True
            return
        self._rebuild_rows()

    # ---- row actions ----

    def on_row_activated(self, row):
        if row is None:
            return
        document = row.document
        if self.root_selection_mode:
            self.workspace.set_one_document_root(document)
            self.activate_normal_mode()
        else:
            self.workspace.set_active_document(document)
            self.view.dialog.close()

    def on_recent_row_activated(self, row):
        if row is None:
            return
        self.view.dialog.close()
        self.workspace.open_document_by_filename_with_spinner(row.filename)

    def on_close_button_clicked(self, button):
        row = button.row
        document = row.document
        self.workspace.actions.push_closed_document(document.get_filename())
        if document.source_buffer.get_modified():
            is_active = (document == self.workspace.get_active_document())
            self.view.dialog.close()
            dialog = DialogLocator.get_dialog('close_confirmation')
            dialog.run({'unsaved_document': document, 'is_active': is_active}, self.on_close_document_callback)
        else:
            if document == self.workspace.get_active_document():
                self.view.dialog.close()
            self.workspace.remove_document(document)

    def on_close_document_callback(self, parameters):
        is_active = parameters['is_active']

        if parameters['response'] == 0:
            self.workspace.remove_document(parameters['unsaved_document'])
        elif parameters['response'] == 2:
            document = parameters['unsaved_document']
            if document.get_filename() is None:
                self.workspace.set_active_document(document)
                DialogLocator.get_dialog('save_document').run(
                    document, self._on_save_new_document_callback, parameters)
                return
            else:
                if document.save_to_disk():
                    self.workspace.remove_document(document)
                else:
                    if is_active:
                        self.workspace.set_active_document(document)
                    self._show_switcher()
                    return

        if not is_active or parameters['response'] == 1:
            self._show_switcher()

    def _on_save_new_document_callback(self, parameters):
        self._show_switcher()

    def on_other_docs_clicked(self, button):
        self.workspace.actions.actions['open-document-dialog'].activate()
        self.view.dialog.close()

    # ---- root selection mode ----

    def set_selection_mode(self, action, parameter=None):
        self.root_selection_mode = True
        self.view.dialog.set_title(_('Select Root Document'))
        self.view.set_root_document_row.set_sensitive(False)
        self.view.unset_root_document_row.set_sensitive(True)
        self.view.explanation_group.set_visible(True)
        self.view.root_group.set_visible(True)
        self._rebuild_rows()

    def unset_root_document(self, action, parameter=None):
        self.workspace.unset_root_document()
        self.activate_normal_mode()

    def activate_normal_mode(self, button=None):
        self.root_selection_mode = False
        self.view.dialog.set_title(_('Open Documents'))
        self.view.explanation_group.set_visible(False)
        self.view.root_group.set_visible(False)
        self._rebuild_rows()

    def _update_root_buttons(self):
        has_latex = len(self.workspace.open_latex_documents) > 0
        self.view.set_root_document_row.set_sensitive(has_latex)
        self.view.unset_root_document_row.set_sensitive(self.workspace.root_document is not None)

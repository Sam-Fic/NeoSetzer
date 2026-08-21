#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""GTK4 command palette dialog."""

from __future__ import annotations

import builtins

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk, Pango

from setzer.command_palette.catalog import CommandCatalog, CommandDescriptor
from setzer.dialogs.helpers.dialog_viewgtk import DialogView


def _(message: str) -> str:
    """Look up a runtime gettext translation with a test-safe fallback."""

    return getattr(builtins, '_', lambda value: value)(message)


class CommandPaletteDialog(DialogView):
    """A reusable modal command palette backed by ``CommandCatalog``."""

    def __init__(self, main_window, workspace):
        DialogView.__init__(self, main_window)
        self.main_window = main_window
        self.workspace = workspace
        self.catalog = CommandCatalog(workspace.actions)
        self.commands: list[CommandDescriptor] = []
        self._previous_focus = None

        self.set_title(_('Command Palette'))
        self.set_content_width(640)
        self.set_content_height(440)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        self.topbox.append(content)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(_('Search commands'))
        self.search_entry.set_hexpand(True)
        self.search_entry.connect('search-changed', self.on_search_changed)
        self.search_entry.connect('activate', self.on_activate)
        content.append(self.search_entry)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        # 不加 'boxed-list'（libadwaita 会给该 class 加边框+阴影），用标准列表样式即可。
        self.listbox.add_css_class('command-palette-list')
        self.listbox.connect('row-activated', self.on_row_activated)

        scrolled_window = Gtk.ScrolledWindow()
        scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.set_vexpand(True)
        scrolled_window.set_child(self.listbox)
        content.append(scrolled_window)

        self.empty_status_page = Adw.StatusPage()
        self.empty_status_page.set_icon_name('system-search-symbolic')
        self.empty_status_page.set_title(_('No commands found'))
        self.empty_status_page.set_valign(Gtk.Align.CENTER)
        self.empty_status_page.set_vexpand(True)
        self.empty_status_page.set_visible(False)
        content.append(self.empty_status_page)

        self.hint_label = Gtk.Label(label=_('Use ↑ and ↓ to select, Enter to run'))
        self.hint_label.add_css_class('dim-label')
        self.hint_label.set_halign(Gtk.Align.START)
        content.append(self.hint_label)

        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller)
        self.connect('closed', self.on_closed)

    def present(self):
        """Refresh enabled commands and focus the search field every time."""

        self._previous_focus = self.main_window.get_focus()
        self.search_entry.set_text('')
        self.refresh_results()
        Adw.Dialog.present(self, self.main_window)
        self.search_entry.grab_focus()

    def close(self):
        Adw.Dialog.close(self)

    def on_closed(self, dialog):
        if self._previous_focus is not None:
            self._previous_focus.grab_focus()
        self._previous_focus = None

    def on_search_changed(self, entry):
        self.refresh_results()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Down:
            self.select_relative(1)
            return True
        if keyval == Gdk.KEY_Up:
            self.select_relative(-1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return self.activate_selected()
        return False

    def on_activate(self, entry):
        self.activate_selected()

    def on_row_activated(self, listbox, row):
        index = row.get_index()
        if 0 <= index < len(self.commands):
            self.execute(self.commands[index])

    def clear_rows(self):
        child = self.listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.listbox.remove(child)
            child = next_child

    def refresh_results(self):
        self.commands = self.catalog.search(self.search_entry.get_text())
        self.clear_rows()
        for command in self.commands:
            self.listbox.append(self.create_row(command))
        has_results = len(self.commands) > 0
        self.listbox.set_visible(has_results)
        self.empty_status_page.set_visible(not has_results)
        if has_results:
            self.listbox.select_row(self.listbox.get_row_at_index(0))

    def create_row(self, command: CommandDescriptor):
        row = Gtk.ListBoxRow()
        row.set_activatable(True)
        row.set_selectable(True)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)

        title = Gtk.Label(label=_(command.title))
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(title)

        category = Gtk.Label(label=_(command.category))
        category.add_css_class('dim-label')
        category.set_halign(Gtk.Align.END)
        box.append(category)
        row.set_child(box)
        return row

    def select_relative(self, offset: int):
        if not self.commands:
            return
        selected = self.listbox.get_selected_row()
        current = selected.get_index() if selected is not None else 0
        index = max(0, min(current + offset, len(self.commands) - 1))
        row = self.listbox.get_row_at_index(index)
        self.listbox.select_row(row)
        row.grab_focus()

    def activate_selected(self) -> bool:
        row = self.listbox.get_selected_row()
        if row is None:
            return False
        index = row.get_index()
        if not 0 <= index < len(self.commands):
            return False
        self.execute(self.commands[index])
        return True

    def execute(self, command: CommandDescriptor):
        # Re-check enablement at execution time: the active document or preview
        # may have changed while the dialog was open.
        if self.catalog.execute(command):
            self.close()
        else:
            self.refresh_results()

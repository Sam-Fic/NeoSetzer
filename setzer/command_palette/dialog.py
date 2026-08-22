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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''GTK4 command palette dialog.'''

from __future__ import annotations

import builtins

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk, Pango

from setzer.app.service_locator import ServiceLocator
from setzer.command_palette.catalog import (
    CommandCatalog,
    CommandDescriptor,
    CommandResultGroup,
    update_recent_command_ids,
)
from setzer.keyboard_shortcuts.shortcut_tooltips import get_action_label
from setzer.dialogs.helpers.dialog_viewgtk import DialogView


def _(message: str) -> str:
    '''Look up a runtime gettext translation with a test-safe fallback.'''

    return getattr(builtins, '_', lambda value: value)(message)


GROUP_TITLES = {
    'recent': _('Recent Commands'),
    'all': _('All Commands'),
    'available': _('Available Commands'),
    'unavailable': _('Unavailable in Current Context'),
}


class CommandPaletteDialog(DialogView):
    '''A reusable modal command palette backed by ``CommandCatalog``.'''

    def __init__(self, main_window, workspace):
        DialogView.__init__(self, main_window)
        self.main_window = main_window
        self.workspace = workspace
        self.catalog = CommandCatalog(workspace.actions)
        self.settings = ServiceLocator.get_settings()
        self.commands: list[CommandDescriptor] = []
        self.command_rows: list[Gtk.ListBoxRow] = []
        self._previous_focus = None
        self._settings_handler = None

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
        '''Refresh commands, observe shortcut changes, and focus search.'''

        self._previous_focus = self.main_window.get_focus()
        self._connect_settings_updates()
        self.search_entry.set_text('')
        self.refresh_results()
        Adw.Dialog.present(self, self.main_window)
        self.search_entry.grab_focus()

    def close(self):
        Adw.Dialog.close(self)

    def on_closed(self, dialog):
        self._disconnect_settings_updates()
        if self._previous_focus is not None:
            self._previous_focus.grab_focus()
        self._previous_focus = None

    def _connect_settings_updates(self):
        if self._settings_handler is None:
            self._settings_handler = self.settings.connect('settings_changed', self._on_settings_changed)

    def _disconnect_settings_updates(self):
        if self._settings_handler is not None:
            self.settings.disconnect(self._settings_handler)
            self._settings_handler = None

    def _on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'keyboard_shortcuts':
            self.refresh_results()

    def on_search_changed(self, entry):
        self.refresh_results()

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Down:
            self.select_relative(1)
            return True
        if keyval == Gdk.KEY_Up:
            self.select_relative(-1)
            return True
        if keyval == Gdk.KEY_Home:
            self.select_index(0)
            return True
        if keyval == Gdk.KEY_End:
            self.select_index(len(self.commands) - 1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return self.activate_selected()
        return False

    def on_activate(self, entry):
        self.activate_selected()

    def on_row_activated(self, listbox, row):
        command = getattr(row, 'command', None)
        if command is not None:
            self.execute(command)

    def clear_rows(self):
        child = self.listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.listbox.remove(child)
            child = next_child
        self.command_rows = []

    def get_recent_command_ids(self):
        recent = self.settings.get_value('app_command_palette', 'recent_commands')
        return recent if isinstance(recent, list) else []

    def record_recent_command(self, command: CommandDescriptor):
        recent = update_recent_command_ids(command.identifier, self.get_recent_command_ids())
        self.settings.set_value('app_command_palette', 'recent_commands', recent)

    def refresh_results(self):
        groups = self.catalog.search_groups(
            self.search_entry.get_text(), self.get_recent_command_ids())
        self.commands = []
        self.clear_rows()
        for group in groups:
            self.listbox.append(self.create_group_header(group))
            for command in group.commands:
                row = self.create_row(command, group.available)
                self.listbox.append(row)
                if group.available:
                    self.commands.append(command)
                    self.command_rows.append(row)
        has_results = bool(groups)
        self.listbox.set_visible(has_results)
        self.empty_status_page.set_visible(not has_results)
        if self.command_rows:
            self.listbox.select_row(self.command_rows[0])

    def create_group_header(self, group: CommandResultGroup):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        row.add_css_class('command-palette-group-header')
        label = Gtk.Label(label=GROUP_TITLES[group.identifier])
        label.add_css_class('heading')
        label.add_css_class('dim-label')
        label.set_halign(Gtk.Align.START)
        label.set_margin_top(10)
        label.set_margin_bottom(2)
        label.set_margin_start(12)
        label.set_margin_end(12)
        row.set_child(label)
        return row

    def create_row(self, command: CommandDescriptor, available: bool):
        row = Gtk.ListBoxRow()
        row.command = command if available else None
        row.set_activatable(available)
        row.set_selectable(available)
        if not available:
            row.set_sensitive(False)
            row.add_css_class('command-palette-unavailable')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(12)
        box.set_margin_end(12)

        primary = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title = Gtk.Label(label=_(command.title))
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        primary.append(title)

        shortcut = get_action_label(command.settings_shortcut_key)
        if shortcut:
            shortcut_label = Gtk.Label(label=shortcut)
            shortcut_label.add_css_class('dim-label')
            shortcut_label.set_halign(Gtk.Align.END)
            primary.append(shortcut_label)

        category = Gtk.Label(label=_(command.category))
        category.add_css_class('dim-label')
        category.set_halign(Gtk.Align.END)
        primary.append(category)
        box.append(primary)

        if not available:
            subtitle = Gtk.Label(label=_('Unavailable in the current document or view'))
            subtitle.add_css_class('dim-label')
            subtitle.set_halign(Gtk.Align.START)
            subtitle.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(subtitle)

        row.set_child(box)
        return row

    def select_relative(self, offset: int):
        if not self.commands:
            return
        selected = self.listbox.get_selected_row()
        try:
            current = self.command_rows.index(selected)
        except ValueError:
            current = 0
        self.select_index(max(0, min(current + offset, len(self.commands) - 1)))

    def select_index(self, index: int):
        if not self.commands or not 0 <= index < len(self.commands):
            return
        row = self.command_rows[index]
        self.listbox.select_row(row)
        row.grab_focus()

    def activate_selected(self) -> bool:
        row = self.listbox.get_selected_row()
        command = getattr(row, 'command', None) if row is not None else None
        if command is None:
            return False
        self.execute(command)
        return True

    def execute(self, command: CommandDescriptor):
        # Re-check enablement at execution time: the active document or preview
        # may have changed while the dialog was open.
        if self.catalog.execute(command):
            self.record_recent_command(command)
            self.close()
        else:
            self.refresh_results()

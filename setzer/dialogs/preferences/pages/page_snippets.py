# coding: utf-8
#
# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Preferences UI for user-defined LaTeX snippets.'''

from __future__ import annotations

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.app.service_locator import ServiceLocator
from setzer.snippets.user_snippets import UserSnippet, UserSnippetStore, SnippetStoreError


class PageSnippets:
    '''Controller for the user snippet preference page.'''

    def __init__(self, preferences, settings):
        self.preferences = preferences
        self.settings = settings
        self.store = UserSnippetStore(ServiceLocator.get_config_folder())
        self.view = SnippetsPageView()
        self._editor = None
        self._delete_identifier = None

    def init(self):
        self.view.add_button.connect('clicked', self.on_add_clicked)
        self.refresh()

    def refresh(self):
        try:
            snippets = self.store.list_snippets()
            self.view.set_error(None)
        except SnippetStoreError as error:
            snippets = []
            self.view.set_error(str(error))
        self.view.populate(snippets, self.on_edit_clicked, self.on_delete_clicked)

    def on_add_clicked(self, button):
        self._open_editor(None)

    def on_edit_clicked(self, button, snippet):
        self._open_editor(snippet)

    def _open_editor(self, snippet):
        editor = SnippetEditorDialog(snippet, self.on_editor_save)
        self._editor = editor
        editor.present(self.preferences.view)

    def on_editor_save(self, editor, snippet, name, trigger, body):
        try:
            if snippet is None:
                self.store.create(name, trigger, body)
            else:
                self.store.update(snippet.identifier, name, trigger, body)
        except SnippetStoreError as error:
            editor.set_error(str(error))
            return
        editor.close()
        self.refresh()

    def on_delete_clicked(self, button, snippet):
        dialog = Adw.AlertDialog(
            heading=_('Delete snippet?'),
            body=_('The snippet “{}” will be permanently removed.').format(snippet.name))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('delete', _('Delete'))
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        self._delete_identifier = snippet.identifier
        dialog.choose(self.preferences.view, None, self._on_delete_response)

    def _on_delete_response(self, dialog, result):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response != 'delete':
            return
        try:
            self.store.delete(self._delete_identifier)
        except SnippetStoreError as error:
            self.view.set_error(str(error))
        self.refresh()


class SnippetsPageView(Adw.PreferencesPage):
    '''List-style preferences page showing the current user snippet library.'''

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Snippets'))
        self.set_icon_name('insert-text-symbolic')

        self.group = Adw.PreferencesGroup()
        self.group.set_title(_('LaTeX Snippets'))
        self.group.set_description(_(
            'Create reusable LaTeX text and expand it from the editor completion list.'))
        self.add(self.group)

        self.add_button = Gtk.Button.new_from_icon_name('list-add-symbolic')
        self.add_button.set_tooltip_text(_('Add snippet'))
        self.add_button.set_valign(Gtk.Align.CENTER)
        self.group.set_header_suffix(self.add_button)

        self.error_row = Adw.ActionRow()
        self.error_row.add_css_class('error')
        self.error_row.set_visible(False)
        self.group.add(self.error_row)

        self.empty_row = Adw.ActionRow()
        self.empty_row.set_title(_('No snippets yet'))
        self.empty_row.set_subtitle(_(
            'Add a snippet to expand a custom LaTeX command while writing.'))
        self.empty_row.set_activatable(False)
        self.group.add(self.empty_row)
        self.rows = []

    def set_error(self, message):
        self.error_row.set_visible(bool(message))
        self.error_row.set_title(message or '')

    def populate(self, snippets, on_edit, on_delete):
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        self.empty_row.set_visible(not snippets)

        for snippet in snippets:
            row = Adw.ActionRow()
            row.set_title(snippet.name)
            row.set_subtitle('{}  ·  {}'.format(snippet.trigger, self._body_summary(snippet.body)))
            row.set_activatable(True)
            row.connect('activated', lambda _row, value=snippet: on_edit(None, value))

            delete_button = Gtk.Button.new_from_icon_name('user-trash-symbolic')
            delete_button.set_tooltip_text(_('Delete snippet'))
            delete_button.set_valign(Gtk.Align.CENTER)
            delete_button.add_css_class('flat')
            delete_button.connect('clicked', on_delete, snippet)
            row.add_suffix(delete_button)
            self.group.add(row)
            self.rows.append(row)

    @staticmethod
    def _body_summary(body):
        summary = ' '.join(body.strip().split())
        return summary[:72] + ('…' if len(summary) > 72 else '')


class SnippetEditorDialog(Adw.Dialog):
    '''Modal form used for both creating and editing one snippet.'''

    def __init__(self, snippet: UserSnippet | None, on_save):
        Adw.Dialog.__init__(self)
        self.snippet = snippet
        self.on_save = on_save
        self.set_title(_('Edit Snippet') if snippet else _('Add Snippet'))
        self.set_content_width(520)
        self.set_content_height(460)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        toolbar.set_content(content)

        group = Adw.PreferencesGroup()
        content.append(group)

        self.name_row = Adw.EntryRow()
        self.name_row.set_title(_('Name'))
        self.name_row.set_text(snippet.name if snippet else '')
        group.add(self.name_row)

        self.trigger_row = Adw.EntryRow()
        self.trigger_row.set_title(_('Trigger'))
        self.trigger_row.set_show_apply_button(False)
        self.trigger_row.set_text(snippet.trigger if snippet else '\\')
        group.add(self.trigger_row)

        body_label = Gtk.Label(label=_('Snippet Body'))
        body_label.set_halign(Gtk.Align.START)
        body_label.add_css_class('heading')
        content.append(body_label)

        self.body_buffer = Gtk.TextBuffer()
        self.body_buffer.set_text(snippet.body if snippet else '')
        body_view = Gtk.TextView(buffer=self.body_buffer)
        body_view.set_monospace(True)
        body_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        body_view.set_top_margin(8)
        body_view.set_bottom_margin(8)
        body_view.set_left_margin(10)
        body_view.set_right_margin(10)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(160)
        scrolled.set_child(body_view)
        content.append(scrolled)

        hint = Gtk.Label(label=_('Use • to place the cursor after inserting the snippet.'))
        hint.set_halign(Gtk.Align.START)
        hint.add_css_class('dim-label')
        hint.add_css_class('caption')
        hint.set_wrap(True)
        content.append(hint)

        self.error_label = Gtk.Label()
        self.error_label.set_halign(Gtk.Align.START)
        self.error_label.set_wrap(True)
        self.error_label.add_css_class('error')
        self.error_label.set_visible(False)
        content.append(self.error_label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label=_('Cancel'))
        cancel.connect('clicked', lambda _button: self.close())
        self.save_button = Gtk.Button(label=_('Save'))
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self._on_save_clicked)
        buttons.append(cancel)
        buttons.append(self.save_button)
        content.append(buttons)

    def _on_save_clicked(self, button):
        start, end = self.body_buffer.get_bounds()
        self.on_save(
            self, self.snippet, self.name_row.get_text(), self.trigger_row.get_text(),
            self.body_buffer.get_text(start, end, False))

    def set_error(self, message):
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

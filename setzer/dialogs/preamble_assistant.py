#!/usr/bin/env python3
# coding: utf-8

'''Review-first UI for package recommendations in a document preamble.'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.app.latex_db import LaTeXDB
from setzer.project.preamble_assistant import PreambleAssistant


class PreambleAssistantDialog:

    def __init__(self, main_window):
        self.main_window = main_window
        self.document = None
        self.suggestions = ()
        self._build_view()

    def present(self, document):
        self.document = document
        packages_detailed = getattr(document.parser, 'symbols', {}).get(
            'packages_detailed', {})
        self.suggestions = PreambleAssistant.suggest(
            document.get_all_text(), packages_detailed,
            LaTeXDB.get_packages_dict())
        self._render_suggestions()
        self.dialog.present(self.main_window)

    def _build_view(self):
        self.dialog = Adw.Dialog()
        self.dialog.set_title(_('Preamble Assistant'))
        self.dialog.set_content_width(620)
        self.dialog.set_content_height(420)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)

        self.heading = Adw.WindowTitle(
            title=_('Preamble Assistant'),
            subtitle=_('Suggestions are based on commands in the document; nothing changes until you confirm.'))
        content.append(self.heading)
        self.buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(view)
        content.append(scroll)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        close = Gtk.Button.new_with_mnemonic(_('_Close'))
        close.connect('clicked', lambda *_: self.dialog.close())
        self.add_button = Gtk.Button.new_with_mnemonic(_('_Add suggested packages'))
        self.add_button.add_css_class('suggested-action')
        self.add_button.connect('clicked', self._on_add)
        buttons.append(close)
        buttons.append(self.add_button)
        content.append(buttons)
        self.dialog.set_child(content)

    def _render_suggestions(self):
        if not self.suggestions:
            self.buffer.set_text(
                _('No missing package suggestions were found in this document.'))
            self.add_button.set_sensitive(False)
            return
        lines = []
        for suggestion in self.suggestions:
            availability = _('available in NeoSetzer’s package database') \
                if suggestion.available_in_database else \
                _('not listed in NeoSetzer’s package database')
            lines.append('{insertion}\n{reason} ({availability})'.format(
                insertion=suggestion.insertion, reason=suggestion.reason,
                availability=availability))
        self.buffer.set_text('\n\n'.join(lines))
        self.add_button.set_sensitive(True)

    def _on_add(self, *_args):
        if not self.suggestions:
            return
        confirmation = Adw.AlertDialog.new(
            _('Add suggested packages?'),
            _('This inserts {count} package declaration(s) into the document preamble.').format(
                count=len(self.suggestions)))
        confirmation.add_response('cancel', _('_Cancel'))
        confirmation.add_response('add', _('_Add packages'))
        confirmation.set_response_appearance('add',
                                              Adw.ResponseAppearance.SUGGESTED)
        confirmation.set_default_response('cancel')
        confirmation.choose(self.dialog, None, self._on_confirm_add)

    def _on_confirm_add(self, dialog, result):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response != 'add':
            return
        self.document.add_packages([suggestion.package
                                    for suggestion in self.suggestions])
        self._show_toast(_('Added {count} suggested package(s).').format(
            count=len(self.suggestions)))
        self.dialog.close()

    def _show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        self.main_window.toast_overlay.add_toast(toast)

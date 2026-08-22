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
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.dialogs.document_wizard.pages.page import Page, PageView


class LetterSettingsPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = LetterSettingsPageView()

    def observe_view(self):
        def format_changed(combo, pspec):
            selected = combo.get_selected()
            if selected != Gtk.INVALID_LIST_POSITION:
                self.current_values['letter']['page_format'] = self.view.page_format_names[selected]

        def font_size_changed(row, pspec):
            self.current_values['letter']['font_size'] = int(row.get_value())

        def option_toggled(row, pspec, key):
            self.current_values['letter'][key] = row.get_active()

        def margin_changed(row, pspec, side):
            self.current_values['letter']['margin_' + side] = row.get_value()

        def text_changed(entry, field_name):
            self.current_values['letter'][field_name] = entry.get_text()

        def address_changed(buffer, field_name):
            start = buffer.get_start_iter()
            end = buffer.get_end_iter()
            self.current_values['letter'][field_name] = buffer.get_text(start, end, False)

        self.view.page_format_combo.connect('notify::selected', format_changed)
        self.view.font_size_entry.connect('notify::value', font_size_changed)
        self.view.option_twocolumn.connect('notify::active', option_toggled, 'option_twocolumn')
        self.view.option_landscape.connect('notify::active', option_toggled, 'is_landscape')
        self.view.option_default_margins.connect('notify::active', self.option_default_margins_toggled, 'default_margins')
        self.view.margins_button_left.connect('notify::value', margin_changed, 'left')
        self.view.margins_button_right.connect('notify::value', margin_changed, 'right')
        self.view.margins_button_top.connect('notify::value', margin_changed, 'top')
        self.view.margins_button_bottom.connect('notify::value', margin_changed, 'bottom')

        self.view.sender_name_entry.connect('changed', text_changed, 'sender_name')
        self.view.sender_address_buffer.connect('changed', address_changed, 'sender_address')
        self.view.sender_phone_entry.connect('changed', text_changed, 'sender_phone')
        self.view.recipient_name_entry.connect('changed', text_changed, 'recipient_name')
        self.view.recipient_address_buffer.connect('changed', address_changed, 'recipient_address')
        self.view.recipient_phone_entry.connect('changed', text_changed, 'recipient_phone')
        self.view.signature_entry.connect('changed', text_changed, 'signature')
        self.view.option_window_address.connect('notify::active', option_toggled, 'option_window_address')
        self.view.option_backaddress.connect('notify::active', option_toggled, 'option_backaddress')
        self.view.option_foldmarks.connect('notify::active', option_toggled, 'option_foldmarks')
        self.view.opening_entry.connect('changed', text_changed, 'opening')
        self.view.closing_entry.connect('changed', text_changed, 'closing')

    def option_default_margins_toggled(self, row, pspec=None, option_name=None):
        for spinrow in [self.view.margins_button_left, self.view.margins_button_right, self.view.margins_button_top, self.view.margins_button_bottom]:
            spinrow.set_sensitive(not row.get_active())
            if row.get_active():
                spinrow.set_value(3.5)
        if option_name != None:
            self.current_values['letter']['option_' + option_name] = row.get_active()

    def load_presets(self, presets):
        for setter_function, value_name in [
            (self.view.font_size_entry.set_value, 'font_size'),
            (self.view.option_twocolumn.set_active, 'option_twocolumn'),
            (self.view.option_landscape.set_active, 'is_landscape'),
            (self.view.margins_button_left.set_value, 'margin_left'),
            (self.view.margins_button_right.set_value, 'margin_right'),
            (self.view.margins_button_top.set_value, 'margin_top'),
            (self.view.margins_button_bottom.set_value, 'margin_bottom'),
            (self.view.option_default_margins.set_active, 'option_default_margins'),
            (self.view.option_window_address.set_active, 'option_window_address'),
            (self.view.option_backaddress.set_active, 'option_backaddress'),
            (self.view.option_foldmarks.set_active, 'option_foldmarks'),
        ]:
            try:
                value = presets['letter'][value_name]
            except (TypeError, KeyError):
                value = self.current_values['letter'][value_name]
            setter_function(value)

        try:
            value = presets['letter']['page_format']
        except Exception:
            value = self.current_values['letter']['page_format']
        self.view.page_format_combo.set_selected(self.view.page_format_names.index(value))

        # 信件专用字段
        for field_name in ['sender_name', 'sender_address', 'sender_phone',
                           'recipient_name', 'recipient_address', 'recipient_phone',
                           'signature', 'opening', 'closing']:
            try:
                text = presets['letter'][field_name]
            except (TypeError, KeyError):
                text = self.current_values['letter'].get(field_name, '')
            if field_name in ('sender_address', 'recipient_address'):
                getattr(self.view, field_name + '_buffer').set_text(text)
            else:
                getattr(self.view, field_name + '_entry').set_text(text)

        self.option_default_margins_toggled(self.view.option_default_margins)
        self._update_scrlttr2_options_visibility()

    def _update_scrlttr2_options_visibility(self):
        self.view.group_envelope.set_visible(
            self.current_values.get('document_class') == 'scrlttr2')

    def on_activation(self):
        self._update_scrlttr2_options_visibility()


class LetterSettingsPageView(PageView):

    @staticmethod
    def _make_address_row(title, tooltip):
        '''Create an accessible multiline postal-address editor inside a row.'''
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(tooltip)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_width(240)
        scrolled.set_min_content_height(76)
        scrolled.set_vexpand(True)
        buffer = Gtk.TextBuffer()
        text_view = Gtk.TextView(buffer=buffer)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_top_margin(6)
        text_view.set_bottom_margin(6)
        text_view.set_left_margin(8)
        text_view.set_right_margin(8)
        text_view.set_tooltip_text(tooltip)
        scrolled.set_child(text_view)
        row.add_suffix(scrolled)
        return row, buffer

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 2: ' + _('Letter settings')

        self.set_document_settings_page()

        # ---- Sender information ----
        self.group_sender = Adw.PreferencesGroup()
        self.group_sender.set_title(_('Sender'))
        self.sender_name_entry = Adw.EntryRow()
        self.sender_name_entry.set_title(_('Name'))
        self.sender_name_entry.set_tooltip_text(_('Your name as it appears in the address block.'))
        self.sender_address_row, self.sender_address_buffer = self._make_address_row(
            _('Address'), _('Your street address or PO box. Use a new line for each address line.'))
        self.sender_phone_entry = Adw.EntryRow()
        self.sender_phone_entry.set_title(_('Phone'))
        self.sender_phone_entry.set_tooltip_text(_('Your phone number.'))
        self.group_sender.add(self.sender_name_entry)
        self.group_sender.add(self.sender_address_row)
        self.group_sender.add(self.sender_phone_entry)

        # ---- Recipient information ----
        self.group_recipient = Adw.PreferencesGroup()
        self.group_recipient.set_title(_('Recipient'))
        self.recipient_name_entry = Adw.EntryRow()
        self.recipient_name_entry.set_title(_('Name'))
        self.recipient_name_entry.set_tooltip_text(_('The name of the person or organization you are writing to.'))
        self.recipient_address_row, self.recipient_address_buffer = self._make_address_row(
            _('Address'), _('The recipient\'s street address or PO box. Use a new line for each address line.'))
        self.recipient_phone_entry = Adw.EntryRow()
        self.recipient_phone_entry.set_title(_('Phone'))
        self.recipient_phone_entry.set_tooltip_text(_('The recipient\'s phone number.'))
        self.group_recipient.add(self.recipient_name_entry)
        self.group_recipient.add(self.recipient_address_row)
        self.group_recipient.add(self.recipient_phone_entry)

        # ---- Letter content ----
        self.group_content = Adw.PreferencesGroup()
        self.group_content.set_title(_('Letter content'))
        self.signature_entry = Adw.EntryRow()
        self.signature_entry.set_title(_('Signature'))
        self.signature_entry.set_tooltip_text(_('Your signature as it appears at the end of the letter.'))
        self.opening_entry = Adw.EntryRow()
        self.opening_entry.set_title(_('Opening'))
        self.opening_entry.set_tooltip_text(_('The salutation, e.g. "Dear Sir or Madam,"'))
        self.closing_entry = Adw.EntryRow()
        self.closing_entry.set_title(_('Closing'))
        self.closing_entry.set_tooltip_text(_('The closing phrase, e.g. "Yours sincerely,"'))
        self.group_content.add(self.signature_entry)
        self.group_content.add(self.opening_entry)
        self.group_content.add(self.closing_entry)

        # ---- scrlttr2 envelope controls ----
        self.group_envelope = Adw.PreferencesGroup()
        self.group_envelope.set_title(_('Envelope and folding'))
        self.option_window_address = Adw.SwitchRow()
        self.option_window_address.set_title(_('Show window-envelope address field'))
        self.option_window_address.set_tooltip_text(_(
            'Show the recipient address in the position for a windowed envelope.'))
        self.option_backaddress = Adw.SwitchRow()
        self.option_backaddress.set_title(_('Show return address'))
        self.option_backaddress.set_tooltip_text(_(
            'Show the sender address above the recipient address field.'))
        self.option_foldmarks = Adw.SwitchRow()
        self.option_foldmarks.set_title(_('Show fold marks'))
        self.option_foldmarks.set_tooltip_text(_(
            'Show marks that help fold the letter for a windowed envelope.'))
        self.group_envelope.add(self.option_window_address)
        self.group_envelope.add(self.option_backaddress)
        self.group_envelope.add(self.option_foldmarks)

        # ---- Page format ----
        self.group_page_format = Adw.PreferencesGroup()
        self.group_page_format.set_title(_('Page format'))
        self.group_page_format.add(self.page_format_combo)

        self.group_options = Adw.PreferencesGroup()
        self.group_options.set_title(_('Options'))
        self.group_options.add(self.option_landscape)
        self.group_options.add(self.option_twocolumn)

        self.group_font_size = Adw.PreferencesGroup()
        self.group_font_size.set_title(_('Font size'))
        self.group_font_size.add(self.font_size_entry)

        self.group_margins = Adw.PreferencesGroup()
        self.group_margins.set_title(_('Page margins'))
        self.group_margins.set_description(_('All values are in cm (1 inch ≅ 2.54 cm).'))
        self.group_margins.add(self.option_default_margins)
        self.group_margins.add(self.margins_button_left)
        self.group_margins.add(self.margins_button_right)
        self.group_margins.add(self.margins_button_top)
        self.group_margins.add(self.margins_button_bottom)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.append(self.group_sender)
        self.content.append(self.group_recipient)
        self.content.append(self.group_content)
        self.content.append(self.group_envelope)
        self.content.append(self.group_page_format)
        self.content.append(self.group_options)
        self.content.append(self.group_font_size)
        self.content.append(self.group_margins)

        self.append(self.wrap_content(self.content))
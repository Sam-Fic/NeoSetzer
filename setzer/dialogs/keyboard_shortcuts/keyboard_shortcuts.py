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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

import html

from setzer.app.service_locator import ServiceLocator


class KeyboardShortcutsDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        
        self.data = self.build_shortcuts_data()

    def build_shortcuts_data(self):
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']
        
        data = list()

        section = {'title': _('Documents'), 'items': list()}
        section['items'].append({'title': _('Create new document'), 'shortcut': shortcuts.get('new_document', '<ctrl>N')})
        section['items'].append({'title': _('Open a document'), 'shortcut': shortcuts.get('open_document', '<ctrl>O')})
        section['items'].append({'title': _('Show recent documents'), 'shortcut': shortcuts.get('show_document_chooser', '<ctrl><shift>O')})
        section['items'].append({'title': _('Show open documents'), 'shortcut': shortcuts.get('show_open_docs', '<ctrl>T')})
        section['items'].append({'title': _('Switch to the next open document'), 'shortcut': shortcuts.get('switch_document', '<ctrl>Tab')})
        section['items'].append({'title': _('Save the current document'), 'shortcut': shortcuts.get('save', '<ctrl>S')})
        section['items'].append({'title': _('Save the document with a new filename'), 'shortcut': shortcuts.get('save_as', '<ctrl><shift>S')})
        section['items'].append({'title': _('Close the current document'), 'shortcut': shortcuts.get('close_document', '<ctrl>W')})
        data.append(section)

        section = {'title': _('Tools'), 'items': list()}
        section['items'].append({'title': _('Save and build .pdf-file from document'), 'shortcut': shortcuts.get('save_and_build', 'F5')})
        section['items'].append({'title': _('Build PDF without saving'), 'shortcut': shortcuts.get('build', 'F6')})
        section['items'].append({'title': _('Show current position in preview'), 'shortcut': shortcuts.get('forward_sync', 'F7')})
        data.append(section)

        section = {'title': _('Windows and Panels'), 'items': list()}
        section['items'].append({'title': _('Show help panel'), 'shortcut': shortcuts.get('help', 'F1')})
        section['items'].append({'title': _('Toggle document structure panel'), 'shortcut': shortcuts.get('document_structure', '<ctrl><shift>B')})
        section['items'].append({'title': _('Toggle symbols panel'), 'shortcut': shortcuts.get('symbols', '<ctrl><shift>S')})
        section['items'].append({'title': _('Toggle build log'), 'shortcut': shortcuts.get('build_log', '<ctrl><shift>L')})
        section['items'].append({'title': _('Toggle preview panel'), 'shortcut': shortcuts.get('preview', '<ctrl><shift>P')})
        section['items'].append({'title': _('Show global menu'), 'shortcut': shortcuts.get('hamburger_menu', 'F10')})
        section['items'].append({'title': _('Show context menu'), 'shortcut': shortcuts.get('context_menu', 'F12')})
        section['items'].append({'title': _('Show keyboard shortcuts'), 'shortcut': shortcuts.get('show_shortcuts', '<ctrl>question')})
        section['items'].append({'title': _('Close Application'), 'shortcut': shortcuts.get('quit', '<ctrl>Q')})
        data.append(section)

        section = {'title': _('Find and Replace'), 'items': list()}
        section['items'].append({'title': _('Find'), 'shortcut': shortcuts.get('find', '<ctrl>F')})
        section['items'].append({'title': _('Find the next match'), 'shortcut': shortcuts.get('find_next', '<ctrl>G')})
        section['items'].append({'title': _('Find the previous match'), 'shortcut': shortcuts.get('find_previous', '<ctrl><shift>G')})
        section['items'].append({'title': _('Find and Replace'), 'shortcut': shortcuts.get('find_and_replace', '<ctrl>H')})
        data.append(section)

        section = {'title': _('Zoom'), 'items': list()}
        section['items'].append({'title': _('Zoom in'), 'shortcut': shortcuts.get('zoom_in', '<ctrl>plus')})
        section['items'].append({'title': _('Zoom out'), 'shortcut': shortcuts.get('zoom_out', '<ctrl>minus')})
        section['items'].append({'title': _('Reset zoom'), 'shortcut': shortcuts.get('reset_zoom', '<ctrl>0')})
        data.append(section)

        section = {'title': _('Copy and Paste'), 'items': list()}
        section['items'].append({'title': _('Copy selected text to clipboard'), 'shortcut': shortcuts.get('copy', '<ctrl>C')})
        section['items'].append({'title': _('Cut selected text to clipboard'), 'shortcut': shortcuts.get('cut', '<ctrl>X')})
        section['items'].append({'title': _('Paste text from clipboard'), 'shortcut': shortcuts.get('paste', '<ctrl>V')})
        data.append(section)

        section = {'title': _('Undo and Redo'), 'items': list()}
        section['items'].append({'title': _('Undo previous text edit'), 'shortcut': shortcuts.get('undo', '<ctrl>Z')})
        section['items'].append({'title': _('Redo previous text edit'), 'shortcut': shortcuts.get('redo', '<ctrl><shift>Z')})
        data.append(section)

        section = {'title': _('Selection'), 'items': list()}
        section['items'].append({'title': _('Select all text'), 'shortcut': shortcuts.get('select_all', '<ctrl>A')})
        data.append(section)

        section = {'title': _('Editing'), 'items': list()}
        section['items'].append({'title': _('Toggle insert / overwrite'), 'shortcut': 'Insert'})
        section['items'].append({'title': _('Move current line up'), 'shortcut': '<Alt>Up'})
        section['items'].append({'title': _('Move current line down'), 'shortcut': '<Alt>Down'})
        section['items'].append({'title': _('Move current word left'), 'shortcut': '<Alt>Left'})
        section['items'].append({'title': _('Move current word right'), 'shortcut': '<Alt>Right'})
        section['items'].append({'title': _('Increment number at cursor'), 'shortcut': '<ctrl><shift>A'})
        section['items'].append({'title': _('Decrement number at cursor'), 'shortcut': '<ctrl><shift>X'})
        section['items'].append({'title': _('Delete current line'), 'shortcut': shortcuts.get('delete_line', '<ctrl><shift>K')})
        data.append(section)

        section = {'title': _('LaTeX Shortcuts'), 'items': list()}
        section['items'].append({'title': _('Comment / Uncomment current line(s)'), 'shortcut': shortcuts.get('toggle_comment', '<ctrl>k')})
        section['items'].append({'title': _('New Line') + ' (\\\\)', 'shortcut': shortcuts.get('new_line', '<ctrl>Return')})
        section['items'].append({'title': _('Bold Text'), 'shortcut': shortcuts.get('bold', '<ctrl>B')})
        section['items'].append({'title': _('Italic Text'), 'shortcut': shortcuts.get('italic', '<ctrl>I')})
        section['items'].append({'title': _('Underlined Text'), 'shortcut': shortcuts.get('underline', '<ctrl>U')})
        section['items'].append({'title': _('Typewriter Text'), 'shortcut': shortcuts.get('typewriter', '<ctrl><shift>T')})
        section['items'].append({'title': _('Emphasized Text'), 'shortcut': shortcuts.get('emphasized', '<ctrl><shift>E')})
        section['items'].append({'title': _('Quotation Marks'), 'shortcut': shortcuts.get('quotation_marks', '<ctrl>quotedbl')})
        section['items'].append({'title': _('List Item'), 'shortcut': shortcuts.get('list_item', '<ctrl><shift>I')})
        section['items'].append({'title': _('Environment'), 'shortcut': shortcuts.get('environment', '<ctrl>E')})
        data.append(section)

        section = {'title': _('Math Shortcuts'), 'items': list()}
        section['items'].append({'title': _('Inline Math Section'), 'shortcut': shortcuts.get('inline_math', '<ctrl>M')})
        section['items'].append({'title': _('Display Math Section'), 'shortcut': shortcuts.get('display_math', '<ctrl><shift>M')})
        section['items'].append({'title': _('Equation'), 'shortcut': shortcuts.get('equation', '<ctrl><shift>N')})
        section['items'].append({'title': _('Subscript'), 'shortcut': shortcuts.get('subscript', '<ctrl><shift>D')})
        section['items'].append({'title': _('Superscript'), 'shortcut': shortcuts.get('superscript', '<ctrl><shift>U')})
        section['items'].append({'title': _('Fraction'), 'shortcut': shortcuts.get('fraction', '<alt><shift>F')})
        section['items'].append({'title': '\\left', 'shortcut': shortcuts.get('left', '<ctrl><shift>L')})
        section['items'].append({'title': '\\right', 'shortcut': shortcuts.get('right', '<ctrl><shift>R')})
        data.append(section)

        return data

    def run(self):
        self.data = self.build_shortcuts_data()
        self.setup()
        self.view.present(self.main_window)

    def setup(self):
        builder_string = '''<?xml version="1.0" encoding="UTF-8"?>
<interface>

  <object class="AdwShortcutsDialog" id="shortcuts-dialog">
'''

        for section in self.data:
            builder_string += '''    <child>
      <object class="AdwShortcutsSection">
        <property name="title" translatable="no">''' + html.escape(section['title']) + '''</property>
'''

            for item in section['items']:
                builder_string += '''        <child>
          <object class="AdwShortcutsItem">
            <property name="title" translatable="no">''' + html.escape(item['title']) + '''</property>
            <property name="accelerator">''' + html.escape(item['shortcut']) + '''</property>
          </object>
        </child>
'''

            builder_string += '''      </object>
    </child>
'''

        builder_string += '''  </object>

</interface>'''

        builder = Gtk.Builder.new_from_string(builder_string, -1)
        self.view = builder.get_object('shortcuts-dialog')

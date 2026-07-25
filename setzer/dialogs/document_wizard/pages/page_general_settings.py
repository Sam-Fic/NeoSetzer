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
from gi.repository import Gtk
from gi.repository import Adw
from gi.repository import GLib

from setzer.dialogs.document_wizard.pages.page import Page, PageView
from setzer.app.service_locator import ServiceLocator

import os
import re


class GeneralSettingsPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = GeneralSettingsPageView()

    def observe_view(self):
        def text_changed(entry, field_name):
            self.current_values[field_name] = entry.get_text()

        def language_changed(combo, pspec):
            selected = combo.get_selected()
            if selected != Gtk.INVALID_LIST_POSITION:
                code = self.view.language_codes[selected]
                self.update_languages_list(code)

        def font_package_changed(combo, pspec):
            # Problem 5: 用户在 ComboRow 选择字体包, 反查为字符串键
            # 存入 current_values['font_package'], 供 document_wizard
            # 的 _get_font_package_line() 生成对应 \usepackage 行。
            selected = combo.get_selected()
            if selected != Gtk.INVALID_LIST_POSITION:
                self.current_values['font_package'] = self.view.font_package_codes[selected]

        def option_toggled(row, pspec, package_name):
            self.current_values['packages'][package_name] = row.get_active()

        self.view.title_entry.connect('changed', text_changed, 'title')
        self.view.author_entry.connect('changed', text_changed, 'author')
        self.view.date_entry.connect('changed', text_changed, 'date')

        self.view.language_combo.connect('notify::selected', language_changed)
        self.view.font_package_combo.connect('notify::selected', font_package_changed)

        for name, row in self.view.option_packages.items():
            row.connect('notify::active', option_toggled, name)

    def load_presets(self, presets):
        try:
            text = presets['author']
        except TypeError:
            text = self.current_values['author']
        self.view.author_entry.set_text(text)
        self.view.title_entry.set_text('')
        self.view.date_entry.set_text('\\today')

        try:
            langs = presets['languages']
        except (TypeError, KeyError):
            langs = self.current_values['languages']
        self.current_values['languages'] = langs
        self.add_languages_list(langs)

        # Problem 5: 恢复字体包选择。presets 是旧版数据时没有 'font_package'
        # 键——KeyError 时回退到 current_values 默认值('lmodern')。
        # 未知值(如 presets 被篡改)同样回退到 lmodern, 保持向后兼容。
        try:
            font_package = presets['font_package']
        except (TypeError, KeyError):
            font_package = self.current_values['font_package']
        if font_package not in self.view.font_package_codes:
            font_package = 'lmodern'
        self.current_values['font_package'] = font_package
        self.view.font_package_combo.set_selected(
            self.view.font_package_codes.index(font_package))

        for name, option in self.view.option_packages.items():
            try:
                is_active = presets['packages'][name]
            except (TypeError, KeyError):
                is_active = self.current_values['packages'][name]
            option.set_active(is_active)

    def on_activation(self):
        self.view.title_entry.grab_focus()

    def add_languages_list(self, langs):
        model = Gtk.StringList()
        self.view.language_codes = list(langs.keys())
        for code in self.view.language_codes:
            model.append('{} ({})'.format(langs[code], code))
        self.view.language_combo.set_model(model)
        self.view.language_combo.set_selected(0)

    def update_languages_list(self, lang):
        dictionary = self.current_values['languages']

        if lang in dictionary and next(iter(dictionary)) != lang:
            value = dictionary.pop(lang)
            dictionary = {lang: value, **dictionary}

            self.current_values['languages'] = dictionary
            self.add_languages_list(dictionary)


class GeneralSettingsPageView(PageView):

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 3: ' + _('General document settings')

        # Package descriptions (instance-level so gettext _() is resolved at
        # runtime, after gettext.install has run).
        self.package_descriptions = {
            'ams': _('<b>AMS packages:</b> provide mathematical symbols, math-related environments, …') + ' (' + _('recommended') + ')',
            'textcomp': '<b>textcomp:</b> ' + _('contains symbols to be used in textmode.') + ' (' + _('recommended') + ')',
            'graphicx': '<b>graphicx:</b> ' + _('include graphics in your document.') + ' (' + _('recommended') + ')',
            'color': '<b>color:</b> ' + _('foreground and background color.') + ' (' + _('recommended') + ')',
            'xcolor': '<b>xcolor:</b> ' + _('enables colored text.') + ' (' + _('recommended') + ')',
            'url': '<b>url:</b> ' + _('type urls with the \\url{..} command without escaping them.') + ' (' + _('recommended') + ')',
            'hyperref': '<b>hyperref:</b> ' + _('create hyperlinks within your document.'),
            'theorem': '<b>theorem:</b> ' + _('define theorem environments (like "definition", "lemma", …) with custom styling.'),
            'listings': '<b>listings:</b> ' + _('provides the \\listing environment for embedding programming code.'),
            'glossaries': '<b>glossaries:</b> ' + _('create a glossary for your document.'),
            'parskip': '<b>parskip:</b> ' + _('paragraphs without indentation.'),
        }

        # Document properties ------------------------------------------------
        self.group_document_properties = Adw.PreferencesGroup()
        self.group_document_properties.set_title(_('Document properties'))

        self.title_entry = Adw.EntryRow()
        self.title_entry.set_title(_('Title'))
        self.author_entry = Adw.EntryRow()
        self.author_entry.set_title(_('Author'))
        self.date_entry = Adw.EntryRow()
        self.date_entry.set_title(_('Date'))
        self.group_document_properties.add(self.title_entry)
        self.group_document_properties.add(self.author_entry)
        self.group_document_properties.add(self.date_entry)

        # Language -----------------------------------------------------------
        self.group_language = Adw.PreferencesGroup()
        self.group_language.set_title(_('Language'))
        self.group_language.set_description(_('The main language for this document. This is used to apply rules for hyphenation and other purposes.'))
        self.language_combo = Adw.ComboRow()
        self.language_combo.set_title(_('Language'))
        self.language_combo.set_model(Gtk.StringList())
        self.language_codes = list()
        self.group_language.add(self.language_combo)

        # Font package (Problem 5) -------------------------------------------
        # 让用户选择字体包, 而非总是插入 \usepackage{lmodern}。
        #   lmodern  : Latin Modern, pdfLaTeX 推荐(默认, 与原行为一致)
        #   fontspec : XeLaTeX/LuaLaTeX 下用系统字体
        #   none     : 不插字体包, 用户自行处理
        # font_package_codes 与下方 StringList 顺序一一对应; 选中索引通过
        # font_package_codes[index] 反查为存储到 current_values['font_package']
        # 的字符串键。这与 language_codes 的索引↔键映射模式对称。
        self.group_font = Adw.PreferencesGroup()
        self.group_font.set_title(_('Font package'))
        self.group_font.set_description(_('Select the font package to include in the preamble. lmodern is recommended for pdfLaTeX, fontspec for XeLaTeX/LuaLaTeX.'))
        self.font_package_combo = Adw.ComboRow()
        self.font_package_combo.set_title(_('Font package'))
        font_package_model = Gtk.StringList()
        self.font_package_codes = ['lmodern', 'fontspec', 'none']
        font_package_labels = {
            'lmodern': _('Latin Modern (lmodern)') + ' (' + _('recommended') + ')',
            'fontspec': _('Fontspec (for XeLaTeX/LuaLaTeX)'),
            'none': _('None'),
        }
        for code in self.font_package_codes:
            font_package_model.append(font_package_labels[code])
        self.font_package_combo.set_model(font_package_model)
        self.group_font.add(self.font_package_combo)

        # Packages -----------------------------------------------------------
        self.option_packages = dict()
        self.option_packages['ams'] = self._create_package_row(_('AMS math packages'), 'ams')
        self.option_packages['textcomp'] = self._create_package_row('textcomp', 'textcomp')
        self.option_packages['graphicx'] = self._create_package_row('graphicx', 'graphicx')
        self.option_packages['color'] = self._create_package_row('color', 'color')
        self.option_packages['xcolor'] = self._create_package_row('xcolor', 'xcolor')
        self.option_packages['url'] = self._create_package_row('url', 'url')
        self.option_packages['hyperref'] = self._create_package_row('hyperref', 'hyperref')
        self.option_packages['theorem'] = self._create_package_row('theorem', 'theorem')
        self.option_packages['listings'] = self._create_package_row('listings', 'listings')
        self.option_packages['glossaries'] = self._create_package_row('glossaries', 'glossaries')
        self.option_packages['parskip'] = self._create_package_row('parskip', 'parskip')

        self.group_packages = Adw.PreferencesGroup()
        self.group_packages.set_title(_('Packages'))
        for name in ['ams', 'textcomp', 'graphicx', 'color', 'xcolor', 'url', 'hyperref', 'theorem', 'listings', 'glossaries', 'parskip']:
            self.group_packages.add(self.option_packages[name])

        # Layout -------------------------------------------------------------
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.append(self.group_document_properties)
        self.content.append(self.group_language)
        self.content.append(self.group_font)
        self.content.append(self.group_packages)

        self.append(self.wrap_content(self.content))

    def _create_package_row(self, label, name):
        row = Adw.SwitchRow()
        row.set_title(label)
        row.set_tooltip_markup(self.package_descriptions[name])
        return row

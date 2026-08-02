#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
                font_package = self.view.font_package_codes[selected]
                self.current_values['font_package'] = font_package
                # 报告 #6：选 fontspec 时显示编译引擎提示。
                self.view.fontspec_note.set_visible(font_package == 'fontspec')

        def custom_packages_changed(entry, pspec):
            self.current_values['custom_packages'] = entry.get_text()

        def option_toggled(row, pspec, package_name):
            self.current_values['packages'][package_name] = row.get_active()

        self.view.title_entry.connect('changed', text_changed, 'title')
        self.view.author_entry.connect('changed', text_changed, 'author')
        self.view.date_entry.connect('changed', text_changed, 'date')

        self.view.language_combo.connect('notify::selected', language_changed)
        self.view.font_package_combo.connect('notify::selected', font_package_changed)
        self.view.custom_packages_entry.connect('notify::text', custom_packages_changed)

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

        # 报告 #2：恢复自定义包输入。
        try:
            custom = presets['custom_packages']
        except (TypeError, KeyError):
            custom = self.current_values.get('custom_packages', '')
        self.current_values['custom_packages'] = custom
        self.view.custom_packages_entry.set_text(custom)

        self._update_package_visibility()

    def on_activation(self):
        self.view.title_entry.grab_focus()
        self._update_package_visibility()

    def _update_package_visibility(self):
        '''根据文档类型显示/隐藏不相关的包选项。'''
        doc_class = self.current_values.get('document_class', 'article')
        # beamer 内置图形支持，无需 graphicx；letter 通常不需要 AMS
        skip_ams = doc_class in ('letter', 'scrlttr2')
        skip_graphicx = doc_class in ('beamer',)

        self.view.option_packages['ams'].set_visible(not skip_ams)
        self.view.option_packages['graphicx'].set_visible(not skip_graphicx)

        # 更新分组可见性
        for group_name, group in self.view._package_groups.items():
            any_visible = any(
                child.get_visible()
                for child in group
                if isinstance(child, Adw.SwitchRow)
            )
            group.set_visible(any_visible)

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
            'textcomp': '<b>textcomp:</b> ' + _('contains symbols to be used in textmode.'),
            'graphicx': '<b>graphicx:</b> ' + _('include graphics in your document.') + ' (' + _('recommended') + ')',
            'color': '<b>color:</b> ' + _('foreground and background color.'),
            'xcolor': '<b>xcolor:</b> ' + _('enables colored text.'),
            'url': '<b>url:</b> ' + _('type urls with the \\url{..} command without escaping them.'),
            'hyperref': '<b>hyperref:</b> ' + _('create hyperlinks within your document.'),
            'theorem': '<b>theorem:</b> ' + _('define theorem environments (like "definition", "lemma", …) with custom styling.'),
            'listings': '<b>listings:</b> ' + _('provides the \\listing environment for embedding programming code.'),
            'glossaries': '<b>glossaries:</b> ' + _('create a glossary for your document.'),
            'parskip': '<b>parskip:</b> ' + _('paragraphs without indentation.') + ' (' + _('recommended') + ')',
        }

        # Document properties ------------------------------------------------
        self.group_document_properties = Adw.PreferencesGroup()
        self.group_document_properties.set_title(_('Document properties'))

        self.title_entry = Adw.EntryRow()
        self.title_entry.set_title(_('Title'))
        self.title_entry.set_tooltip_text(_('The document title, used in the \\title{} command.'))
        self.author_entry = Adw.EntryRow()
        self.author_entry.set_title(_('Author'))
        self.author_entry.set_tooltip_text(_('The document author, used in the \\author{} command.'))
        self.date_entry = Adw.EntryRow()
        self.date_entry.set_title(_('Date'))
        self.date_entry.set_tooltip_text(_('The document date, used in the \\date{} command. '
                                            'Use \\\\today for the current date.'))
        self.group_document_properties.add(self.title_entry)
        self.group_document_properties.add(self.author_entry)
        self.group_document_properties.add(self.date_entry)

        # Language -----------------------------------------------------------
        self.group_language = Adw.PreferencesGroup()
        self.group_language.set_title(_('Language'))
        self.group_language.set_description(_('The main language for this document. This is used to apply rules for hyphenation and other purposes.'))
        # 使用原生 Adw.ComboRow，与 Font package 保持一致；babel 语言列表较
        # 长，在支持的 libadwaita 版本上启用弹窗内搜索。
        self.language_combo = Adw.ComboRow()
        self.language_combo.set_title(_('Language'))
        self.language_combo.set_tooltip_text(_('The main language for hyphenation patterns and automatic labels '
                                                '(e.g. "Abstract" vs "Zusammenfassung").'))
        self.language_combo.set_model(Gtk.StringList())
        if hasattr(self.language_combo, 'set_enable_search'):
            self.language_combo.set_enable_search(True)
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
        self.font_package_combo.set_tooltip_text(_(
            'Choose the font package. lmodern (Latin Modern) is recommended for pdfLaTeX. '
            'fontspec enables system fonts with XeLaTeX/LuaLaTeX.'))
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

        # fontspec 提示（报告 #6）：选 fontspec 时显示，提醒需 XeLaTeX/LuaLaTeX。
        self.fontspec_note = Gtk.Label()
        self.fontspec_note.set_wrap(True)
        self.fontspec_note.set_xalign(0)
        self.fontspec_note.set_text(_('fontspec requires XeLaTeX or LuaLaTeX to compile; pdfLaTeX will fail.'))
        self.fontspec_note.add_css_class('dim-label')
        self.fontspec_note.set_visible(False)
        self.group_font.add(self.fontspec_note)

        # Packages -----------------------------------------------------------
        # Category groups: each group is an Adw.PreferencesGroup with a title.
        # The group references are stored in _package_groups for search filtering.
        self._package_groups = dict()
        self.option_packages = dict()

        # -- Math --
        self.option_packages['ams'] = self._create_package_row(_('AMS math packages'), 'ams')
        self._package_groups['math'] = self._create_package_group(_('Math'), [self.option_packages['ams']])

        # -- Graphics --
        self.option_packages['graphicx'] = self._create_package_row('graphicx', 'graphicx')
        self._package_groups['graphics'] = self._create_package_group(_('Graphics'), [self.option_packages['graphicx']])

        # -- Text --
        self.option_packages['textcomp'] = self._create_package_row('textcomp', 'textcomp')
        self.option_packages['color'] = self._create_package_row('color', 'color')
        self.option_packages['xcolor'] = self._create_package_row('xcolor', 'xcolor')
        self._package_groups['text'] = self._create_package_group(_('Text'), [
            self.option_packages['textcomp'],
            self.option_packages['color'],
            self.option_packages['xcolor'],
        ])

        # -- References --
        self.option_packages['url'] = self._create_package_row('url', 'url')
        self.option_packages['hyperref'] = self._create_package_row('hyperref', 'hyperref')
        self.option_packages['glossaries'] = self._create_package_row('glossaries', 'glossaries')
        self._package_groups['references'] = self._create_package_group(_('References'), [
            self.option_packages['url'],
            self.option_packages['hyperref'],
            self.option_packages['glossaries'],
        ])

        # -- Layout --
        self.option_packages['parskip'] = self._create_package_row('parskip', 'parskip')
        self.option_packages['theorem'] = self._create_package_row('theorem', 'theorem')
        self.option_packages['listings'] = self._create_package_row('listings', 'listings')
        self._package_groups['layout'] = self._create_package_group(_('Layout'), [
            self.option_packages['parskip'],
            self.option_packages['theorem'],
            self.option_packages['listings'],
        ])

        # Packages 区：标题 + 搜索栏 + 分组列表。
        self.group_packages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.group_packages_title = Gtk.Label(label=_('Packages'))
        self.group_packages_title.add_css_class('title-4')
        self.group_packages_title.set_halign(Gtk.Align.START)
        self.group_packages.append(self.group_packages_title)

        self.packages_search_entry = Gtk.SearchEntry()
        self.packages_search_entry.set_placeholder_text(_('Search packages'))
        self.packages_search_entry.connect('search-changed', self.on_packages_search)
        self.group_packages.append(self.packages_search_entry)

        self.packages_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for group in self._package_groups.values():
            self.packages_box.append(group)

        # 自定义包输入（报告 #2）：逗号分隔的包名，额外插入 preamble。
        self.custom_packages_entry = Adw.EntryRow()
        self.custom_packages_entry.set_title(_('Other packages'))
        self.custom_packages_entry.set_tooltip_text(_('Comma-separated package names to include additionally.'))
        self.packages_box.append(self.custom_packages_entry)

        self.group_packages.append(self.packages_box)

        # Preview -------------------------------------------------------------
        # 实时预览将生成的 \\documentclass 行（报告 #3），由 controller 在
        # 进入本页时填入 preview_label。
        self.group_preview = Adw.PreferencesGroup()
        self.group_preview.set_title(_('Preview'))
        self.preview_label = Gtk.Label()
        self.preview_label.set_selectable(True)
        self.preview_label.set_xalign(0)
        self.preview_label.set_wrap(True)
        self.preview_label.set_markup('<tt>\\documentclass{article}</tt>')
        self.group_preview.add(self.preview_label)

        # Layout -------------------------------------------------------------
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.content.append(self.group_document_properties)
        self.content.append(self.group_language)
        self.content.append(self.group_font)
        self.content.append(self.group_packages)
        self.content.append(self.group_preview)

        self.append(self.wrap_content(self.content))

    def _create_package_row(self, label, name):
        row = Adw.SwitchRow()
        row.set_title(label)
        row.set_tooltip_markup(self.package_descriptions[name])
        return row

    def _create_package_group(self, title, rows):
        group = Adw.PreferencesGroup()
        group.set_title(title)
        for row in rows:
            group.add(row)
        return group

    def on_packages_search(self, entry):
        '''按包名（键）过滤开关行，同时隐藏空分组。'''
        query = entry.get_text().lower().strip()
        for name, row in self.option_packages.items():
            row.set_visible(query == '' or query in name.lower())
        # Hide groups where all children are hidden
        for group_name, group in self._package_groups.items():
            any_visible = any(
                child.get_visible()
                for child in group
                if isinstance(child, Adw.SwitchRow)
            )
            group.set_visible(any_visible)

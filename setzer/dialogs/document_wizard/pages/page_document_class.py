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
from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Adw

from setzer.dialogs.document_wizard.pages.page import Page, PageView
from setzer.dialogs.document_wizard import page_map
from setzer.app.service_locator import ServiceLocator
import setzer.widgets.async_svg.async_svg as async_svg

import os


class DocumentClassPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = DocumentClassPageView()

    def observe_view(self):
        def row_selected(box, row, user_data=None):
            child_name = row.get_title().lower()
            self.current_values['document_class'] = child_name
            self.view.preview_container.set_visible_child_name(child_name)

        def template_selected(combo, pspec):
            # 索引 0 为"不加载"；其余对应已保存模板。
            selected = combo.get_selected()
            if selected == 0 or selected == Gtk.INVALID_LIST_POSITION:
                return
            name = self.view.template_names[selected - 1]
            if self.controller.apply_template(name):
                # apply_template 已刷新各页控件；跳到通用设置页并刷新预览。
                self.controller.goto_page(page_map.GENERAL_PAGE_INDEX)

        self.view.list.connect('row-selected', row_selected)
        self.view.templates_combo.connect('notify::selected', template_selected)

    def load_presets(self, presets):
        try:
            row = self.view.list_rows[presets['document_class']]
        except (TypeError, KeyError):
            row = self.view.list_rows[self.current_values['document_class']]
        self.view.list.select_row(row)

    def on_activation(self):
        # 进入文档类页时刷新模板下拉（报告 #5）。
        if getattr(self, 'controller', None) is not None:
            self.view.set_templates(list(self.controller.get_templates().keys()))


class DocumentClassPageView(PageView):

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 1: ' + _('Choose a document class')

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.list.set_size_request(348, -1)
        self.list.set_can_focus(False)
        self.list.add_css_class('boxed-list')
        self.list_rows = dict()
        for document_class in ['beamer', 'letter', 'book', 'report', 'article',
                               'scrbook', 'scrreprt', 'scrartcl', 'scrlttr2']:
            row = Adw.ActionRow()
            row.set_title(document_class.title())
            self.list_rows[row.get_title().lower()] = row
            self.list.prepend(row)

        self.list.set_vexpand(False)

        self.preview_container = Gtk.Stack()
        self.preview_container.set_size_request(366, -1)
        self.preview_data = list()
        self.preview_data.append({'name': 'article', 'image': 'article1.svg', 'text': _('<b>Article:</b>  For articles in scientific journals, term papers, handouts, short reports, …\n\nThis class on its own is pretty simplistic and is often used as a starting point for more custom layouts.')})
        self.preview_data.append({'name': 'book', 'image': 'book1.svg', 'text': _('<b>Book:</b>  For actual books containing many chapters and sections.')})
        self.preview_data.append({'name': 'report', 'image': 'report1.svg', 'text': _('<b>Report:</b>  For longer reports and articles containing more than one chapter, small books, thesis.')})
        self.preview_data.append({'name': 'letter', 'image': 'letter1.svg', 'text': _('<b>Letter:</b>  For writing letters.')})
        self.preview_data.append({'name': 'beamer', 'image': 'beamer1.svg', 'text': _('<b>Beamer:</b>  A class for making presentation slides with LaTeX.\n\nThere are many predefined presentation styles.')})
        # KOMA-Script 类复用对应标准类缩略图，仅文案不同（报告 #4）。
        self.preview_data.append({'name': 'scrartcl', 'image': 'article1.svg', 'text': _('<b>Scrartcl:</b>  KOMA-Script replacement for the article class. Adds many customizations and sensible defaults.')})
        self.preview_data.append({'name': 'scrreprt', 'image': 'report1.svg', 'text': _('<b>Scrreprt:</b>  KOMA-Script replacement for the report class.')})
        self.preview_data.append({'name': 'scrbook', 'image': 'book1.svg', 'text': _('<b>Scrbook:</b>  KOMA-Script replacement for the book class.')})
        self.preview_data.append({'name': 'scrlttr2', 'image': 'letter1.svg', 'text': _('<b>Scrlttr2:</b>  KOMA-Script letter class with advanced features for address, date, and subject layout.')})
        for item in self.preview_data:
            image = async_svg.AsyncSvg(os.path.join(ServiceLocator.get_resources_path(), 'document_wizard', item['image']), 374, 262)
            image.set_margin_bottom(6)

            label = Gtk.Label()
            label.set_markup(item['text'])
            label.set_xalign(0)
            label.set_wrap(True)
            label.set_margin_start(6)
            label.set_margin_end(6)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.append(image)
            box.append(label)

            self.preview_container.add_named(box, item['name'])

        # 模板库（报告 #5）：下拉加载已保存的命名模板。
        self.group_templates = Adw.PreferencesGroup()
        self.group_templates.set_title(_('Templates'))
        self.templates_combo = Adw.ComboRow()
        self.templates_combo.set_title(_('Load template'))
        self.templates_combo.set_model(Gtk.StringList())  # 占位，on_activation 填充
        self.template_names = list()
        self.group_templates.add(self.templates_combo)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        inner.append(self.list)
        inner.append(self.preview_container)
        self.content.append(inner)
        self.content.append(self.group_templates)

        self.append(self.content)

    def set_templates(self, names):
        '''用已保存模板名填充下拉；索引 0 为"不加载"。'''
        self.template_names = list(names)
        model = Gtk.StringList()
        model.append(_('— None —'))
        for name in self.template_names:
            model.append(name)
        self.templates_combo.set_model(model)
        self.templates_combo.set_selected(0)

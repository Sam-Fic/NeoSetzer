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

import setzer.workspace.sidebar.document_structure_page.document_structure_page as document_structure_page
import setzer.workspace.sidebar.symbols_page.symbols_page as symbols_page
import setzer.workspace.sidebar.document_structure_page.data_provider as data_provider
import setzer.workspace.sidebar.document_structure_page.files as files_section
import setzer.workspace.sidebar.document_structure_page.structure as structure_section
import setzer.workspace.sidebar.document_structure_page.labels as labels_section
import setzer.workspace.sidebar.document_structure_page.todos as todos_section
import setzer.workspace.sidebar.document_stats.document_stats as document_stats_section
from setzer.app.service_locator import ServiceLocator


class Sidebar(object):

    def __init__(self, workspace):
        self.view = ServiceLocator.get_main_window().sidebar
        self.workspace = workspace

        self.data_provider = data_provider.DataProvider(self, workspace)

        self.create_document_structure_page()
        self.create_symbols_page()

        self.view.add_named(self.document_structure_page, 'document_structure')
        self.view.add_named(self.symbols_page.view, 'symbols')

        self.view.set_pages(self.document_structure_page, self.symbols_page.view)

        self.document_structure_page.switch_button.connect('clicked', lambda b: self.view.switch_page())
        self.symbols_page.view.switch_button.connect('clicked', lambda b: self.view.switch_page())

        self.data_provider.connect('document_changed', self.on_document_changed)

        # Document Stats 定时器随可见性启停：切到 Symbols 页时暂停 stats 的
        # 1s/2s 定时器（stat + texcount spawn），回到 Structure 页时恢复。
        self.view.stack.connect('notify::visible-child', self.on_visible_child_changed)

        self.view.stack.queue_draw()

    def on_visible_child_changed(self, stack, pspec):
        active = (stack.get_visible_child() is self.document_structure_page)
        self.document_stats_section.set_active(active)

    def on_document_changed(self, data_provider, document):
        if document is None:
            self.document_structure_page.show_no_document()
        else:
            self.document_structure_page.show_content()

    def create_document_structure_page(self):
        self.document_structure_page = document_structure_page.DocumentStructurePage()

        self.files_section = files_section.FilesSection(self.data_provider)
        self.document_structure_page.add_section('files', _('Files'), self.files_section.view)

        self.structure_section = structure_section.StructureSection(self.data_provider)
        self.document_structure_page.add_section('structure', _('Document Structure'), self.structure_section.view)

        self.labels_section = labels_section.LabelsSection(self.data_provider)
        self.document_structure_page.add_section('labels', _('Labels'), self.labels_section.view)

        self.todos_section = todos_section.TodosSection(self.data_provider)
        self.document_structure_page.add_section('todos', _('To-Dos'), self.todos_section.view)

        self.document_stats_section = document_stats_section.DocumentStats(self.workspace)
        self.document_stats_section.set_group(
            self.document_structure_page.add_section('stats', _('Document Stats'), self.document_stats_section.view))

    def create_symbols_page(self):
        self.symbols_page = symbols_page.SymbolsPage(self.workspace)



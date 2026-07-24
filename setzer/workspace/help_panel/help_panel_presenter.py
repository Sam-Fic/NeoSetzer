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

import setzer.workspace.help_panel.help_panel_viewgtk as help_panel_view

from setzer.app.service_locator import ServiceLocator


class HelpPanelPresenter(object):
    '''Pass-11: search_button 已迁移到 headerbar（help_search_button），
    通过 ServiceLocator.get_main_window().headerbar 引用。'''

    def __init__(self, help_panel, view):
        self.help_panel = help_panel
        self.view = view
        self.headerbar = ServiceLocator.get_main_window().headerbar

        self.help_panel.connect('search_query_changed', self.on_search_query_changed)
        self.help_panel.connect('uri_changed', self.on_uri_changed)

        self.view.content.load_uri(self.help_panel.current_uri)

    def on_search_query_changed(self, help_panel):
        results_list = self.help_panel.search_results
        for item in self.view.search_result_items:
            self.view.search_results.remove(item)
        self.view.search_result_items = list()
        if results_list:
            self.view.search_entry.remove_css_class('error')
            self.view.search_scroll.set_visible(True)
            self.view.no_results_slate.set_visible(False)
            self.view.initial_slate.set_visible(False)
            for item in reversed(results_list):
                list_item = help_panel_view.SearchResultView(item)
                self.view.search_results.prepend(list_item)
                self.view.search_result_items.append(list_item)
        elif self.help_panel.query != '':
            self.view.search_entry.add_css_class('error')
            self.view.search_scroll.set_visible(False)
            self.view.no_results_slate.set_visible(True)
            self.view.initial_slate.set_visible(False)
        else:
            self.view.search_entry.remove_css_class('error')
            self.view.search_scroll.set_visible(False)
            self.view.no_results_slate.set_visible(False)
            self.view.initial_slate.set_visible(True)

    def on_uri_changed(self, help_panel, uri):
        if self.view.content.get_uri() != uri:
            self.view.content.load_uri(uri)
        self.headerbar.help_search_button.set_active(False)



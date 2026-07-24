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
gi.require_versions({'Gtk': '4.0', 'WebKit': '6.0', 'Adw': '1'})
from gi.repository import WebKit, Gtk, Adw, Pango

from setzer.widgets.search_entry.search_entry import SearchEntry


class HelpPanelView(Gtk.Box):
    '''帮助面板的视图层。

    Pass-11 重构：原 ActionBar（home / up / back / next / search）已全部
    迁移到 headerbar，由 HeaderBar.help_buttons 持有，并通过
    HeaderBar.panel_buttons_stack 在帮助展开时显示。本视图仅保留 WebView
    内容栈与搜索页（search_clamp）。控制器对按钮的引用统一改为
    ServiceLocator.get_main_window().headerbar.help_*。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_size_request(396, -1)
        self.add_css_class('help')

        # Search page: a single Adw.Clamp wraps the vertical search content,
        # giving native bounded/centered width. The entry sits at the top,
        # results scroll below, and a compact StatusPage shows the no-results
        # empty state. This replaces the former double-CenterBox floating blob.
        self.search_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search_content_box.set_margin_top(12)
        self.search_content_box.set_margin_bottom(12)

        self.search_entry = SearchEntry()
        self.search_entry.set_placeholder_text(_('Search help'))
        self.search_content_box.append(self.search_entry)

        self.search_results = Gtk.ListBox()
        self.search_results.set_selection_mode(Gtk.SelectionMode.NONE)
        self.search_results.set_can_focus(False)
        self.search_results.set_margin_top(12)
        self.search_scroll = Gtk.ScrolledWindow()
        self.search_scroll.set_vexpand(True)
        self.search_scroll.kinetic_scrolling = True
        self.search_scroll.overlay_scrolling = True
        self.search_scroll.set_child(self.search_results)
        self.search_content_box.append(self.search_scroll)

        self.no_results_slate = Adw.StatusPage()
        self.no_results_slate.add_css_class('compact')
        self.no_results_slate.set_icon_name('system-search-symbolic')
        self.no_results_slate.set_title(_('No results found'))
        self.no_results_slate.set_visible(False)
        self.no_results_slate.set_vexpand(True)
        self.no_results_slate.set_valign(Gtk.Align.CENTER)
        self.search_content_box.append(self.no_results_slate)

        self.initial_slate = Adw.StatusPage()
        self.initial_slate.add_css_class('compact')
        self.initial_slate.set_icon_name('system-search-symbolic')
        self.initial_slate.set_title(_('Search Help'))
        self.initial_slate.set_description(_('Type a keyword to search the documentation.'))
        self.initial_slate.set_visible(True)
        self.initial_slate.set_vexpand(True)
        self.initial_slate.set_valign(Gtk.Align.CENTER)
        self.search_content_box.append(self.initial_slate)

        self.search_clamp = Adw.Clamp()
        self.search_clamp.set_maximum_size(600)
        self.search_clamp.set_tightening_threshold(400)
        self.search_clamp.set_margin_start(12)
        self.search_clamp.set_margin_end(12)
        self.search_clamp.set_child(self.search_content_box)

        self.content = WebKit.WebView()
        self.content.set_hexpand(True)
        self.content.set_vexpand(True)
        self.user_content_manager = self.content.get_user_content_manager()

        self.settings = self.content.get_settings()
        self.settings.set_enable_javascript(False)
        self.settings.set_enable_javascript_markup(False)
        self.settings.set_enable_developer_extras(False)
        self.settings.set_enable_page_cache(False)
        # Make help pages scroll smoothly with touchpads/mice.
        self.settings.set_enable_smooth_scrolling(True)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self.content, 'content')
        self.stack.add_named(self.search_clamp, 'search')

        self.append(self.stack)

        self.search_result_items = list()


class SearchResultView(Gtk.ListBoxRow):

    def __init__(self, data):
        Gtk.ListBoxRow.__init__(self)
        self.set_can_focus(False)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(15)
        self.set_margin_end(15)
        self.uri_ending = data[0]
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.set_spacing(2)
        self.text_label = Gtk.Label()
        self.text_label.set_markup(data[1])
        self.text_label.set_xalign(0)
        self.text_label.set_wrap(True)
        self.text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text_label.set_selectable(False)
        self.location_label = Gtk.Label()
        self.location_label.set_markup(data[2])
        self.location_label.set_xalign(0)
        self.location_label.set_wrap(True)
        self.location_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.location_label.add_css_class('dim-label')
        self.location_label.set_selectable(False)
        self.box.append(self.text_label)
        self.box.append(self.location_label)
        self.set_child(self.box)

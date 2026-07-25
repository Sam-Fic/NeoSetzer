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


class HelpPanelPresenter(object):
    '''Pass-12: 按钮回到 help_panel 内嵌工具栏，直接通过 self.view.* 访问。'''

    def __init__(self, help_panel, view):
        self.help_panel = help_panel
        self.view = view

        self.help_panel.connect('search_query_changed', self.on_search_query_changed)
        self.help_panel.connect('uri_changed', self.on_uri_changed)

        self.view.content.load_uri(self.help_panel.current_uri)

    def on_search_query_changed(self, help_panel):
        results_list = self.help_panel.search_results
        if results_list:
            self.view.search_entry.remove_css_class('error')
            self.view.search_scroll.set_visible(True)
            self.view.no_results_slate.set_visible(False)
            self.view.initial_slate.set_visible(False)

            # 结果计数：ngettext 处理单复数（"1 result" vs "N results"）。
            count = len(results_list)
            self.view.result_count_label.set_text(
                ngettext('{n} result', '{n} results', count).format(n=count))
            self.view.result_count_label.set_visible(True)

            # 复用已存在的 row：搜索结果上限 8 条，原实现每次按键（去抖后）
            # 都 8 次 ListBox.remove + 8 次 prepend（每次 remove 触发 ListBox
            # 内部重新索引，N 次 remove 是 O(N²)）+ 8 次 SearchResultView
            # 构造（每个含 ListBoxRow + Box + 2 Label = 4 widget）。
            # 改为：已有的 row 调 update_content 仅 set_markup；不够才新建；
            # 多余的 row set_visible(False) 保留在 ListBox 中以备下次复用。
            existing = self.view.search_result_items
            for i, item in enumerate(results_list):
                if i < len(existing):
                    row = existing[i]
                    row.update_content(item)
                    if not row.get_visible():
                        row.set_visible(True)
                else:
                    row = help_panel_view.SearchResultView(item)
                    self.view.search_results.append(row)
                    existing.append(row)
            # 隐藏多余 row（结果数比上次少时）
            for i in range(len(results_list), len(existing)):
                existing[i].set_visible(False)
        elif self.help_panel.query != '':
            self.view.search_entry.add_css_class('error')
            self.view.search_scroll.set_visible(False)
            self.view.no_results_slate.set_visible(True)
            self.view.initial_slate.set_visible(False)
            self.view.result_count_label.set_visible(False)
            # 隐藏所有已有 row 而非销毁，下次搜索可复用
            for row in self.view.search_result_items:
                row.set_visible(False)
        else:
            self.view.search_entry.remove_css_class('error')
            self.view.search_scroll.set_visible(False)
            self.view.no_results_slate.set_visible(False)
            self.view.initial_slate.set_visible(True)
            self.view.result_count_label.set_visible(False)
            for row in self.view.search_result_items:
                row.set_visible(False)

    def on_uri_changed(self, help_panel, uri):
        if self.view.content.get_uri() != uri:
            self.view.content.load_uri(uri)
        self.view.search_button.set_active(False)



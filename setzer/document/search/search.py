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
gi.require_version('GtkSource', '5')
from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import GtkSource

import setzer.document.search.search_viewgtk as search_view
from setzer.helpers.observable import Observable
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.helpers.timer import timer


class Search(Observable):

    def __init__(self, document, document_view):
        Observable.__init__(self)

        self.view = search_view.SearchBar()
        self.search_bar_mode = None

        self.document_view = document_view
        self.document = document
        self.document_view.vbox.append(self.view)

        self.search_settings = GtkSource.SearchSettings()
        self.search_context = GtkSource.SearchContext.new(self.document.source_buffer, self.search_settings)
        self.search_context.set_highlight(True)

        # occurrences-count 由 GtkSource 异步计算：在大文档上首次 set_search_text
        # 后会返回 -1（仍在扫描）。原实现用 `while get_occurrences_count() == -1:
        # forward()` 强制同步扫描整本 buffer，对 50K+ 行的论文每次按键都阻塞
        # 主线程数百毫秒。改为监听 notify::occurrences-count，让 GtkSource 后台
        # 扫描完成时回调刷新计数器，主线程零阻塞。
        self.search_context.connect('notify::occurrences-count', self.on_occurrences_count_changed)
        # 缓存当前选中匹配的序号：on_search_next_match 用 occurrence_position
        # 计算 match_no，但 total 可能此时仍为 -1（异步未完成）。缓存 match_no
        # 等 total 就绪时一并显示。
        self._pending_match_no = None

        self.view.entry.connect('changed', self.on_search_entry_changed)
        self.view.entry.connect('stop-search', self.on_search_stop)
        self.view.entry.connect('next-match', self.on_search_next_match)
        self.view.entry.connect('previous-match', self.on_search_previous_match)
        self.view.entry.connect('activate', self.on_search_entry_activate)
        self.view.close_button.connect('clicked', self.on_search_close_button_click)
        self.view.next_button.connect('clicked', self.on_search_next_button_click)
        self.view.prev_button.connect('clicked', self.on_search_prev_button_click)
        self.view.replace_button.connect('clicked', self.on_replace_button_click)
        self.view.replace_all_button.connect('clicked', self.on_replace_all_button_click)
        self.document.connect('cursor_position_changed', self.on_selection_might_have_changed)

    def on_selection_might_have_changed(self, document):
        self.update_replace_button()

    def on_search_close_button_click(self, button_object=None):
        self.on_search_stop()

    def on_search_next_button_click(self, button_object=None):
        self.on_search_next_match()
        
    def on_search_prev_button_click(self, button_object=None):
        self.on_search_previous_match()
        
    def on_replace_button_click(self, button_object=None):
        replacement = self.view.replace_entry.get_text()
        bounds = self.search_context.get_buffer().get_selection_bounds()
        if len(bounds) == 2:
            self.search_context.replace(*bounds, replacement, -1)
            self.on_search_next_match()

    def on_replace_all_button_click(self, button_object=None):
        original = self.view.entry.get_text()
        replacement = self.view.replace_entry.get_text()
        number_of_occurrences = self.search_context.get_occurrences_count()

        if number_of_occurrences > 0:
            dialog = DialogLocator.get_dialog('replace_confirmation')
            dialog.run(original, replacement, number_of_occurrences, self.search_context)

    def on_search_entry_activate(self, entry=None):
        self.on_search_next_match(entry, True)
        self.document_view.source_view.grab_focus()

    def on_search_next_match(self, entry=None, include_current_highlight=False):
        buffer = self.search_context.get_buffer()
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        bound_iter = buffer.get_iter_at_mark(buffer.get_selection_bound())

        if include_current_highlight:
            if insert_iter.get_offset() < bound_iter.get_offset(): search_iter = insert_iter
            else: search_iter = bound_iter
            result = self.search_context.forward(search_iter)
        else:
            if insert_iter.get_offset() < bound_iter.get_offset(): search_iter = bound_iter
            else: search_iter = insert_iter
            result = self.search_context.forward(search_iter)

        if result[0] == True:
            self._select_match(buffer, result)
        else:
            search_iter = buffer.get_start_iter()
            result = self.search_context.forward(search_iter)

            if result[0] == True:
                self._select_match(buffer, result)

    def on_search_previous_match(self, entry=None):
        buffer = self.search_context.get_buffer()
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        bound_iter = buffer.get_iter_at_mark(buffer.get_selection_bound())

        if insert_iter.get_offset() > bound_iter.get_offset(): search_iter = bound_iter
        else: search_iter = insert_iter
        result = self.search_context.backward(search_iter)

        if result[0] == True:
            self._select_match(buffer, result, reverse=True)
        else:
            search_iter = buffer.get_end_iter()
            result = self.search_context.backward(search_iter)

            if result[0] == True:
                self._select_match(buffer, result, reverse=True)

    def _select_match(self, buffer, result, reverse=False):
        '''统一处理 next/previous 命中的选中 + 滚动 + 计数器更新。

        计数器逻辑：match_no 立即可算（occurrence_position 是 O(1) 查表），
        total 来自 occurrences-count，GtkSource 异步计算时返回 -1。total=-1 时
        缓存 match_no 到 _pending_match_no，等 notify::occurrences-count 回调
        补全显示；total 已就绪则立即显示。reverse 仅决定 select_range 的方向
        （保持光标落在匹配起始/结束侧的语义与原代码一致）。
        '''
        if reverse:
            buffer.select_range(result[1], result[2])
        else:
            buffer.select_range(result[2], result[1])
        self.document.scroll_cursor_onscreen()
        match_no = self.search_context.get_occurrence_position(result[1], result[2])
        total = self.search_context.get_occurrences_count()
        self._pending_match_no = match_no
        if total == -1:
            # total 仍在异步扫描中。先清空计数器（避免显示「X of -1」），
            # 待 on_occurrences_count_changed 回调用缓存的 match_no 补全。
            self.set_match_counter(-1, -1)
        else:
            self.set_match_counter(match_no, total)

    def on_occurrences_count_changed(self, search_context, gparam):
        '''GtkSource 后台扫描完成时回调：用缓存的 match_no + 新就绪的 total
        更新计数器。若 total 仍为 -1（极端情况下 GtkSource 二次扫描），等下
        一次通知。'''
        total = search_context.get_occurrences_count()
        if total == -1:
            return
        match_no = self._pending_match_no
        if match_no is None:
            # 没有当前选中匹配（如刚清空搜索再输入），但仍要显示总数。
            self.set_match_counter(-1, total)
        else:
            self.set_match_counter(match_no, total)

    def on_search_entry_changed(self, entry):
        search_view = self.view
        self.search_settings.set_search_text(entry.get_text())
        search_view.replace_entry.set_text(entry.get_text())

        # scan buffer, then highlight match
        if len(entry.get_text()) > 0:
            buffer = self.search_context.get_buffer()
            result = self.search_context.forward(buffer.get_start_iter())
            if result[0] == False:
                self._pending_match_no = None
                self.set_match_counter(-1, -1)
                search_view.entry.add_css_class('error')
                search_view.replace_all_button.set_sensitive(False)
            else:
                search_view.entry.remove_css_class('error')
                # 不再用 while 循环强制同步扫描整本 buffer（大文档每次按键
                # 阻塞主线程）。改为：立即跳到首个匹配让用户看到结果，总数由
                # notify::occurrences-count 异步回调刷新计数器。
                self.on_search_next_match(entry, include_current_highlight=True)
                search_view.replace_all_button.set_sensitive(True)
        else:
            self._pending_match_no = None
            self.set_match_counter(-1, -1)
            search_view.entry.remove_css_class('error')
            search_view.replace_all_button.set_sensitive(False)

    def update_replace_button(self):
        selected_text = self.document.get_selected_text()
        if selected_text != None and selected_text == self.view.entry.get_text():
            self.view.replace_button.set_sensitive(True)
        else:
            self.view.replace_button.set_sensitive(False)

    def on_search_stop(self, entry=None):
        self.hide_search_bar()

    '''
    *** actions: search bar
    '''

    def hide_search_bar(self):
        self.on_search_next_match(None, True)
        self.document_view.source_view.grab_focus()
        self.view.set_search_mode(False)
        self.view.replace_revealer.set_reveal_child(False)
        self.view.entry.set_text('')
        self.search_bar_mode = None
        self.add_change_code('mode_changed')

    def set_mode_search(self):
        self.view.set_search_mode(True)
        GLib.idle_add(self.search_entry_grab_focus, None)
        self.search_bar_mode = 'search'
        self.view.replace_revealer.set_reveal_child(False)
        self.add_change_code('mode_changed')

    def set_mode_replace(self):
        self.view.set_search_mode(True)
        GLib.idle_add(self.search_entry_grab_focus, None)
        self.search_bar_mode = 'replace'
        self.view.replace_revealer.set_reveal_child(True)
        self.add_change_code('mode_changed')

    def search_entry_grab_focus(self, args=None):
        entry = self.view.entry

        selection = self.document.get_selected_text()
        if selection != None:
            entry.set_text(selection)

        entry.grab_focus()
        entry.set_position(len(entry.get_text()))

        if selection == None:
            entry.select_region(0, len(entry.get_text()))
            self.on_search_entry_changed(entry)

    def set_match_counter(self, match_no=-1, total=-1):
        search_bar = self.view
        if total == -1:
            search_bar.match_counter.set_text('')
            search_bar.prev_button.set_sensitive(False)
            search_bar.next_button.set_sensitive(False)
        else:
            search_bar.match_counter.set_text(str(match_no) + ' of ' + str(total))
            search_bar.prev_button.set_sensitive(True)
            search_bar.next_button.set_sensitive(True)



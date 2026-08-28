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
gi.require_version('GtkSource', '5')
from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import GtkSource
from gi.repository import Adw

import setzer.document.search.search_viewgtk as search_view
from setzer.helpers.observable import Observable
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.helpers.timer import timer
from setzer.app.service_locator import ServiceLocator


class Search(Observable):

    def __init__(self, document, document_view):
        Observable.__init__(self)

        self.view = search_view.SearchBar()
        self.search_bar_mode = None
        self.settings = ServiceLocator.get_settings()

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

        self._find_entry_changed_handler = self.view.entry.connect('changed', self.on_search_entry_changed)
        self.view.entry.connect('stop-search', self.on_search_stop)
        self.view.entry.connect('next-match', lambda entry: self.on_search_next_match(entry, record_history=True))
        self.view.entry.connect('previous-match', lambda entry: self.on_search_previous_match(entry, record_history=True))
        self.view.entry.connect('activate', self.on_search_entry_activate)
        self.view.close_button.connect('clicked', self.on_search_close_button_click)
        self.view.next_button.connect('clicked', self.on_search_next_button_click)
        self.view.prev_button.connect('clicked', self.on_search_prev_button_click)
        self.view.replace_button.connect('clicked', self.on_replace_button_click)
        self.view.replace_all_button.connect('clicked', self.on_replace_all_button_click)
        self.view.case_toggle.connect('toggled', self.on_match_option_toggled)
        self.view.regex_toggle.connect('toggled', self.on_match_option_toggled)
        self.view.word_toggle.connect('toggled', self.on_match_option_toggled)
        self.view.preserve_case_toggle.connect('toggled', self.on_match_option_toggled)
        self._replace_mode_handler = self.view.replace_mode_button.connect('toggled', self.on_replace_mode_toggled)
        self._selection_toggle_handler = self.view.selection_toggle.connect('toggled', self.on_selection_toggle_toggled)
        self.document.connect('cursor_position_changed', self.on_selection_might_have_changed)

        self._search_in_selection = False
        self._selection_start = None
        self._selection_end = None

        # 历史 popup 信号连接
        find_popup = self.view.find_history_popup
        find_popup.connect('item-selected', self._on_find_history_selected)
        find_popup.connect('item-deleted', self._on_find_history_deleted)
        find_popup.connect('show', self._on_find_history_show)

        replace_popup = self.view.replace_history_popup
        replace_popup.connect('item-selected', self._on_replace_history_selected)
        replace_popup.connect('item-deleted', self._on_replace_history_deleted)
        replace_popup.connect('show', self._on_replace_history_show)

    # --- History handling ---

    def _add_find_history(self, text):
        """Add a search term to history (called on user-initiated search)."""
        self.settings.add_to_search_history('find', text)

    def _add_replace_history(self, text):
        """Add a replacement term to history (called on replace)."""
        self.settings.add_to_search_history('replace', text)

    def _set_entry_text_blocked(self, entry, text, handler_id):
        """Set entry text while blocking the given signal handler.

        Prevents programmatic text changes (e.g. from picking a history
        item) from triggering side-effects like auto-copying search text
        into the replace field.
        """
        entry.handler_block(handler_id)
        entry.set_text(text)
        entry.handler_unblock(handler_id)

    def _on_find_history_show(self, popup):
        """Populate the find-history popup when it's about to be shown."""
        items = self.settings.get_search_history('find')
        popup.populate(items, on_clear=self._clear_find_history)

    def _on_replace_history_show(self, popup):
        """Populate the replace-history popup when it's about to be shown."""
        items = self.settings.get_search_history('replace')
        popup.populate(items, on_clear=self._clear_replace_history)

    def _clear_find_history(self):
        """Clear all search history (called from popup's Clear button)."""
        self.settings.clear_search_history('find')

    def _clear_replace_history(self):
        """Clear all replace history (called from popup's Clear button)."""
        self.settings.clear_search_history('replace')

    def _on_find_history_selected(self, popup, text):
        """Handle selecting an item from find history: set search text."""
        self._set_entry_text_blocked(self.view.entry, text, self._find_entry_changed_handler)
        self.on_search_entry_changed(self.view.entry)

    def _on_replace_history_selected(self, popup, text):
        """Handle selecting an item from replace history: set replace text."""
        self.view.replace_entry.set_text(text)
        self.update_replace_button()

    def _on_find_history_deleted(self, popup, text):
        """Handle deleting a single item from find history."""
        history = self.settings.get_search_history('find')
        if text in history:
            history.remove(text)
        self.settings.set_value('search_history', 'find', history)

    def _on_replace_history_deleted(self, popup, text):
        """Handle deleting a single item from replace history."""
        history = self.settings.get_search_history('replace')
        if text in history:
            history.remove(text)
        self.settings.set_value('search_history', 'replace', history)

    def on_selection_might_have_changed(self, document):
        self.update_replace_button()
        self.update_selection_toggle_visibility()

    def on_match_option_toggled(self, toggle_button=None):
        '''Apply case / regex / whole-word flags to GtkSource.SearchSettings
        and re-run the current search so the highlights and counter update.'''
        self.search_settings.set_case_sensitive(self.view.case_toggle.get_active())
        self.search_settings.set_regex_enabled(self.view.regex_toggle.get_active())
        self.search_settings.set_at_word_boundaries(self.view.word_toggle.get_active())
        # 重新执行当前搜索（若搜索框非空），刷新高亮与计数。
        self.on_search_entry_changed(self.view.entry)

    def on_selection_toggle_toggled(self, toggle_button=None):
        if toggle_button.get_active():
            buffer = self.document.source_buffer
            if buffer.get_has_selection():
                start, end = buffer.get_selection_bounds()
                self._selection_start = start.get_offset()
                self._selection_end = end.get_offset()
                self._search_in_selection = True
            else:
                toggle_button.set_active(False)
                self._search_in_selection = False
                self._selection_start = None
                self._selection_end = None
        else:
            self._search_in_selection = False
            self._selection_start = None
            self._selection_end = None
        self.on_search_entry_changed(self.view.entry)

    def on_replace_mode_toggled(self, toggle_button=None):
        '''第一行左侧「搜索/替换」切换按钮：active 时展开 replace 行，inactive 时
        回到纯搜索模式。'''
        if toggle_button.get_active():
            self.set_mode_replace()
        else:
            self.set_mode_search()

    def update_selection_toggle_visibility(self):
        buffer = self.document.source_buffer
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            if start.get_line() != end.get_line():
                self.view.selection_toggle.set_visible(True)
                return
        self.view.selection_toggle.set_visible(False)
        if self._search_in_selection:
            self.view.selection_toggle.set_active(False)

    def on_search_close_button_click(self, button_object=None):
        self.on_search_stop()

    def on_search_next_button_click(self, button_object=None):
        self.on_search_next_match(record_history=True)
        
    def on_search_prev_button_click(self, button_object=None):
        self.on_search_previous_match(record_history=True)
        
    def on_replace_button_click(self, button_object=None):
        replacement = self.view.replace_entry.get_text()
        bounds = self.search_context.get_buffer().get_selection_bounds()
        if len(bounds) == 2:
            matched_text = bounds[0].get_text(bounds[1])
            self.search_context.replace(*bounds, self._apply_preserve_case(matched_text, replacement), -1)
            self._add_replace_history(replacement)
            self.on_search_next_match()

    def on_replace_all_button_click(self, button_object=None):
        original = self.view.entry.get_text()
        replacement = self.view.replace_entry.get_text()
        if replacement:
            self._add_replace_history(replacement)

        if self._search_in_selection:
            # 选区模式：GtkSource.SearchContext 无选区范围的 replace_all，只能
            # 逐个 forward+replace。先 dry-run 计数（_count_matches_in_selection），
            # 匹配数超过阈值时走确认对话框（与非选区模式一致），少量直接执行。
            # 替换循环包在 begin/end_user_action 内，作为单个 undo 步骤（原实现
            # 每次 replace 是独立 undo 步骤，选区替换需 N 次 undo 才能撤销）。
            count = self._count_matches_in_selection()
            if count == 0:
                return
            if count > 100:
                def do_replace():
                    self._replace_all_in_selection(replacement)
                    self.on_search_entry_changed(self.view.entry)
                dialog = DialogLocator.get_dialog('replace_confirmation')
                dialog.run(original, replacement, count, self.search_context, on_confirm=do_replace)
            else:
                self._replace_all_in_selection(replacement)
                self.on_search_entry_changed(self.view.entry)
            return

        number_of_occurrences = self.search_context.get_occurrences_count()

        # 没有匹配则不操作（0 明确无匹配；-1 是 GtkSource 异步扫描尚未就绪，
        # 此时直接返回，用户稍后重按即可得到真实数量，避免显示错误数字）。
        if number_of_occurrences == 0:
            return

        # 仅当匹配数较多（>100）或数量未知（-1）时才弹出二次确认对话框，
        # 少量替换（1–100）直接执行，降低高频确认摩擦。阈值原为 50，调至 100
        # 以减少经常做大量替换的用户的弹窗频次（仍保留对真正大批量替换的防护）。
        if number_of_occurrences > 100 or number_of_occurrences < 0:
            dialog = DialogLocator.get_dialog('replace_confirmation')
            dialog.run(original, replacement, number_of_occurrences, self.search_context)
        else:
            if self.view.preserve_case_toggle.get_active():
                # preserve-case 必须逐匹配映射替换文本的大小写，GtkSource 原生
                # replace_all 不支持，故走循环路径（整篇范围）。
                self._replace_all_in_range(replacement, 0, None)
            else:
                self.search_context.replace_all(replacement, -1)
            self.on_search_entry_changed(self.view.entry)

    def _count_matches_in_selection(self):
        '''Dry-run：在选区范围内 forward 计数，不执行替换。

        用于选区模式 replace-all 前判断匹配数，决定是否弹确认对话框。forward
        不带 replace 比 replace 便宜（无 buffer 修改 + 重新扫描），典型选区
        （< 数百匹配）< 50ms。零长度匹配（如正则 ^）时前进 1 字符避免死循环。
        '''
        buffer = self.search_context.get_buffer()
        search_iter = buffer.get_iter_at_offset(self._selection_start)
        count = 0
        while True:
            result = self.search_context.forward(search_iter)
            if not result[0] or result[1].get_offset() > self._selection_end:
                break
            count += 1
            # 移到当前匹配末尾继续找下一个；零长度匹配前进 1 字符避免死循环。
            next_iter = result[2].copy()
            if next_iter.get_offset() <= result[1].get_offset():
                next_iter.forward_char()
            search_iter = next_iter
        return count

    def _replace_all_in_selection(self, replacement):
        '''在选区范围内逐个替换所有匹配。

        GtkSource.SearchContext 没有 selection-scoped replace_all，只能 Python
        循环 forward+replace。整个操作包在 begin/end_user_action 内，作为单个
        undo 步骤（与 replace_all 一致）。try/finally 保证 end_user_action 总被
        调用，避免异常时后续编辑被合并进未关闭的 user-action。
        '''
        self._replace_all_in_range(replacement, self._selection_start, self._selection_end)

    def _replace_all_in_range(self, replacement, start_offset, end_offset):
        '''在 [start_offset, end_offset] 范围内逐个替换所有匹配。

        通用循环替换：选区模式传选区边界，非选区（整篇）模式传 (0, None)。
        preserve-case 在每个匹配上把 replacement 的大小写形态映射到匹配文本
        （GtkSource 原生 replace_all 不支持逐匹配变换）。整个循环包在
        begin/end_user_action 内作为单个 undo 步骤；try/finally 保证 end_user_action
        总被调用。
        '''
        buffer = self.search_context.get_buffer()
        search_iter = buffer.get_iter_at_offset(start_offset)
        buffer.begin_user_action()
        try:
            while True:
                result = self.search_context.forward(search_iter)
                if not result[0]:
                    break
                if end_offset is not None and result[1].get_offset() > end_offset:
                    break
                matched_text = result[1].get_text(result[2])
                self.search_context.replace(result[1], result[2],
                                            self._apply_preserve_case(matched_text, replacement), -1)
                # replace 后 result[1] 仍指向匹配起始（GtkTextIter 随编辑更新），
                # + len(replacement) 跳过刚插入的替换文本，找下一个匹配。
                new_replacement = self._apply_preserve_case(matched_text, replacement)
                search_iter = buffer.get_iter_at_offset(result[1].get_offset() + len(new_replacement))
        finally:
            buffer.end_user_action()

    def _apply_preserve_case(self, matched_text, replacement):
        '''Preserve-case：根据匹配文本的大小写形态调整替换文本（类似 gedit）。

        规则：匹配文本全大写 → 替换文本全大写；全小写 → 全小写；首字母大写且
        其余小写（Title Case）→ 替换文本首字母大写其余小写；其它情况保持替换
        文本原样。仅当 preserve-case toggle 激活时生效，否则原样返回。
        '''
        if not self.view.preserve_case_toggle.get_active():
            return replacement
        if matched_text.isupper() and matched_text.lower() != matched_text:
            return replacement.upper()
        if matched_text.islower():
            return replacement.lower()
        if matched_text[:1].isupper() and matched_text[1:].islower():
            return replacement[:1].upper() + replacement[1:].lower()
        return replacement

    def on_search_entry_activate(self, entry=None):
        self.on_search_next_match(entry, True, record_history=True)
        self.document_view.source_view.grab_focus()

    def on_search_next_match(self, entry=None, include_current_highlight=False, record_history=False):
        if record_history:
            search_text = self.view.entry.get_text()
            if search_text:
                self._add_find_history(search_text)

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
            if self._search_in_selection and not self._is_in_selection(result[1], result[2]):
                self._skip_to_selection_end(buffer, forward=True)
                return
            self._select_match(buffer, result)
        else:
            if self._search_in_selection:
                search_iter = buffer.get_iter_at_offset(self._selection_start)
            else:
                search_iter = buffer.get_start_iter()
            result = self.search_context.forward(search_iter)

            if result[0] == True:
                if self._search_in_selection and not self._is_in_selection(result[1], result[2]):
                    self._skip_to_selection_end(buffer, forward=True)
                    return
                self._select_match(buffer, result)
                self._show_wrap_around_toast('next')

    def on_search_previous_match(self, entry=None, record_history=False):
        if record_history:
            search_text = self.view.entry.get_text()
            if search_text:
                self._add_find_history(search_text)

        buffer = self.search_context.get_buffer()
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        bound_iter = buffer.get_iter_at_mark(buffer.get_selection_bound())

        if insert_iter.get_offset() > bound_iter.get_offset(): search_iter = bound_iter
        else: search_iter = insert_iter
        result = self.search_context.backward(search_iter)

        if result[0] == True:
            if self._search_in_selection and not self._is_in_selection(result[1], result[2]):
                self._skip_to_selection_end(buffer, forward=False)
                return
            self._select_match(buffer, result, reverse=True)
        else:
            if self._search_in_selection:
                search_iter = buffer.get_iter_at_offset(self._selection_end)
            else:
                search_iter = buffer.get_end_iter()
            result = self.search_context.backward(search_iter)

            if result[0] == True:
                if self._search_in_selection and not self._is_in_selection(result[1], result[2]):
                    self._skip_to_selection_end(buffer, forward=False)
                    return
                self._select_match(buffer, result, reverse=True)
                self._show_wrap_around_toast('prev')

    def _show_wrap_around_toast(self, direction):
        message = _('已到文档末尾，从头继续') if direction == 'next' else _('已到文档开头，从末尾继续')
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(2)
            main_window.toast_overlay.add_toast(toast)

    def _is_in_selection(self, start_iter, end_iter):
        start_offset = start_iter.get_offset()
        end_offset = end_iter.get_offset()
        return (start_offset >= self._selection_start and
                end_offset <= self._selection_end)

    def _skip_to_selection_end(self, buffer, forward=True):
        if forward:
            search_iter = buffer.get_iter_at_offset(self._selection_end)
            result = self.search_context.forward(search_iter)
        else:
            search_iter = buffer.get_iter_at_offset(self._selection_start)
            result = self.search_context.backward(search_iter)

        if result[0] == True:
            if self._is_in_selection(result[1], result[2]):
                self._select_match(buffer, result, reverse=not forward)
            else:
                self.set_match_counter(-1, 0)

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
                # 检查是否有正则表达式错误（GtkSource.SearchContext 提供
                # get_regex_error() 返回 GLib.Error，包含具体的语法错误描述）。
                # 这比笼统的 "No matches" 更有信息量，告诉用户问题出在
                # 正则表达式本身而非文档中不存在该模式。
                regex_error = self.search_context.get_regex_error()
                if regex_error is not None:
                    self.set_match_counter(-1, -2, regex_error.message)
                else:
                    # 明确无匹配：立即显示「No matches」，不要等异步。occurrences-count
                    # 回调最终也会给出 0，但此刻已可确定，避免误显示「Searching…」。
                    self.set_match_counter(-1, 0)
                search_view.entry.add_css_class('error')
                search_view.replace_all_button.set_sensitive(False)
            else:
                search_view.entry.remove_css_class('error')
                # 清除之前可能存在的正则错误状态
                self.set_match_counter(-1, -1)
                # 不再用 while 循环强制同步扫描整本 buffer（大文档每次按键
                # 阻塞主线程）。改为：立即跳到首个匹配让用户看到结果，总数由
                # notify::occurrences-count 异步回调刷新计数器。
                self.on_search_next_match(entry, include_current_highlight=True)
                search_view.replace_all_button.set_sensitive(True)
        else:
            self._pending_match_no = None
            # 空搜索框：不清空搜索状态，但计数器应隐藏（不显示「Searching…」）。
            self._clear_match_counter()
            search_view.entry.remove_css_class('error')
            search_view.replace_all_button.set_sensitive(False)

    def update_replace_button(self):
        search_text = self.view.entry.get_text()
        if not search_text:
            self.view.replace_button.set_sensitive(False)
            return
        buffer = self.document.source_buffer
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        # forward/backward 返回 (found, start, end, has_wrapped) 四元组。
        result = self.search_context.forward(cursor)
        if result[0] and result[1].get_offset() <= cursor.get_offset() <= result[2].get_offset():
            self.view.replace_button.set_sensitive(True)
        else:
            result = self.search_context.backward(cursor)
            if result[0] and result[1].get_offset() <= cursor.get_offset() <= result[2].get_offset():
                self.view.replace_button.set_sensitive(True)
            else:
                self.view.replace_button.set_sensitive(False)

    def on_search_stop(self, entry=None):
        self.hide_search_bar()

    '''
    *** actions: search bar
    '''

    def hide_search_bar(self):
        # 不再调用 on_search_next_match(None, True)：那是 Esc 关闭搜索时的意外
        # 副作用，会把光标跳到下一个匹配。用户只想关闭搜索栏，应保持光标不动。
        # 高亮由下方 entry.set_text('') → on_search_entry_changed →
        # set_search_text('') 清除，GtkSource 会在 search_text 为空时停止高亮。
        self.document_view.source_view.grab_focus()
        self.view.set_search_mode(False)
        self.view.replace_revealer.set_reveal_child(False)
        self.view.entry.set_text('')
        # handler_block 避免 set_active(False) 触发 on_selection_toggle_toggled
        # 回调链（该回调会在搜索栏已隐藏后再次调用 on_search_entry_changed，
        # 产生不必要的状态变更）。手动设置 _search_in_selection 等字段即可。
        toggle = self.view.selection_toggle
        toggle.handler_block(self._selection_toggle_handler)
        toggle.set_active(False)
        toggle.set_visible(False)
        toggle.handler_unblock(self._selection_toggle_handler)
        self._search_in_selection = False
        self._selection_start = None
        self._selection_end = None
        self.view.replace_mode_button.handler_block(self._replace_mode_handler)
        self.view.replace_mode_button.set_active(False)
        self.view.replace_mode_button.handler_unblock(self._replace_mode_handler)
        self.search_bar_mode = None
        self.add_change_code('mode_changed')

    def set_mode_search(self):
        self.view.set_search_mode(True)
        GLib.idle_add(self.search_entry_grab_focus, None)
        self.search_bar_mode = 'search'
        self.view.replace_revealer.set_reveal_child(False)
        self.view.replace_mode_button.handler_block(self._replace_mode_handler)
        self.view.replace_mode_button.set_active(False)
        self.view.replace_mode_button.handler_unblock(self._replace_mode_handler)
        self.add_change_code('mode_changed')

    def set_mode_replace(self):
        self.view.set_search_mode(True)
        GLib.idle_add(self.search_entry_grab_focus, None)
        self.search_bar_mode = 'replace'
        self.view.replace_revealer.set_reveal_child(True)
        self.view.replace_mode_button.handler_block(self._replace_mode_handler)
        self.view.replace_mode_button.set_active(True)
        self.view.replace_mode_button.handler_unblock(self._replace_mode_handler)
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

    def set_match_counter(self, match_no=-1, total=-1, error_message=None):
        search_bar = self.view
        if total == -2 and error_message is not None:
            # 正则表达式语法错误：显示 GtkSource 返回的具体错误消息
            # （如 "missing terminating ] for character class"），
            # 比通用 "No matches" 更能帮助用户定位问题。
            search_bar.match_counter.set_text(error_message)
            search_bar.prev_button.set_sensitive(False)
            search_bar.next_button.set_sensitive(False)
            search_bar.replace_all_button.set_tooltip_text(_('Replace all results'))
        elif total == 0:
            search_bar.match_counter.set_text(_('No matches'))
            search_bar.prev_button.set_sensitive(False)
            search_bar.next_button.set_sensitive(False)
            search_bar.replace_all_button.set_tooltip_text(_('Replace all results'))
        elif total == -1:
            # GtkSource 异步扫描尚未完成（大文档首次搜索可能持续数秒）。
            # 显示「Searching…」明确状态（原「X of …」让人困惑按钮为何灰着）；
            # 若已有当前匹配（match_no > 0）则保持上/下按钮可用，允许跳转——
            # GtkSource.SearchContext 在扫描进行中也能正确处理 next/prev。
            search_bar.match_counter.set_text(_('Searching…'))
            has_current = match_no > 0
            search_bar.prev_button.set_sensitive(has_current)
            search_bar.next_button.set_sensitive(has_current)
            search_bar.replace_all_button.set_tooltip_text(_('Replace all results'))
        else:
            if match_no is None or match_no < 1:
                # total 已就绪但当前没有有效序号（如异步扫描完成时上一次导航
                # 发生在扫描结束前，序号尚未确定）。此时不显示「-1 of N」，
                # 而是显示总数，避免误导。
                search_bar.match_counter.set_text(
                    ngettext('{total} match', '{total} matches', total).format(total=total))
            else:
                search_bar.match_counter.set_text(str(match_no) + ' of ' + str(total))
            search_bar.prev_button.set_sensitive(True)
            search_bar.next_button.set_sensitive(True)
            # 仅在替换模式下预览「将替换 N 处匹配」，避免普通搜索时误导。
            if self.search_bar_mode == 'replace':
                search_bar.replace_all_button.set_tooltip_text(
                    ngettext('Replace all {amount} match',
                              'Replace all {amount} matches', total).format(amount=total))
            else:
                search_bar.replace_all_button.set_tooltip_text(_('Replace all results'))

    def _clear_match_counter(self):
        '''空搜索框时隐藏计数器并重置导航/替换按钮灵敏度。

        与 set_match_counter(-1, -1) 不同：后者表示「异步扫描中」会显示
        「Searching…」，而空内容不应显示任何计数提示，故直接清空文本。
        '''
        search_bar = self.view
        search_bar.match_counter.set_text('')
        search_bar.prev_button.set_sensitive(False)
        search_bar.next_button.set_sensitive(False)
        search_bar.replace_all_button.set_tooltip_text(_('Replace all results'))



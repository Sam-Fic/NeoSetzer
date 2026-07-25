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
from gi.repository import Gtk, GLib


# 自动补全 popover 样式：Adwaita 风格圆角/阴影/选中高亮。
# 用 widget 级别 CssProvider（add_provider 到 StyleContext），优先级
# STYLE_PROVIDER_PRIORITY_USER + 1，高于 display 级别的 libadwaita 默认样式，
# 确保覆盖。原先放 data/resources/style_gtk.css（display 级别），但被
# libadwaita 的 list 样式覆盖不生效。
_AC_CSS = '''
listbox.autocomplete-popup {
    background-color: red;
    border-radius: 12px;
    padding: 4px;
}
listbox.autocomplete-popup row {
    border-radius: 6px;
    padding: 2px 8px;
}
listbox.autocomplete-popup row:selected {
    background-color: blue;
    color: white;
}
'''


class AutocompleteWidgetView(Gtk.ListBox):
    '''Autocomplete popup listing up to 5 matching commands.

    Formerly a Gtk.DrawingArea with a custom Cairo draw function; now a
    standard Gtk.ListBox. The text view keeps keyboard focus (selection is
    driven by the model via the source view's key controller), so this
    widget is display-only: can_focus/can_target are disabled and the
    selected item is highlighted programmatically via select_row().
    '''

    def __init__(self, model):
        Gtk.ListBox.__init__(self)

        self.model = model

        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_can_focus(False)
        self.set_can_target(False)
        # monospace: FontManager 的 CSS 选择器 listbox.monospace row label
        # 据此应用用户配置的字体/字号，跟随设置变化。
        # boxed-list: libadwaita 原生列表卡片样式（圆角+边框+卡片背景），
        # 自带明暗主题适配，无需自定义 CSS（自定义 CSS 被 libadwaita 高优先级
        # 覆盖不生效，boxed-list 是 libadwaita 原生支持的 class）。
        self.add_css_class('monospace')
        self.add_css_class('boxed-list')

        # 签名缓存：populate 在 select_next/select_previous/page_down/page_up
        # 以及滚动/焦点变化时都被调用，但此时 items 切片未变，仅选中项或位置
        # 不同。签名命中时跳过 clear + 重建 5 个 ListBoxRow，只更新 select_row。
        self._last_items_signature = None

    def populate(self):
        r'''Rebuild the visible rows from the model's current state.

        Mirrors the former draw(): shows items[first:first+5], bolds the
        matched prefix (current_word) and renders dotlabels in place of the
        '•' placeholder at reduced alpha. When LaTeXDB has a parse error
        (model.db_error), appends a non-selectable "database unavailable"
        row at the bottom so the user knows why \ref/\cite completions are
        empty/stale (UX report #8).
        '''
        model = self.model.model
        si = model.selected_item_index
        fi = model.first_item_index
        db_error = model.db_error

        # 签名 = (current_word, first_item_index, db_error, 可见 items 的 command+dotlabels)
        # current_word 决定加粗前缀长度，fi 决定切片起点，items 决定内容，
        # db_error 决定是否追加错误提示行。四者都不变时 row 内容完全相同，
        # 跳过重建只更新选中项。db_error 纳入签名确保 parse 错误状态变化时重建。
        if model.current_word is not None and fi is not None and (len(model.items) > 0 or db_error):
            visible = model.items[fi:fi + 5] if len(model.items) > 0 else []
            signature = (model.current_word, fi, db_error, tuple(
                (item['command'], item['dotlabels']) for item in visible))
        else:
            signature = None

        if signature == self._last_items_signature:
            # items 未变，只更新选中行
            self._update_selection(si, fi)
            return

        self._last_items_signature = signature

        # Clear existing rows.
        child = self.get_first_child()
        while child is not None:
            sibling = child.get_next_sibling()
            self.remove(child)
            child = sibling

        if model.current_word is None or si is None or fi is None or (len(model.items) == 0 and not db_error):
            return

        offset = len(model.current_word)
        selected_row = None
        for i, item in enumerate(model.items[fi:fi + 5]):
            command_text = '<b>' + GLib.markup_escape_text(item['command'][:offset]) + '</b>'
            command_text += GLib.markup_escape_text(item['command'][offset:])

            dotlabels = [d for d in item['dotlabels'].split('###') if d]
            for dotlabel in dotlabels:
                command_text = command_text.replace('•', '<span alpha="60%">' + GLib.markup_escape_text(dotlabel) + '</span>', 1)

            label = Gtk.Label()
            label.set_markup(command_text)
            label.set_halign(Gtk.Align.START)
            label.set_xalign(0.0)
            label.set_margin_start(6)
            label.set_margin_end(6)
            label.set_margin_top(2)
            label.set_margin_bottom(2)

            row = Gtk.ListBoxRow()
            row.set_child(label)
            row.set_activatable(False)
            row.set_selectable(True)
            self.append(row)

            if i == si - fi:
                selected_row = row

        if selected_row is not None:
            self.select_row(selected_row)

        # LaTeXDB 解析失败时在列表底部追加"标签数据库不可用"提示行。
        # 仅对 \ref/\cite 动态查询显示（model.db_error 已在 autocomplete.py
        # 中按此条件设置）。提示行不可选中，避免干扰键盘导航（Tab/Enter/
        # Up/Down 仅作用于上方命令行）。
        if db_error:
            self.append(self._build_db_error_row())

    def _build_db_error_row(self):
        '''构建不可选中的"标签数据库不可用"提示行。'''
        label = Gtk.Label()
        label.set_markup('⚠ ' + GLib.markup_escape_text(_('Label database unavailable (parse error)')))
        label.set_halign(Gtk.Align.START)
        label.set_xalign(0.0)
        label.set_margin_start(6)
        label.set_margin_end(6)
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        label.add_css_class('dim-label')
        label.add_css_class('caption')

        row = Gtk.ListBoxRow()
        row.set_child(label)
        row.set_activatable(False)
        row.set_selectable(False)
        return row

    def _update_selection(self, si, fi):
        '''items 未变时仅更新选中行，避免重建全部 row。'''
        if si is None or fi is None:
            self.select_row(None)
            return
        target_index = si - fi
        if target_index < 0:
            self.select_row(None)
            return
        child = self.get_first_child()
        i = 0
        while child is not None:
            if i == target_index:
                self.select_row(child)
                return
            child = child.get_next_sibling()
            i += 1
        self.select_row(None)

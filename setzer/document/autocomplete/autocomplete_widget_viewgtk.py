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
from gi.repository import Gtk, GLib


_REF_COMMANDS = ('\\ref', '\\pageref', '\\eqref')
_CITE_COMMANDS = ('\\cite', '\\citep', '\\citet', '\\parencite', '\\autocite', '\\textcite')


def _get_icon_name(item):
    '''根据补全项类型返回主题图标名（复刻 gnome-builder 补全行的图标列）。

    - \\begin{...}/\\end{...} 环境 → 有序列表图标
    - \\ref/\\cite 等交叉引用 → 标签图标
    - onlymath 命令（希腊字母等）→ 数学符号图标
    - 其他 → 通用文本图标
    '''
    command = item['command']
    if item.get('is_snippet'):
        return 'text-x-generic-symbolic'
    if command.startswith(('\\begin{', '\\end{')):
        return 'view-list-ordered-symbolic'
    if command.startswith(_REF_COMMANDS) or command.startswith(_CITE_COMMANDS):
        return 'tag-symbolic'
    if item.get('onlymath'):
        return 'own-symbols-misc-math-symbolic'
    return 'text-symbolic'


def _get_detail_text(item):
    '''提取补全项的右侧详情列文本（复刻 gnome-builder 的 details 列）。

    目前来源为 dotlabels 参数名：\\dfrac{•}{•} + "num###den###" →
    "num, den"。无参数名（如 \\section、\\ref{sec:intro}）返回空串。
    '''
    if item.get('is_snippet'):
        return item.get('description', '')
    dotlabels = [d for d in item['dotlabels'].split('###') if d]
    return ', '.join(dotlabels)


class AutocompleteWidgetView(Gtk.ListBox):
    '''Autocomplete popup listing up to 5 matching commands.

    Formerly a Gtk.DrawingArea with a custom Cairo draw function; now a
    standard Gtk.ListBox. The text view keeps keyboard focus (selection is
    driven by the model via the source view's key controller), so this
    widget is display-only: can_focus/can_target are disabled and the
    selected item is highlighted programmatically via select_row().

    When there are more than 5 matches a non-selectable, dimmed "<selected>
    / <total>" counter row is appended at the bottom of the card (mirroring
    the existing db_error info row), so the user knows there are more results
    and where the current selection sits.
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
        # 注意：不使用 libadwaita 的 boxed-list 类，因其样式可能与自定义背景色冲突。
        # 改用自定义 CSS（.autocomplete-card）实现卡片外观，确保背景色正确渲染。
        self.add_css_class('monospace')
        self.add_css_class('autocomplete-widget')
        # 延迟添加背景色 CSS 类，确保 widget 已经 realize
        GLib.idle_add(self._apply_background_css)

        # 签名缓存：populate 在 select_next/select_previous/page_down/page_up
        # 以及滚动/焦点变化时都被调用，但此时 items 切片未变，仅选中项或位置
        # 不同。签名命中时跳过 clear + 重建 5 个 ListBoxRow，只更新 select_row。
        self._last_items_signature = None
        # 计数器行的 label 引用，选中项变化时就地刷新文本，无需重建整卡。
        self._counter_label = None
        # 补全弹窗四周的 padding，让内容有呼吸感
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(4)
        self.set_margin_end(4)

    def _apply_background_css(self):
        '''延迟应用背景色 CSS（widget realize 后调用）。
        
        通过 add_overlay 添加到 Overlay 的 widget 可能在 realize 前
        就已经添加了 CSS 类，导致样式未生效。此方法在 idle 中调用，
        确保 widget 已经 realize。
        '''
        if self.get_realized():
            self.get_style_context().add_class('autocomplete-widget')

    def populate(self):
        r'''Rebuild the visible rows from the model's current state.

        Mirrors the former draw(): shows items[first:first+5], bolds the
        matched prefix (current_word) and renders dotlabels in place of the
        '•' placeholder at reduced alpha. When LaTeXDB has a parse error
        (model.db_error), appends a non-selectable "database unavailable"
        row at the bottom so the user knows why \ref/\cite completions are
        empty/stale (UX report #8). When there are more than 5 matches, a
        non-selectable "<selected> / <total>" counter row follows.
        '''
        model = self.model.model
        si = model.selected_item_index
        fi = model.first_item_index
        db_error = model.db_error
        total = len(model.items)
        show_counter = total > 5

        # 签名 = (current_word, first_item_index, db_error, show_counter,
        #         可见 items 的 command+dotlabels)
        # current_word 决定加粗前缀长度，fi 决定切片起点，items 决定内容，
        # db_error 决定是否追加错误提示行，show_counter 决定是否追加计数行。
        # 五者都不变时 row 内容完全相同，跳过重建只更新选中项/计数行。
        # db_error / show_counter 纳入签名确保状态变化时重建。
        if model.current_word is not None and fi is not None and (total > 0 or db_error):
            visible = model.items[fi:fi + 5] if total > 0 else []
            signature = (model.current_word, fi, db_error, show_counter, tuple(
                (item['command'], item['dotlabels']) for item in visible))
        else:
            signature = None

        if signature == self._last_items_signature:
            # items 未变，只更新选中行与计数行文本
            self._update_selection(si, fi)
            self._update_footer(si, total)
            return

        self._last_items_signature = signature

        # Clear existing rows.
        child = self.get_first_child()
        while child is not None:
            sibling = child.get_next_sibling()
            self.remove(child)
            child = sibling
        self._counter_label = None

        if model.current_word is None or si is None or fi is None or (total == 0 and not db_error):
            return

        offset = len(model.current_word)
        selected_row = None
        for i, item in enumerate(model.items[fi:fi + 5]):
            command_text = '<b>' + GLib.markup_escape_text(item['command'][:offset]) + '</b>'
            command_text += GLib.markup_escape_text(item['command'][offset:])

            dotlabels = [d for d in item['dotlabels'].split('###') if d]
            for dotlabel in dotlabels:
                command_text = command_text.replace('•', '<span alpha="60%">' + GLib.markup_escape_text(dotlabel) + '</span>', 1)

            # 复刻 gnome-builder 补全行的三列布局：图标 | 命令文本 | 详情（参数名）。
            # - 图标列：_get_icon_name 按 \begin{}/\ref/\cite/onlymath 类型选取，
            #   使用主题 symbolic 图标，set_pixel_size(16) 固定尺寸避免行高被撑大。
            # - 详情列：从 dotlabels 提取参数名（如 \dfrac 的 "num, den"），
            #   右对齐 + dim-label + caption 降级视觉权重，与查找/文件行的副标题风格一致。
            # 使用 Gtk.Grid 替代 Gtk.Box：Grid 默认透明，不会阻断 ListBoxRow 的背景色
            # 渲染（Box 在某些主题下会绘制自己的背景，导致 boxed-list 背景色丢失）。
            icon = Gtk.Image()
            icon.set_from_icon_name(_get_icon_name(item))
            icon.set_pixel_size(16)

            label = Gtk.Label()
            label.set_markup(command_text)
            label.set_halign(Gtk.Align.START)
            label.set_xalign(0.0)
            label.set_hexpand(True)
            label.set_margin_start(6)

            detail = _get_detail_text(item)
            detail_label = Gtk.Label()
            detail_label.set_text(detail)
            detail_label.set_halign(Gtk.Align.END)
            detail_label.set_xalign(1.0)
            detail_label.set_margin_start(12)
            detail_label.set_margin_end(6)
            detail_label.add_css_class('dim-label')
            detail_label.add_css_class('caption')
            detail_label.set_visible(bool(detail))

            content = Gtk.Grid()
            content.set_hexpand(True)
            content.set_column_spacing(4)
            content.attach(icon, 0, 0, 1, 1)
            content.attach(label, 1, 0, 1, 1)
            content.attach(detail_label, 2, 0, 1, 1)
            content.set_margin_top(2)
            content.set_margin_bottom(2)
            content.set_margin_start(6)
            content.set_margin_end(6)

            row = Gtk.ListBoxRow()
            row.set_child(content)
            row.set_activatable(False)
            row.set_selectable(True)
            # 直接设置行背景色（绕过 libadwaita 样式优先级问题）
            row.get_style_context().add_class('autocomplete-widget')
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

        # 匹配项 > 5 时追加"<选中项+1> / 总数"计数行（如 "12 / 50"），提示还有
        # 更多结果/当前位置。与 db_error 行同为卡片内非可选 dim 行，样式一致。
        if show_counter:
            self.append(self._build_counter_row(si, total))

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

    def _build_counter_row(self, si, total):
        '''构建卡片底部非可选的"<选中项+1> / 总数"计数行。'''
        label = Gtk.Label()
        label.set_text(str(si + 1) + ' / ' + str(total))
        label.set_halign(Gtk.Align.END)
        label.set_xalign(1.0)
        label.set_margin_start(6)
        label.set_margin_end(6)
        label.set_margin_top(2)
        label.set_margin_bottom(2)
        label.add_css_class('dim-label')
        label.add_css_class('caption')
        self._counter_label = label

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
        # items 行始终排在 db_error / 计数行之前，故按索引直接取即可。
        self.select_row(self.get_row_at_index(target_index))

    def _update_footer(self, si, total):
        '''选中项变化时就地刷新计数行文本（不重建卡片）。'''
        if self._counter_label is not None and total > 5 and si is not None:
            self._counter_label.set_text(str(si + 1) + ' / ' + str(total))

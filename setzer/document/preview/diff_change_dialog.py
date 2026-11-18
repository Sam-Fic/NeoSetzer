#!/usr/bin/env python3
# coding: utf-8

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

'''点击 diff 高亮矩形时的改动详情弹窗。

数据来自 `phitex session compare --json` 的单个 change（kind/file/line/
old_text/new_text/section）。只读展示：非模态的信息反馈本该走 Toast，
但前后文本可能各占多行，Toast 装不下——用弹窗展示、不做任何决定项，
符合 ui-guidelines「需要用户做出决定的场景才用 AlertDialog」之外的
轻量替代（本弹窗无动作按钮，仅一个 Close）。
'''

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw


# kind → (徽章文案, 文本着色 class)。error/success/warning 是 Adwaita 内建
# 的文本着色 class，主题感知，不必自定义 CSS。
_KIND_INFO = {
    'add': ('Added', 'success'),
    'del': ('Deleted', 'error'),
    'change': ('Changed', 'warning'),
}


class DiffChangeDialog(Adw.Dialog):

    def __init__(self, change):
        super().__init__()
        change_n = change.get('n')
        section = (change.get('section') or '').strip()
        self.set_title(_('Change {}').format(change_n) if section == '' else section)
        self.set_content_width(480)
        self.set_content_height(360)
        self.set_follows_content_size(True)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        close_button = Gtk.Button(label=_('Close'))
        close_button.connect('clicked', lambda button: self.close())
        header = Adw.HeaderBar()
        # ui-guidelines：禁用默认右上角关闭按钮，保留单一手动关闭入口。
        header.set_show_end_title_buttons(False)
        header.pack_start(close_button)
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        # 元信息行：kind 徽章 + 源位置（file:line 指当前版本文件）。
        kind = change.get('kind') or 'change'
        kind_text, color_class = _KIND_INFO.get(kind, _KIND_INFO['change'])
        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        kind_label = Gtk.Label(label=_(kind_text))
        kind_label.add_css_class(color_class)
        kind_label.add_css_class('heading')
        meta_row.append(kind_label)
        file_name = change.get('file')
        line = change.get('line')
        if file_name:
            location = '{}:{}'.format(file_name, line if isinstance(line, int) else '?')
            location_label = Gtk.Label(label=location)
            location_label.add_css_class('caption')
            location_label.add_css_class('dim-label')
            meta_row.append(location_label)
        content.append(meta_row)

        # 前后文本卡：del 只显示旧文本，add 只显示新文本，change 两个都显示。
        # 文本 selectable=True：审阅时常要复制原文去搜索。
        old_text = change.get('old_text') or ''
        new_text = change.get('new_text') or ''
        if old_text:
            content.append(self._build_text_card(_('Before'), old_text, 'error'))
        if new_text:
            content.append(self._build_text_card(_('After'), new_text, 'success'))
        if not old_text and not new_text:
            content.append(Gtk.Label(label=_('No text recorded for this change')))

        clamp = Adw.Clamp()
        clamp.set_child(content)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(clamp)
        scrolled.set_vexpand(True)
        self.toolbar_view.set_content(scrolled)
        self.set_child(self.toolbar_view)

    def _build_text_card(self, title, text, color_class):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_label = Gtk.Label(label=title)
        header_label.set_halign(Gtk.Align.START)
        header_label.add_css_class('caption')
        header_label.add_css_class('dim-label')
        box.append(header_label)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class('card')
        card.set_margin_start(2)
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.set_margin_top(10)
        label.set_margin_bottom(10)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.add_css_class(color_class)
        card.append(label)
        box.append(card)
        return box


def show_diff_change_dialog(parent_widget, change):
    '''在 parent_widget 所在窗口展示改动详情。'''
    dialog = DiffChangeDialog(change)
    root = parent_widget.get_root() if parent_widget is not None else None
    dialog.present(root)

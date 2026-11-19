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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''单文件并排 diff（VSCode 式），非 LaTeX 文件与 PDF diff 兜底路径共用。

左右两栏分别是 HEAD 版与工作区版全文，行按 difflib 对齐（同一行编号在
两栏里指同一行，空出来的一侧留空），改动行整行着色：左栏红（旧）、右栏
绿（新）。两栏行数恒等，所以纵向滚动可以直接互相跟随。

为什么不用统一补丁（+/- 单栏）了：侧栏弹窗面向的是"这文件改了什么"，
并排对照能一眼看到前后文；统一补丁只能逐 hunk 上下拼读。
'''

import builtins
import difflib


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango

# 超过这个行数就不做并排渲染（两侧各建一个上万行的 TextView 会卡）。
MAX_DIFF_LINES = 5000

# 改动行底色。用半透明 rgba 让深浅主题都成立（前景色仍由主题决定）。
_COLOR_REMOVED = 'rgba(192, 28, 40, 0.22)'
_COLOR_ADDED = 'rgba(38, 162, 105, 0.22)'
# TextTag 的 foreground 只认具体颜色值，不认 CSS 的 alpha(currentColor, …)。
_COLOR_GUTTER = '#8a8a8a'


def align_lines(old_text, new_text):
    '''把两侧全文对齐成 [(old_line 或 None, new_line 或 None, kind), ...]。

    kind ∈ equal / change / delete / insert：change 两侧都有（对位改写），
    delete 只有旧侧，insert 只有新侧。返回行序即渲染行序，两栏行数恒等。'''
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(i2 - i1):
                rows.append((old_lines[i1 + offset], new_lines[j1 + offset], 'equal'))
        elif tag == 'replace':
            for offset in range(max(i2 - i1, j2 - j1)):
                old_line = old_lines[i1 + offset] if i1 + offset < i2 else None
                new_line = new_lines[j1 + offset] if j1 + offset < j2 else None
                rows.append((old_line, new_line, 'change'))
        elif tag == 'delete':
            for index in range(i1, i2):
                rows.append((old_lines[index], None, 'delete'))
        elif tag == 'insert':
            for index in range(j1, j2):
                rows.append((None, new_lines[index], 'insert'))
    return rows


class DiffDialog(Adw.Dialog):

    def __init__(self, path, old_text, new_text):
        super().__init__()
        self.set_title(os.path.basename(path))
        self.set_content_width(980)
        self.set_content_height(620)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(6)
        header.set_margin_bottom(6)
        header.set_margin_start(12)
        header.set_margin_end(12)
        path_label = Gtk.Label(label=path)
        path_label.add_css_class('dim-label')
        path_label.add_css_class('caption')
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.set_xalign(0)
        path_label.set_hexpand(True)
        header.append(path_label)
        box.append(header)

        rows = align_lines(old_text, new_text)
        if len(rows) > MAX_DIFF_LINES:
            rows = rows[:MAX_DIFF_LINES]
            truncated = True
        else:
            truncated = False

        if not rows:
            empty = Gtk.Label(label=_('No changes in this file.'))
            empty.add_css_class('dim-label')
            empty.set_vexpand(True)
            empty.set_valign(Gtk.Align.CENTER)
            box.append(empty)
            self.set_child(box)
            return

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_wide_handle(True)
        old_view, old_scrolled = self._build_column(rows, side='old')
        new_view, new_scrolled = self._build_column(rows, side='new')
        paned.set_start_child(old_scrolled)
        paned.set_end_child(new_scrolled)
        paned.set_position(480)
        box.append(paned)

        if truncated:
            note = Gtk.Label(label=_('Diff truncated: too many lines to display.'))
            note.add_css_class('dim-label')
            note.add_css_class('caption')
            note.set_margin_top(6)
            note.set_margin_bottom(6)
            note.set_margin_start(12)
            box.append(note)

        self._sync_scrolling(old_scrolled, new_scrolled)
        self.set_child(box)

    def _build_column(self, rows, side):
        '''构建一栏：等宽只读 TextView + 改动行着色 + 本侧为空的占位行。'''
        buffer = Gtk.TextBuffer()
        tag_removed = buffer.create_tag('removed', background=_COLOR_REMOVED)
        tag_added = buffer.create_tag('added', background=_COLOR_ADDED)
        tag_gutter = buffer.create_tag('gutter', foreground=_COLOR_GUTTER)

        text_lines = []
        number_lines = []
        for index, (old_line, new_line, kind) in enumerate(rows, start=1):
            line = old_line if side == 'old' else new_line
            text_lines.append('' if line is None else line)
            # 行号：本侧没有这一行时留空，视觉上就是一道空白。
            number_lines.append('' if line is None else str(index))

        # 行号与正文同文本流（单个 TextView），行号列用等宽 + 灰标签色。
        gutter_width = max(len(n) for n in number_lines) if number_lines else 1
        display = []
        for number, line in zip(number_lines, text_lines):
            display.append('%(num)s │ %(line)s' % {
                'num': number.rjust(gutter_width), 'line': line})
        buffer.set_text('\n'.join(display))

        # 逐行套标签：改动行整行底色，行号段恒定灰。
        # 注意 PyGObject 的 get_iter_at_line* 返回 (ok, iter) 元组而不是
        # 迭代器本身——直接对返回值调 copy()/forward_to_line_end() 会炸。
        for offset, (old_line, new_line, kind) in enumerate(rows):
            _, line_start = buffer.get_iter_at_line(offset)
            line_end = line_start.copy()
            line_end.forward_to_line_end()
            _, gutter_end = buffer.get_iter_at_line_offset(
                offset, min(gutter_width, len(number_lines[offset])))
            if side == 'old' and kind in ('change', 'delete') and old_line is not None:
                buffer.apply_tag(tag_removed, line_start, line_end)
            if side == 'new' and kind in ('change', 'insert') and new_line is not None:
                buffer.apply_tag(tag_added, line_start, line_end)
            buffer.apply_tag(tag_gutter, line_start, gutter_end)

        view = Gtk.TextView(buffer=buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.NONE)
        view.set_monospace(True)
        view.set_left_margin(8)
        view.set_right_margin(8)
        view.set_top_margin(6)
        view.set_bottom_margin(6)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(view)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        return view, scrolled

    def _sync_scrolling(self, first, second):
        '''两栏纵向滚动互跟随（行数恒等，对齐天然成立）。

        重入保护：set_value 会再发 value-changed，不拦就会两栏互相推。'''
        state = {'busy': False}

        def follow(source, target):
            if state['busy']:
                return
            state['busy'] = True
            try:
                target.get_vadjustment().set_value(source.get_vadjustment().get_value())
            finally:
                state['busy'] = False

        first.get_vadjustment().connect(
            'value-changed', lambda adj: follow(first, second))
        second.get_vadjustment().connect(
            'value-changed', lambda adj: follow(second, first))

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


import os


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GLib, Gtk, Adw


# kind → (徽章文案, 文本着色 class)。error/success/warning 是 Adwaita 内建
# 的文本着色 class，主题感知，不必自定义 CSS。
_KIND_INFO = {
    'add': ('Added', 'success'),
    'del': ('Deleted', 'error'),
    'moved': ('Moved', 'accent'),
    'change': ('Changed', 'warning'),
}


class DiffChangeDialog(Adw.Dialog):

    def __init__(self, change, crop_provider=None):
        super().__init__()
        change_n = change.get('n')
        section = (change.get('section') or '').strip()
        self.set_title(_('Change {}').format(change_n) if section == '' else section)
        # 宽度 480 为 §6.5 档位外特例：预览浮层，跟手小窗，不进档位。
        # 静态小窗：固定尺寸 + follows_content_size。
        self.set_content_width(480)
        self.set_content_height(360)
        self.set_follows_content_size(True)

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
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # 元信息行：kind 徽章 + 源位置（file:line 指当前版本文件）。
        kind = change.get('kind') or 'change'
        kind_text, color_class = _KIND_INFO.get(kind, _KIND_INFO['change'])
        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
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
            location_label.add_css_class('dimmed')
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

        # 排版裁剪图：这处改动在 diff 构建里的样子（latexdiff 标记排版）。
        # 取图是一次 png 子进程调用（秒级），放后台线程，回来自动替换占位。
        self._crop_closed = False
        if crop_provider is not None and change_n is not None:
            content.append(self._build_crop_section(crop_provider, change_n))

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
        header_label.add_css_class('dimmed')
        box.append(header_label)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class('card')
        # 2px：卡片相对上方 caption 标题的光学缩进，表达层级归属（§2.2）。
        card.set_margin_start(2)
        label = Gtk.Label(label=text)
        label.set_wrap(True)
        label.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_xalign(0.0)
        label.set_margin_top(12)
        label.set_margin_bottom(12)
        label.set_margin_start(12)
        label.set_margin_end(12)
        label.add_css_class(color_class)
        card.append(label)
        box.append(card)
        return box

    def _build_crop_section(self, crop_provider, change_n):
        '''排版裁剪图区块：先放占位，后台线程取到 PNG 后回填。

        取图走 `phitex session png --compare --change N`（一次子进程，秒级），
        失败/取不到就整个区块让位——它只是弹窗里的锦上添花，不该以错误状态
        露脸。'''
        import threading

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_label = Gtk.Label(label=_('Typeset'))
        header_label.set_halign(Gtk.Align.START)
        header_label.add_css_class('caption')
        header_label.add_css_class('dimmed')
        box.append(header_label)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class('card')
        # 2px：卡片相对上方 caption 标题的光学缩进，表达层级归属（§2.2）。
        card.set_margin_start(2)
        placeholder = Adw.Spinner()
        placeholder.set_margin_top(12)
        placeholder.set_margin_bottom(12)
        card.append(placeholder)
        box.append(card)
        self._crop_card = card

        def _load():
            path = None
            try:
                path = crop_provider(change_n)
            except Exception:
                path = None
            GLib.idle_add(self._apply_crop, card, placeholder, path)

        threading.Thread(target=_load, daemon=True).start()
        return box

    def _apply_crop(self, card, placeholder, path):
        '''把取到的裁剪 PNG 换进卡片；弹窗已关（_crop_closed）或没取到则让位。'''
        if self._crop_closed:
            return False
        card.remove(placeholder)
        if not path or not os.path.isfile(path):
            label = Gtk.Label(label=_('No typeset crop available'))
            label.add_css_class('caption')
            label.add_css_class('dimmed')
            label.set_margin_top(12)
            label.set_margin_bottom(12)
            card.append(label)
            return False
        picture = Gtk.Picture.new_for_filename(path)
        picture.set_content_fit(Gtk.ContentFit.SCALE_DOWN)
        picture.set_hexpand(True)
        picture.set_margin_top(6)
        picture.set_margin_bottom(6)
        card.append(picture)
        # 高度随内容变化：弹窗开了 follows_content_size，但已显示后追加内容
        # 需要主动收一次尺寸，否则底部留白。
        self.set_content_height(-1)
        return False

    def do_close(self):
        '''弹窗关掉时立牌子：在途的取图线程回来后不再动 widget。'''
        self._crop_closed = True
        Adw.Dialog.do_close(self)


def show_diff_change_dialog(parent_widget, change, crop_provider=None):
    '''在 parent_widget 所在窗口展示改动详情。

    crop_provider：可选的 (change_n) → PNG 路径 回调，在后台线程调用——
    由调用方（preview controller）绑定 PhitexSession 与基线快照。'''
    dialog = DiffChangeDialog(change, crop_provider=crop_provider)
    root = parent_widget.get_root() if parent_widget is not None else None
    dialog.present(root)

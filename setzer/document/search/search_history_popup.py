#!/usr/bin/env python3
# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GObject, Adw

from setzer.helpers import animation_prefs


class SearchHistoryPopup(Gtk.Popover):
    """Popover showing search/replace history entries.

    Each entry is an Adw.ActionRow; activating a row emits ``item-selected``
    with the entry text. A per-row suffix delete button (hidden until the
    row is hovered, see ``.search-history-popover`` in style_gtk.css)
    removes a single entry. The header holds the title plus a compact
    ``Clear history`` button; an Adw.StatusPage is shown when empty.

    Size: 400px wide; with entries present the popover keeps a ~520px
    total minimum height via ``set_size_request(400, 460)`` (set in
    ``populate``; the popover adds ~58px of its own padding), so it no
    longer collapses when only a few entries are stored. The height guarantee
    lives on the popover, NOT on the scroller: ``list.boxed-list`` paints the
    card background over the whole ListBox allocation, so giving the scroller
    a min height would stretch the card into the empty area below the rows.
    The surplus space instead falls below the list and shows the plain
    popover background, while the card only wraps the rows.

    The scroller does not clip its child (no ``set_overflow(HIDDEN)``):
    clipping would also cut off the boxed-list's card shadow, leaving hard
    square edges on the sides. When the rows overflow, the rows reach the
    viewport's square corners while scrolling — an accepted trade-off to
    keep the card shadow intact.
    """

    __gsignals__ = {
        'item-selected': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'item-deleted': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.add_css_class('search-history-popover')
        self.set_size_request(400, -1)
        self.set_can_focus(True)

        # 外围留白由 popover 自带 contents padding 提供；内部段间距用 sm=6。
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.set_spacing(6)

        # --- header: title + clear ---
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header.set_spacing(6)

        title = Gtk.Label(label=_('Search history'))
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)
        title.add_css_class('heading')
        header.append(title)

        self._clear_button = Gtk.Button(label=_('Clear history'))
        self._clear_button.add_css_class('flat')
        self._clear_button.add_css_class('destructive-action')
        self._clear_button.set_valign(Gtk.Align.CENTER)
        self._clear_button.connect('clicked', self._on_clear_clicked)
        header.append(self._clear_button)

        content.append(header)

        # --- list ---
        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(True)
        self._listbox.add_css_class('boxed-list')
        self._listbox.connect('row-activated', self._on_row_activated)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # propagate_natural_height：natural 跟随内容（否则 natural 被钉死、
        # max_content_height 永不生效）；max 460 封顶，超出后滚动。
        # 这里不设 min_content_height：保底高度由弹窗自身的 size_request
        # 负责（见类 docstring），否则 ListBox 卡片底色会铺满空区。
        self._scroller.set_propagate_natural_height(True)
        self._scroller.set_max_content_height(460)
        self._scroller.set_child(self._listbox)
        content.append(self._scroller)

        # --- empty state (ui-guidelines「空状态」：Adw.StatusPage，不留白板) ---
        self._empty_status = Adw.StatusPage()
        self._empty_status.add_css_class('compact')
        self._empty_status.set_icon_name('document-open-recent-symbolic')
        self._empty_status.set_title(_('No history'))
        self._empty_status.set_visible(False)
        content.append(self._empty_status)

        self.set_child(content)
        self._clear_callback = None

    def populate(self, items, on_clear=None):
        """Fill the popup with history strings.

        ``items`` is a list of strings (most-recent first). ``on_clear`` is
        called (without arguments) when the user clicks Clear.
        """
        self._clear_callback = on_clear
        self._clear_list()

        # 减弱动画（§8.2 / docs/ui-guidelines.md）：CSS 里 hover 淡入删除按钮的
        # `transition: opacity 150ms` 不走 animation_prefs 登记的 Revealer/Stack
        # 通道，须像 .preview-card 那样按偏好挂 .no-transition 关掉。populate
        # 每次弹出前都跑，偏好中途切换下次打开即生效。
        if animation_prefs.is_reduced():
            self.add_css_class('no-transition')
        else:
            self.remove_css_class('no-transition')

        if not items:
            self._show_empty_state()
            return

        # 有条目时弹窗整体保底 ~520px 总高（请求 460 + popover 自身内边距
        # ~58，实测总高 518）。列表卡片保持自然高，盈余空间落在列表下方、
        # 显示弹窗背景，而非把卡片撑进空区。
        self.set_size_request(400, 460)
        self._scroller.set_visible(True)
        self._clear_button.set_visible(True)
        self._empty_status.set_visible(False)

        for text in items:
            row = self._make_row(text)
            self._listbox.append(row)

    def _show_empty_state(self):
        '''无条目：隐藏列表与 Clear，显示 StatusPage 空状态。'''
        # 空状态内容矮，取消高度保底，避免弹窗下缘留大段空区。
        self.set_size_request(400, -1)
        self._scroller.set_visible(False)
        self._clear_button.set_visible(False)
        self._empty_status.set_visible(True)

    def _clear_list(self):
        child = self._listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._listbox.remove(child)
            child = next_child

    def _make_row(self, text):
        row = Adw.ActionRow()
        row.set_activatable(True)
        # ActionRow 标题自带 ellipsize，无需原 60 字手动截断；
        # 换行符压平为单行显示，row._text 仍保留原文供信号使用。
        row.set_title(text.replace('\n', ' '))

        delete_btn = Gtk.Button()
        delete_btn.set_icon_name('window-close-symbolic')
        delete_btn.add_css_class('flat')
        delete_btn.set_can_focus(True)
        delete_btn.set_tooltip_text(_('Remove from history'))
        delete_btn.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Remove from history')])
        # suffix 盒子默认纵向 fill，会把按钮拉伸到整行高，悬停背景变成
        # 高条；valign=CENTER + 正方形 size_request 让悬停区域是方块。
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.set_size_request(34, 34)
        delete_btn.connect('clicked', self._on_delete_clicked, row)
        row.add_suffix(delete_btn)

        row._text = text
        return row

    def _on_row_activated(self, listbox, row):
        if hasattr(row, '_text'):
            self.emit('item-selected', row._text)
        self.popdown()

    def _on_delete_clicked(self, button, row):
        self.emit('item-deleted', getattr(row, '_text', ''))
        self._listbox.remove(row)
        if self._listbox.get_first_child() is None:
            self._show_empty_state()

    def _on_clear_clicked(self, button):
        if callable(self._clear_callback):
            self._clear_callback()
        self.popdown()

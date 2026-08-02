#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject


class SearchHistoryPopup(Gtk.Popover):
    """Popover showing search/replace history entries.

    Each entry is a clickable row; clicking it emits ``item-selected`` with
    the entry text. A per-row delete button removes a single entry. A
    footer button clears the entire history.
    """

    __gsignals__ = {
        'item-selected': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'item-deleted': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.set_size_request(260, -1)
        self.set_can_focus(True)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._listbox.set_activate_on_single_click(True)
        self._listbox.add_css_class('boxed-list')
        self._listbox.connect('row-activated', self._on_row_activated)

        self._empty_label = Gtk.Label(label=_('No history'))
        self._empty_label.add_css_class('dim-label')
        self._empty_label.set_margin_top(12)
        self._empty_label.set_margin_bottom(12)

        self._clear_button = Gtk.Button(label=_('Clear history'))
        self._clear_button.add_css_class('flat')
        self._clear_button.add_css_class('destructive-action')
        self._clear_button.set_visible(False)
        self._clear_button.connect('clicked', self._on_clear_clicked)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_max_content_height(300)
        self._scroller.set_child(self._listbox)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.append(self._empty_label)
        self._content.append(self._scroller)
        self._content.append(self._clear_button)
        self.set_child(self._content)

        self._clear_callback = None

    def populate(self, items, on_clear=None):
        """Fill the popup with history strings.

        ``items`` is a list of strings (most-recent first). ``on_clear``
        is called (without arguments) when the user clicks Clear.
        """
        self._clear_callback = on_clear
        self._clear_list()

        if not items:
            self._empty_label.set_visible(True)
            self._scroller.set_visible(False)
            self._clear_button.set_visible(False)
            return

        self._empty_label.set_visible(False)
        self._scroller.set_visible(True)
        self._clear_button.set_visible(True)

        for text in items:
            row = self._make_row(text)
            self._listbox.append(row)

    def _clear_list(self):
        child = self._listbox.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._listbox.remove(child)
            child = next_child

    def _make_row(self, text):
        row = Gtk.ListBoxRow()
        row_text = text.replace('\n', ' ')
        if len(row_text) > 60:
            row_text = row_text[:57] + '…'

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(6)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        label = Gtk.Label(label=row_text)
        label.set_halign(Gtk.Align.START)
        label.set_hexpand(True)
        label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        box.append(label)

        delete_btn = Gtk.Button()
        delete_btn.set_icon_name('window-close-symbolic')
        delete_btn.add_css_class('flat')
        delete_btn.set_can_focus(False)
        delete_btn.set_tooltip_text(_('Remove from history'))
        delete_btn.connect('clicked', self._on_delete_clicked, row)
        box.append(delete_btn)

        row.set_child(box)
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
            self._empty_label.set_visible(True)
            self._scroller.set_visible(False)
            self._clear_button.set_visible(False)

    def _on_clear_clicked(self, button):
        if callable(self._clear_callback):
            self._clear_callback()
        self.popdown()

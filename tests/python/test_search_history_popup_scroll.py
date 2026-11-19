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

'''搜索/替换历史弹窗的「圆角卡片蒙版」契约。

契约：历史列表装在垂直滚动、按内容撑高（propagate natural height，
460px 封顶）的 ScrolledWindow 里，并挂 `.list-card-scroll` +
`set_overflow(HIDDEN)`——条目溢出时滚动的行被裁进 boxed-list 的 12px
圆角内，与 Git 侧栏列表同一观感。
'''

import builtins
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

if gtk_ready:
    Adw.init()
    from setzer.document.search.search_history_popup import SearchHistoryPopup


def listbox_rows(listbox):
    rows = 0
    child = listbox.get_first_child()
    while child is not None:
        rows += 1
        child = child.get_next_sibling()
    return rows


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class SearchHistoryPopupScrollTest(unittest.TestCase):

    def setUp(self):
        self.popup = SearchHistoryPopup()

    def tearDown(self):
        if self.popup.get_parent() is not None:
            self.popup.unparent()

    def test_scroller_is_card_masked(self):
        scroller = self.popup._scroller
        self.assertIsInstance(scroller, Gtk.ScrolledWindow)
        self.assertEqual(scroller.get_policy()[1], Gtk.PolicyType.AUTOMATIC)
        self.assertTrue(scroller.get_propagate_natural_height())
        self.assertEqual(scroller.get_max_content_height(), 460)
        self.assertTrue(scroller.has_css_class('list-card-scroll'))
        self.assertEqual(scroller.get_overflow(), Gtk.Overflow.HIDDEN)
        # ScrolledWindow 先包一层 viewport，列表在 viewport 里。
        viewport = scroller.get_child()
        self.assertIsInstance(viewport, Gtk.Viewport)
        self.assertIs(viewport.get_child(), self.popup._listbox)

    def test_populated_list_fits_in_scroller(self):
        self.popup.populate(['term %d' % index for index in range(30)])
        self.assertTrue(self.popup._scroller.get_visible())
        self.assertFalse(self.popup._empty_status.get_visible())
        self.assertEqual(listbox_rows(self.popup._listbox), 30)

    def test_empty_history_hides_scroller(self):
        self.popup.populate([])
        self.assertFalse(self.popup._scroller.get_visible())
        self.assertTrue(self.popup._empty_status.get_visible())


if __name__ == '__main__':
    unittest.main()

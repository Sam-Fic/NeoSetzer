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

'''对话框内列表的滚动容器契约。

契约：参考文献管理器条目列表、崩溃恢复文档列表、Add / Remove Packages 的
包列表都装在 ScrolledWindow 里，容器不做 overflow 圆角裁剪——裁剪会连
boxed-list 的卡片投影一起裁掉（见 docs/dev/gtk-pitfalls.md 3.2）。包列表
的 18px 底部留白挂在滚动容器上（而不是 ListBox），滚到底时卡片下沿与容器
下沿对齐。
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
    from setzer.dialogs.add_remove_packages.add_remove_packages_viewgtk import (
        AddRemovePackagesDialogView)
    from setzer.dialogs.autosave_recovery.autosave_recovery import (
        AutosaveRecoveryDialog)
    from setzer.dialogs.bibliography_manager.bibliography_manager import (
        BibliographyManagerDialog)


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class DialogListScrollTest(unittest.TestCase):

    def assert_scroller(self, scroller, label):
        self.assertIsInstance(scroller, Gtk.ScrolledWindow, label)
        # 不裁剪：overflow 会连 boxed-list 的卡片投影一起裁掉，卡片两侧
        # 只剩硬切直边。
        self.assertEqual(scroller.get_overflow(), Gtk.Overflow.VISIBLE, label)

    def test_add_remove_packages(self):
        view = AddRemovePackagesDialogView(None)
        self.assert_scroller(view.add_scrolled_window, 'add')
        self.assert_scroller(view.remove_scrolled_window, 'remove')
        # 底部留白在滚动容器上，卡片才能铺满滚动区。
        self.assertEqual(view.add_scrolled_window.get_margin_bottom(), 18)
        self.assertEqual(view.remove_scrolled_window.get_margin_bottom(), 18)
        self.assertEqual(view.add_list.get_margin_bottom(), 0)
        self.assertEqual(view.remove_list.get_margin_bottom(), 0)

    def test_autosave_recovery(self):
        dialog = AutosaveRecoveryDialog(None, None)
        dialog.manifest = {
            '/tmp/setzer-recovery-%02d.tex' % index:
                {'displayname': 'doc_%02d.tex' % index,
                 'timestamp': 1700000000 + index}
            for index in range(20)
        }
        dialog.setup()
        scroller = dialog.view.get_extra_child()
        self.assert_scroller(scroller, 'crash recovery')
        self.assertEqual(scroller.get_max_content_height(), 260)
        self.assertTrue(scroller.get_propagate_natural_height())

    def test_bibliography_manager_entries(self):
        dialog = BibliographyManagerDialog(None, None)
        viewport = dialog.entry_list.get_parent()
        self.assertIsInstance(viewport, Gtk.Viewport)
        scroller = viewport.get_parent()
        self.assert_scroller(scroller, 'bibliography entries')
        self.assertTrue(scroller.get_vexpand())


if __name__ == '__main__':
    unittest.main()

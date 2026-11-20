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

'''Git 侧栏两个长列表（改动文件 / 提交历史）的滚动契约。

契约：列表必须待在 vexpand 的 Gtk.ScrolledWindow 里，内容超出面板高度时
vadjustment 出现可滚动范围、窗口变矮时列表被压缩而不是把 Commit 按钮顶出
面板；列表很短时卡片仍只占内容高度（ListBox 设 valign START，避免被
viewport 拉成一张通到面板底部的大卡片）。
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
from gi.repository import Adw, Gdk, GLib, Gtk

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = Gdk.Display.get_default() is not None
except Exception:
    gtk_ready = False

if gtk_ready:
    Adw.init()
    from setzer.workspace.sidebar.git.git_page_viewgtk import GitPageView
    from setzer.workspace.sidebar.git.git_section_viewgtk import GitSectionView


def pump(ms=150):
    '''等一轮布局/绘制：尺寸分配与 vadjustment 更新都在主循环里完成。'''
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def pump_until(predicate, max_ms=3000, step_ms=30):
    '''按步长泵主循环，直到 predicate 为真或超时（返回是否满足）。

    滚动偏移要等 frame clock 跑到布局相位才落到 viewport 子控件的几何上，
    本机（Wayland/niri）实测这一步要 ~1.7s 才稳定发生，固定时长的 pump
    会间歇性赶不上——必须轮询等待，不能用固定 sleep。'''
    elapsed = 0
    while elapsed < max_ms:
        pump(step_ms)
        elapsed += step_ms
        if predicate():
            return True
    return False


def bounds_y(widget, ancestor):
    ok, rect = widget.compute_bounds(ancestor)
    assert ok
    return rect.get_y()


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class GitSidebarScrollTest(unittest.TestCase):

    def setUp(self):
        self.view = GitSectionView()
        self.view.show_repository()
        self.window = Gtk.Window()
        # 固定窗口尺寸：无 WM 的 Xvfb 下窗口按自然宽度开，窄到 ActionRow
        # 换行，行高与「短列表」断言都会漂。
        self.window.set_default_size(420, 700)
        self.window.set_child(self.view)
        self.window.present()
        pump()

    def tearDown(self):
        self.window.destroy()

    def _add_file_rows(self, count):
        self.view.clear_file_rows()
        for index in range(count):
            row, _check, _open, _discard = self.view.build_file_row(
                {'status': ' M', 'path': 'dir/file_%02d.py' % index})
            self.view.append_file_row(row)
        pump()

    def _add_commits(self, count):
        self.view.populate_history([
            {'subject': 'commit %02d' % index, 'hash': 'abcdef%02d' % index,
             'author': 'Sam', 'timestamp': 1700000000 + index}
            for index in range(count)
        ])
        pump()

    def test_lists_live_in_vertical_scrolled_windows(self):
        for attr in ('files_scroller', 'history_scroller'):
            scroller = getattr(self.view, attr)
            self.assertIsInstance(scroller, Gtk.ScrolledWindow)
            self.assertTrue(scroller.get_vexpand())
            self.assertEqual(scroller.get_policy()[1],
                             Gtk.PolicyType.AUTOMATIC)
            self.assertIsNotNone(scroller.get_child())
            # 不做圆角裁剪：overflow 会连 boxed-list 的卡片投影一起裁掉。
            self.assertEqual(scroller.get_overflow(), Gtk.Overflow.VISIBLE)

    def test_overflowing_file_list_can_scroll(self):
        self._add_file_rows(40)
        scroller = self.view.files_scroller
        adjustment = scroller.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        # 溢出也不裁剪：卡片投影由 boxed-list 自己画（见 docs/dev/gtk-pitfalls.md 3.2）。
        self.assertEqual(scroller.get_overflow(), Gtk.Overflow.VISIBLE)
        # 窗口变矮时滚动区被压缩到面板高度，而不是把页面撑出窗口。
        self.assertLess(scroller.get_height(),
                        self.view.files_list.get_height())
        before = bounds_y(self.view.files_list, scroller)
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        # 偏移经 frame clock 布局相位落地（本机实测 ~1.7s），轮询等待。
        self.assertTrue(
            pump_until(
                lambda: bounds_y(self.view.files_list, scroller) < before),
            '滚动偏移 3s 内未应用到布局：bounds_y=%r value=%r upper=%r' % (
                bounds_y(self.view.files_list, scroller),
                adjustment.get_value(), adjustment.get_upper()))

    def test_overflowing_history_can_scroll(self):
        self.view.set_repo_view('commits')
        self._add_commits(40)
        adjustment = self.view.history_scroller.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        self.assertEqual(
            self.view.history_scroller.get_overflow(), Gtk.Overflow.VISIBLE)

    def test_short_list_keeps_natural_card_height(self):
        self._add_file_rows(2)
        # 列表短时卡片只占内容高度，剩余空白由透明 viewport 提供（不铺满）。
        self.assertLess(self.view.files_list.get_height(),
                        self.view.files_scroller.get_height())
        self.assertEqual(
            self.view.files_scroller.get_overflow(), Gtk.Overflow.VISIBLE)

    def test_content_margins_sidebar_edge_line(self):
        '''横向 edge 边距在**视口内部**：页面容器 margin 归零，卡片自己的
        `.git-card-list` 边距 + 同栏控件的 `.git-section-box > *` 边距凑出
        10px 边缘线（官方 PreferencesPage 的 clamp box margin 同构）。底部
        12px 保留：滚到底时卡片下沿不贴边（先例：统计页）。'''
        view = GitSectionView()
        page = GitPageView(view)
        content = page.get_last_child()
        self.assertEqual(content.get_margin_start(), 0)
        self.assertEqual(content.get_margin_end(), 0)
        self.assertEqual(content.get_margin_bottom(), 12)
        self.assertTrue(view.files_list.has_css_class('git-card-list'))
        self.assertTrue(view.history_list.has_css_class('git-card-list'))
        self.assertTrue(view.changes_box.has_css_class('git-section-box'))

    def test_scroller_follows_empty_states(self):
        self._add_file_rows(2)
        self.assertTrue(self.view.files_scroller.get_visible())
        self.view.set_clean()
        self.assertFalse(self.view.files_scroller.get_visible())

        self._add_commits(2)
        self.assertTrue(self.view.history_scroller.get_visible())
        self.view.populate_history([])
        self.assertFalse(self.view.history_scroller.get_visible())


if __name__ == '__main__':
    unittest.main()

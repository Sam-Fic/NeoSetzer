#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''编辑器外围 UI 的最小宽度稳定性（无界面源码测试）。

选中文字时状态栏会显示「N lines selected」、搜索栏的「在选区中搜索」开关会
变为可用。这两处都会改变**编辑器列的最小宽度**；而 Adw.OverlaySplitView 给
侧栏分配宽度时用「总宽 − 内容最小宽」封顶，编辑器列最小宽度一变，预览侧栏
就被压窄，不再跟随设置里的占比——表现为选中文字时预览宽度跳变。

另外 GtkSearchBar 收起时只在竖直方向收起内部 Revealer，水平方向仍按整条
查找行的最小宽度参与布局（默认 1020px 窗口下会把 50% 的预览压到约 39%）。
故本文件锁住三条约束：

1. 状态栏标签使用 ellipsize：最小宽度固定为「…」，不随文本/可见性变化；
2. 搜索栏收起时隐藏整个控件，打开前再显示；
3. 「在选区中搜索」开关常驻占位，只切 sensitive、不切 visible。
'''

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
STATUS_VIEW = ROOT / 'setzer/document/statusbar/statusbar_viewgtk.py'
SEARCH = ROOT / 'setzer/document/search/search.py'
SEARCH_VIEW = ROOT / 'setzer/document/search/search_viewgtk.py'


class TestStatusBarMinWidth(unittest.TestCase):
    def setUp(self):
        self.body = STATUS_VIEW.read_text(encoding='utf-8')

    def test_pango_imported(self):
        self.assertIn('from gi.repository import Gtk, Pango', self.body)

    def test_all_labels_ellipsized(self):
        # info 组 7 个标签（循环内一次）+ 构建结果 + 选区行数
        self.assertGreaterEqual(
            self.body.count('set_ellipsize(Pango.EllipsizeMode.END)'), 3)

    def test_selection_label_ellipsized(self):
        # 选区标签是选中文字时 min 宽度变化的直接来源
        start = self.body.index("self.selection_label = Gtk.Label(label='')")
        end = self.body.index('self.append(self.info_box)')
        self.assertIn('set_ellipsize(Pango.EllipsizeMode.END)',
                      self.body[start:end])


class TestSearchBarMinWidth(unittest.TestCase):
    def setUp(self):
        self.body = SEARCH.read_text(encoding='utf-8')
        self.view_body = SEARCH_VIEW.read_text(encoding='utf-8')

    def test_hidden_at_startup(self):
        append = self.body.index('self.document_view.vbox.append(self.view)')
        self.assertIn('self.view.set_visible(False)', self.body[append:append + 1200])

    def test_shown_before_open(self):
        for name in ('def set_mode_search', 'def set_mode_replace'):
            start = self.body.index(name)
            end = self.body.index('self.view.set_search_mode(True)', start)
            self.assertIn('self._show_search_view()', self.body[start:end])

    def test_hide_deferred_until_animation_ends(self):
        start = self.body.index('def hide_search_bar')
        end = self.body.index('def _show_search_view', start)
        self.assertIn('self._schedule_search_view_hide()', self.body[start:end])
        self.assertIn('GLib.timeout_add(', self.body)

    def test_selection_toggle_only_toggles_sensitivity(self):
        # 显隐会改变查找行的 min 宽度，只允许切 sensitive
        self.assertNotIn('toggle.set_visible', self.body)
        self.assertIn('self.view.selection_toggle.set_sensitive(available)',
                      self.body)
        self.assertNotIn('self.selection_toggle.set_visible(False)',
                         self.view_body)
        self.assertIn('self.selection_toggle.set_sensitive(False)',
                      self.view_body)


if __name__ == '__main__':
    unittest.main()

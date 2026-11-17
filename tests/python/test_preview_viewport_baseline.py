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

'''PDF 预览「视口宽基准」一致性单测。

.preview-card 左右各 6px margin，使 PreviewView.get_allocated_width() 比
真实滚动视口（content.width）宽 12px。页面 margin 的绘制端、点击反算、
fit-to-width 缩放级别、页码徽章水平定位必须全部用同一个视口宽：
- fit-to-width 用 allocated 会让页面宽 12px、溢出视口；
- 页码徽章用 allocated 会让徽章相对页面右边缘偏左 6px。

方法体经 AST 提取 exec，注入轻量桩，不依赖 GTK 显示。
'''

import ast
import os
import sys
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

VIEW_SRC = os.path.join(REPO, 'setzer', 'document', 'preview', 'preview_viewgtk.py')
ZOOM_SRC = os.path.join(REPO, 'setzer', 'document', 'preview', 'preview_zoom_manager.py')

VIEWPORT = 1240      # 真实可视视口宽（content.width）
PAGE_CSS = 1224.0    # 页面 css 宽（= zoom × hidpi × pt）
HIDPI = 2
BADGE_WIDTH = 40


def _load_method(path, class_name, method_name, namespace):
    tree = ast.parse(open(path, encoding='utf-8').read())
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == method_name)
    ns = dict(namespace)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), path, 'exec'), ns)
    return ns[method_name]


class _Layout:
    def __init__(self):
        self.canvas_width = PAGE_CSS + 2 * self.get_horizontal_margin(VIEWPORT)

    @staticmethod
    def get_horizontal_margin(window_width):
        return int(max((window_width - PAGE_CSS) / 2, 0))

    @staticmethod
    def get_page_top(page):
        return 30.0


class FitToWidthBaselineTest(unittest.TestCase):

    def test_fit_to_width_uses_viewport_not_allocated_width(self):
        method = _load_method(ZOOM_SRC, 'PreviewZoomManager', 'update_fit_to_width',
                              {'Gtk': None})
        fake = SimpleNamespace(
            view=SimpleNamespace(get_viewport_width=lambda: VIEWPORT,
                                 get_allocated_width=lambda: VIEWPORT + 12),
            preview=SimpleNamespace(page_width=612.0,
                                    layout=SimpleNamespace(hidpi_factor=HIDPI)))
        method(fake)
        # 页面 css 宽 = zoom × hidpi × pt 应恰好等于视口宽，而不是多 12px。
        page_css = fake.zoom_level_fit_to_width * HIDPI * 612.0
        self.assertAlmostEqual(page_css, VIEWPORT, places=6)


class PageIndicatorBaselineTest(unittest.TestCase):

    def _view(self):
        layout = _Layout()
        content = SimpleNamespace(calls=[])
        content.move_overlay_widget = (
            lambda widget, x, y: content.calls.append((widget, x, y)))
        badges = []
        revealers = []
        for _ in range(2):
            badge = SimpleNamespace(page=None)
            badge.set_page_number = (lambda b: lambda n: setattr(b, 'page', n))(badge)
            badge.measure = lambda orientation, size: (0, BADGE_WIDTH, 0, 0)
            revealer = SimpleNamespace(revealed=None)
            revealer.set_reveal_child = (
                lambda r: lambda v: setattr(r, 'revealed', v))(revealer)
            badges.append(badge)
            revealers.append(revealer)
        return SimpleNamespace(
            content=content,
            _page_indicator_buttons=badges,
            _page_indicator_revealers=revealers,
            get_viewport_width=lambda: VIEWPORT,
            get_allocated_width=lambda: VIEWPORT + 12), layout

    def test_badge_right_edge_follows_drawn_page_edge(self):
        gtk_stub = SimpleNamespace(
            Orientation=SimpleNamespace(HORIZONTAL=0))
        method = _load_method(VIEW_SRC, 'PreviewView',
                              '_update_indicator_button_positions', {'Gtk': gtk_stub})
        view, layout = self._view()
        method(view, [1], layout)
        _, x, y = view.content.calls[0]
        margin = layout.get_horizontal_margin(VIEWPORT)
        # 徽章右边缘 = 页面右边缘 - 10，与 draw() 的 margin 同基准。
        self.assertEqual(x + BADGE_WIDTH, layout.canvas_width - (margin + 10))
        self.assertEqual(y, layout.get_page_top(0) + 10)

    def test_badge_would_shift_6px_if_allocated_width_used(self):
        '''回归护栏：用 allocated（+12）会让 x 偏左 6px。'''
        gtk_stub = SimpleNamespace(
            Orientation=SimpleNamespace(HORIZONTAL=0))
        method = _load_method(VIEW_SRC, 'PreviewView',
                              '_update_indicator_button_positions', {'Gtk': gtk_stub})
        view, layout = self._view()
        method(view, [1], layout)
        _, good_x, _ = view.content.calls[0]
        bad_margin = layout.get_horizontal_margin(VIEWPORT + 12)
        bad_x = layout.canvas_width - (bad_margin + 10) - BADGE_WIDTH
        self.assertEqual(good_x - bad_x, 6)


if __name__ == '__main__':
    unittest.main()

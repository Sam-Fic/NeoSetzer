#!/usr/bin/env python3
# coding: utf-8
#
# 构建按钮里只剩停止图标时的居中。
#
# 构建态按钮的 child 是 [停止图标][计时器] 的 Gtk.Box。计时器隐藏后 Box 的
# 自然宽度只剩 16px 的图标，但按钮受主题约束仍有 24px 最小宽度；Box 默认
# halign=FILL 会被拉到 24px，图标于是贴在左侧（左边 0、右边 8，偏 4px）。
# 让 Box 居中收缩即可。

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / 'setzer/document/build_widget/build_widget_viewgtk.py'


class TimerHiddenIconCenteringTest(unittest.TestCase):

    def test_active_box_is_centered_so_it_can_shrink_to_the_icon(self):
        body = VIEW.read_text(encoding='utf-8')
        created = body.index('self.active_box = Gtk.Box(')
        first_append = body.index('self.active_box.append(self.active_icon)')
        self.assertIn('self.active_box.set_halign(Gtk.Align.CENTER)',
                      body[created:first_append])


if __name__ == '__main__':
    unittest.main()

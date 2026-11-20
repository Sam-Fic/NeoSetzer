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

'''ShortcutsBar overflow 连续 reflow 的真实 GTK 回归测试。

契约：窗口缩放（不经过 update_buttons / request_reflow）时，overflow 数量
必须随分配宽度连续重算，且任何可见按钮都不被挤压裁切。

历史回归：reflow 钩子曾写在 ShortcutsBar.do_size_allocate 里，而该 widget
设置了 layout manager（_NoNaturalWidthLayout）——GTK4 在有 layout manager
时跳过 widget 的 size_allocate vfunc，钩子从未执行：窗口缩放不收按钮，
超出的按钮被裁一半（2026-11-20 修复：钩子移入 layout manager do_allocate）。
本测试若再遇到该回归，会以「缩放后 ovf 不变」失败。'''

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
    from setzer.popovers.popover_manager import PopoverManager
    from setzer.workspace.shortcutsbar.shortcutsbar_viewgtk import ShortcutsBar


def pump(ms=60):
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class ShortcutsBarOverflowReflowTest(unittest.TestCase):

    def setUp(self):
        # ShortcutsBar 构造会经 PopoverManager.create_popover('context_menu')
        # 建右键菜单（需要 workspace）。测试里换成裸 Popover 桩。
        self._orig_create_popover = PopoverManager.create_popover

        class _StubPopover:
            def __init__(self):
                self.view = Gtk.Popover()

        PopoverManager.create_popover = staticmethod(lambda name: _StubPopover())
        self.addCleanup(self._restore_popover_manager)

        # shortcut_tooltips 惰性取 ServiceLocator 的 settings（测试环境未初始化），
        # 预置无操作桩并清理。
        from setzer.keyboard_shortcuts import shortcut_tooltips
        self._shortcut_tooltips = shortcut_tooltips
        self._orig_settings = shortcut_tooltips._settings

        class _StubSettings:
            def connect(self, *a):
                pass

            def get_value(self, *a):
                return None

        shortcut_tooltips._settings = _StubSettings()
        self.addCleanup(self._restore_tooltip_settings)

        self.win = Adw.Window()
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.bar = ShortcutsBar()
        wrapper.append(self.bar)
        filler = Gtk.Box()
        filler.set_vexpand(True)
        wrapper.append(filler)
        self.win.set_content(wrapper)
        self.win.present()
        pump(120)
        pump(120)
        self.addCleanup(self._teardown_window)

    def _restore_popover_manager(self):
        PopoverManager.create_popover = self._orig_create_popover

    def _restore_tooltip_settings(self):
        self._shortcut_tooltips._settings = self._orig_settings

    def _teardown_window(self):
        self.win.destroy()

    def _set_width(self, width):
        self.win.set_default_size(width, 320)
        pump(60)
        pump(60)

    def test_overflow_follows_window_resize_without_reflow_calls(self):
        '''核心回归：纯窗口缩放（无 update_buttons/request_reflow）也要重算。'''
        self._set_width(760)
        self.assertEqual(self.bar._overflow_count, 0, '宽窗口应全部内联显示')

        self._set_width(430)
        self.assertGreaterEqual(
            self.bar._overflow_count, 3,
            '窗口收窄后必须收进 overflow（历史回归：do_size_allocate 被 '
            'layout manager 路径跳过，缩放期间 ovf 恒为 0）')

        self._set_width(760)
        self.assertEqual(self.bar._overflow_count, 0, '窗口放宽后应恢复全部内联')

    def test_overflow_menu_mirrors_hidden_buttons(self):
        '''收进菜单的按钮数与 Gio.Menu 条目数一致（延迟重建 pump 后生效）。'''
        self._set_width(430)
        pump(60)
        self.assertEqual(
            self.bar._overflow_model.get_n_items(), self.bar._overflow_count)

    def test_no_visible_button_squeezed_when_narrow(self):
        '''窄窗口下所有可见按钮的实际占宽不小于其测量宽度（无裁切挤压）。'''
        self._set_width(430)
        for btn in self.bar.left_buttons + [self.bar.button_search,
                                            self.bar.button_build_log,
                                            self.bar.button_more]:
            if not btn.get_visible():
                continue
            _mn, nat, _b1, _b2 = btn.measure(Gtk.Orientation.HORIZONTAL, -1)
            alloc = btn.get_allocation()
            footprint = alloc.width + btn.get_margin_start() + btn.get_margin_end()
            self.assertGreaterEqual(
                footprint, nat,
                '按钮被挤压裁切：footprint=%s < measure=%s' % (footprint, nat))

    def test_reflow_threshold_keeps_wide_window_fully_inline(self):
        '''远超内容宽度时不得收起（激进档位不能把宽窗口也收了）。'''
        self._set_width(1000)
        self.assertEqual(self.bar._overflow_count, 0)


if __name__ == '__main__':
    unittest.main()

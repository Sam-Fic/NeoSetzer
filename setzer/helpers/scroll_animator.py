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

import time

from gi.repository import GObject


class ScrollAnimatorMixin:
    '''section 导航滚动动画的共享逻辑。

    DocumentStructurePage 与 SymbolsPage 有逐行相同的 scroll_view / do_scroll /
    ease / _scroll_timeout_id 管理（约 30 行），提取为 mixin 消除重复，避免
    两处行为漂移（如动画时长、缓动函数修改时只改一处）。

    子类需：
      - 在 __init__ 中初始化 ``self.scroll_to = None`` 与
        ``self._scroll_timeout_id = None``
      - 实现 ``_get_scrolled_window()`` 返回用于滚动的 Gtk.ScrolledWindow
      - 在 ``_on_destroy`` 中调用 ``self._cancel_scroll_animation()`` 取消
        进行中的动画 timeout（两个子类各自还有其他 idle 需清理，故不把
        整个 _on_destroy 提到 mixin）
    '''

    def _get_scrolled_window(self):
        raise NotImplementedError

    def scroll_view(self, position, duration=0.2):
        # 取消进行中的动画：连续点击下一/上一段时，旧 timeout 仍在写
        # adjustment，与新动画叠加产生抖动。
        if self._scroll_timeout_id is not None:
            GObject.source_remove(self._scroll_timeout_id)
            self._scroll_timeout_id = None
        scrolled_window = self._get_scrolled_window()
        adjustment = scrolled_window.get_vadjustment()
        self.scroll_to = {'position_start': adjustment.get_value(), 'position_end': position, 'time_start': time.time(), 'duration': duration}
        scrolled_window.set_kinetic_scrolling(False)
        self._scroll_timeout_id = GObject.timeout_add(15, self.do_scroll)

    def do_scroll(self):
        if self.scroll_to != None:
            scrolled_window = self._get_scrolled_window()
            adjustment = scrolled_window.get_vadjustment()
            time_elapsed = time.time() - self.scroll_to['time_start']
            if self.scroll_to['duration'] == 0:
                time_elapsed_percent = 1
            else:
                time_elapsed_percent = time_elapsed / self.scroll_to['duration']
            if time_elapsed_percent >= 1:
                adjustment.set_value(self.scroll_to['position_end'])
                self.scroll_to = None
                scrolled_window.set_kinetic_scrolling(True)
                self._scroll_timeout_id = None
                return False
            else:
                adjustment.set_value(self.scroll_to['position_start'] * (1 - self.ease(time_elapsed_percent)) + self.scroll_to['position_end'] * self.ease(time_elapsed_percent))
                return True
        # scroll_to 已被取消（新动画或销毁），停止本 timeout。
        self._scroll_timeout_id = None
        return False

    def ease(self, time):
        return (time - 1)**3 + 1

    def _cancel_scroll_animation(self):
        '''取消进行中的滚动动画 timeout。供子类 _on_destroy 调用。'''
        if self._scroll_timeout_id is not None:
            try:
                GObject.source_remove(self._scroll_timeout_id)
            except (ValueError, RuntimeError):
                pass
            self._scroll_timeout_id = None

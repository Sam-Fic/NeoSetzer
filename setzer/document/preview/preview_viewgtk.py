#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Adw

from setzer.widgets.scrolling_widget.scrolling_widget import ScrollingWidget


class PreviewView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class('preview')

        self.content = ScrollingWidget()
        self.drawing_area = self.content.content

        self.blank_slate = BlankSlateView()

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self.blank_slate, 'blank_slate')
        self.stack.add_named(self.content.view, 'pdf')

        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.overlay.set_child(self.stack)
        self.append(self.overlay)

        self.target_label = Gtk.Label()
        self.target_label.set_halign(Gtk.Align.START)
        self.target_label.set_valign(Gtk.Align.END)
        self.target_label.set_can_target(False)
        self.overlay.add_overlay(self.target_label)
        # 缓存上次的 link_target_string：update_cursor 在每次滚动/鼠标移动时
        # 都调 set_link_target_string，绝大多数调用 target_string 不变（光标
        # 仍在同一链接/无链接区域）。set_text + set_visible 即使值相同也会
        # 经 GTK 属性通知链；签名比对为 O(1) 字符串比较，省去无谓的 widget 更新。
        self._current_link_target = None
        self.set_link_target_string('')

    def set_layout_data(self, layout_data):
        self.layout_data = layout_data

    def set_link_target_string(self, target_string):
        if target_string != self._current_link_target:
            self._current_link_target = target_string
            self.target_label.set_text(target_string)
            self.target_label.set_visible(target_string != '')


def BlankSlateView():
    '''Preview empty-state placeholder.

    Returns a compact Adw.StatusPage. Adw.StatusPage 是 final 类型无法子类化，
    故以工厂函数返回实例，替代原手绘 Gtk.Box + Gtk.Image + Gtk.Label 布局。'''
    page = Adw.StatusPage()
    page.add_css_class('compact')
    page.set_icon_name('document-properties-symbolic')
    page.set_title(_('No preview available'))
    page.set_description(_('To show a .pdf preview of your document, click the build button in the headerbar.'))
    return page



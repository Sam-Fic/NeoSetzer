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

"""圆角文本输入卡片：多行文本输入框的统一外观。

AI 批注 popover 与「发送到 AI Agent」弹窗的 prompt 编辑区共用同一套
「灰底 + 1px 边框 + 8px 圆角 + 内缩文字」的输入框外观，避免各处各写
一份 CSS 导致观感漂移（旧 popover 直接给 ScrolledWindow 挂 .card，
TextView 自身背景是直角方块，会盖住圆角）。

用法::

    scrolled = create_text_input_card(text_view, min_height=80, max_height=160)
    box.append(scrolled)

返回值就是包裹 TextView 的 Gtk.ScrolledWindow，调用方负责放进布局。
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk


VIEW_CSS_CLASS = 'setzer-text-input'
SCROLL_CSS_CLASS = 'setzer-text-input-scroll'

_CSS = '''
textview.setzer-text-input {
    background-color: @card_bg_color;
    border: 1px solid @borders;
    border-radius: 8px;
}
/* overshoot/undershoot 提示条画在 ScrolledWindow 边缘、不受子件 overflow
   裁剪，滚动时会溢出圆角外框，因此整体关掉——圆角卡片本身已足够提示
   这是可滚动区域。 */
scrolledwindow.setzer-text-input-scroll overshoot,
scrolledwindow.setzer-text-input-scroll undershoot {
    background-image: none;
    background-color: transparent;
    border: none;
    box-shadow: none;
}
'''

_css_provider = None


def _ensure_css():
    '''把卡片样式注册到 display（全局只注册一次）。

    display 级 provider 重复 add 会堆积，因此用模块级单例守卫；display
    尚未可用（无 GUI 环境单测）时直接跳过，控件仍可构建，只是没有边框。
    '''
    global _css_provider
    if _css_provider is not None:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    if hasattr(provider, 'load_from_string'):  # GTK 4.12+，无弃用警告
        provider.load_from_string(_CSS)
    else:
        provider.load_from_data(_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_provider = provider


def create_text_input_card(text_view, min_height=None, max_height=None,
                           min_width=None, hexpand=False, vexpand=False,
                           text_margin=12, hscroll=False):
    '''用圆角卡片外框包住 text_view，返回外层 ScrolledWindow。

    可编辑输入与只读预览（LaTeX 预览、BibTeX 详情等）共用同一外观。

    Args:
        text_view: 待包装的 Gtk.TextView。
        min_height: ScrolledWindow 最小内容高度（None / 0 = 不设）。
        max_height: ScrolledWindow 最大内容高度（None / 0 = 不设）。
        min_width: ScrolledWindow 最小内容宽度（None / 0 = 不设）。
        hexpand / vexpand: 外框是否随容器扩展。
        text_margin: 文字与边框之间的内缩（四边一致），默认 12。
        hscroll: 是否允许横向滚动（WrapMode.NONE 的代码预览需要）。
    '''
    _ensure_css()

    for setter in (text_view.set_left_margin, text_view.set_right_margin,
                   text_view.set_top_margin, text_view.set_bottom_margin):
        setter(text_margin)
    text_view.add_css_class(VIEW_CSS_CLASS)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(
        Gtk.PolicyType.AUTOMATIC if hscroll else Gtk.PolicyType.NEVER,
        Gtk.PolicyType.AUTOMATIC)
    # 把 TextView 的背景裁到圆角内，否则直角背景会盖住外框的四个角。
    scrolled.set_overflow(Gtk.Overflow.HIDDEN)
    scrolled.set_hexpand(hexpand)
    scrolled.set_vexpand(vexpand)
    if min_width:
        scrolled.set_min_content_width(min_width)
    if min_height:
        scrolled.set_min_content_height(min_height)
    if max_height:
        scrolled.set_max_content_height(max_height)
    scrolled.add_css_class(SCROLL_CSS_CLASS)
    scrolled.set_child(text_view)
    return scrolled

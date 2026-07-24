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
# along with this program; if not, write to the <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


# 悬停预览中放大符号的像素尺寸（侧边栏列表里仅约 16px，放大便于看清细节）。
PREVIEW_PIXEL_SIZE = 56


def attach_symbol_hover_preview(button, symbol, folder=None,
                                 favorite_state_func=None, favorite_toggle_func=None):
    '''为符号按钮挂载 hover 预览 Popover：放大版符号 + LaTeX 命令（+ 包名）。

    symbol 结构为 [icon_name_suffix, command, package, w, h, ...]，与
    SidebarSymbolsList 及 recent 列表保持一致。鼠标进入时弹出，离开时收起。

    folder 为该符号所属分类目录（如 'greek_letters'），用于收藏时标识。
    favorite_state_func(folder, command) -> bool：当前是否已收藏。
    favorite_toggle_func(folder, command)：切换收藏状态（由 SymbolsPage 实现，
    内部负责刷新 Favorites 列表）。二者均提供时，Popover 内显示收藏按钮。
    '''
    popover = Gtk.Popover()
    popover.set_has_arrow(True)
    popover.set_autohide(True)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.set_spacing(6)
    box.set_margin_top(10)
    box.set_margin_bottom(10)
    box.set_margin_start(10)
    box.set_margin_end(10)

    image = Gtk.Image(icon_name='sidebar-' + symbol[0] + '-symbolic')
    image.set_pixel_size(PREVIEW_PIXEL_SIZE)
    image.set_halign(Gtk.Align.CENTER)
    box.append(image)

    command_label = Gtk.Label(label=symbol[1])
    command_label.set_selectable(True)
    command_label.add_css_class('monospace')
    box.append(command_label)

    if symbol[2] is not None:
        package_label = Gtk.Label(label=_('Package') + ': ' + symbol[2])
        package_label.add_css_class('dim-label')
        box.append(package_label)

    favorite_button = None
    if folder is not None and favorite_state_func is not None and favorite_toggle_func is not None:
        favorite_button = Gtk.Button()
        favorite_button.add_css_class('flat')
        command = symbol[1]

        def refresh_favorite_label():
            if favorite_state_func(folder, command):
                favorite_button.set_label(_('★ Remove from Favorites'))
            else:
                favorite_button.set_label(_('☆ Add to Favorites'))

        def on_favorite_clicked(btn):
            favorite_toggle_func(folder, command)
            refresh_favorite_label()

        refresh_favorite_label()
        favorite_button.connect('clicked', on_favorite_clicked)
        box.append(favorite_button)

    popover.set_child(box)

    def on_enter(controller, x, y):
        popover.set_parent(button)
        popover.popup()

    def on_leave(controller):
        popover.popdown()

    motion = Gtk.EventControllerMotion()
    motion.connect('enter', on_enter)
    motion.connect('leave', on_leave)
    button.add_controller(motion)

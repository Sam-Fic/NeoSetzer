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
from gi.repository import Gtk, Gdk, Gio


# 悬停预览中放大符号的像素尺寸（侧边栏列表里仅约 16px，放大便于看清细节）。
PREVIEW_PIXEL_SIZE = 56


def attach_symbol_hover_preview(button, symbol):
    '''为符号按钮挂载 hover 预览 Popover：放大版符号 + LaTeX 命令（+ 包名）。

    symbol 结构为 [icon_name_suffix, command, package, w, h, ...]，与
    SidebarSymbolsList 及 recent 列表保持一致。鼠标进入时弹出，离开时收起。

    收藏操作已统一收进右键上下文菜单（见 attach_symbol_context_menu），
    hover 预览只负责展示，不再带收藏按钮。

    Popover 懒创建：主列表约 10 个分类 × 每分类数十到上百符号 = 数百到上千
    按钮，原实现为每个按钮立即构造完整 Popover + Image + Label (+ Button)，
    绝大多数永不被打开却常驻内存并参与样式匹配/a11y 注册，拖慢启动。改为
    首次 hover 时按需构造并缓存于 button._hover_popover，后续 hover 复用。
    启动时数百个 Popover 的构造降为 0，运行时仅按需创建用户实际悬停的符号。
    '''
    def _build_popover():
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

        popover.set_child(box)
        return popover

    def on_enter(controller, x, y):
        popover = getattr(button, '_hover_popover', None)
        if popover is None:
            popover = _build_popover()
            button._hover_popover = popover
        refresh = getattr(popover, '_refresh_favorite_label', None)
        if refresh is not None:
            refresh()
        if popover.get_parent() is None:
            popover.set_parent(button)
        popover.popup()

    def on_leave(controller):
        popover = getattr(button, '_hover_popover', None)
        if popover is not None:
            popover.popdown()

    motion = Gtk.EventControllerMotion()
    motion.connect('enter', on_enter)
    motion.connect('leave', on_leave)
    button.add_controller(motion)


def attach_symbol_context_menu(button, symbol, folder=None,
                                insert_func=None, copy_func=None,
                                favorite_state_func=None, favorite_toggle_func=None):
    '''为符号按钮挂载右键上下文菜单：Insert / Add to Favorites / Copy LaTeX Command。

    symbol 同 attach_symbol_hover_preview：[icon_name_suffix, command, package, w, h, ...]。
    folder 为该符号所属分类目录，用于插入时记最近、以及对收藏状态（最近/收藏列表中的符号
    同样携带其原始分类）的判断。

    insert_func(folder, command)：执行「插入」操作（与左键一致：插入 + 记最近）。
    copy_func(command)：把 LaTeX 命令复制到剪贴板。
    favorite_state_func(folder, command) -> bool / favorite_toggle_func(folder, command)：
    二者齐备时菜单显示「Add/Remove from Favorites」项，并按当前状态切换文案。
    '''
    command = symbol[1]

    action_group = Gio.SimpleActionGroup()
    button.insert_action_group('symbol-context', action_group)

    def on_insert(action, param):
        if insert_func is not None:
            insert_func(folder, command)

    def on_copy(action, param):
        if copy_func is not None:
            copy_func(command)

    def on_favorite(action, param):
        if folder is not None and favorite_toggle_func is not None:
            favorite_toggle_func(folder, command)

    insert_action = Gio.SimpleAction.new('insert', None)
    insert_action.connect('activate', on_insert)
    action_group.add_action(insert_action)

    copy_action = Gio.SimpleAction.new('copy', None)
    copy_action.connect('activate', on_copy)
    action_group.add_action(copy_action)

    if folder is not None and favorite_state_func is not None and favorite_toggle_func is not None:
        favorite_action = Gio.SimpleAction.new('favorite', None)
        favorite_action.connect('activate', on_favorite)
        action_group.add_action(favorite_action)

    def build_menu_model():
        menu = Gio.Menu()
        menu.append(_('Insert'), 'symbol-context.insert')
        if favorite_state_func is not None and folder is not None:
            if favorite_state_func(folder, command):
                menu.append(_('★ Remove from Favorites'), 'symbol-context.favorite')
            else:
                menu.append(_('☆ Add to Favorites'), 'symbol-context.favorite')
        menu.append(_('Copy LaTeX Command'), 'symbol-context.copy')
        return menu

    gesture = Gtk.GestureClick()
    gesture.set_button(Gdk.BUTTON_SECONDARY)

    def on_pressed(gesture, n_press, x, y):
        # 每次右键重建菜单模型，以刷新「收藏」项的文案（状态可能在别处被切换）。
        popover = getattr(button, '_context_menu', None)
        if popover is None:
            popover = Gtk.PopoverMenu()
            popover.set_parent(button)
            popover.set_has_arrow(True)
            button._context_menu = popover
        popover.set_menu_model(build_menu_model())
        popover.popup()

    gesture.connect('pressed', on_pressed)
    button.add_controller(gesture)

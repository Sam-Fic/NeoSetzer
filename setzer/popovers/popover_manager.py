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
from gi.repository import Gtk

from setzer.popovers.hamburger_menu.hamburger_menu import HamburgerMenu
from setzer.popovers.preview_zoom_level.preview_zoom_level import PreviewZoomLevel
from setzer.popovers.context_menu.context_menu import ContextMenu


class PopoverManager():
    '''Registry for the app's popovers.

    Migrated off the hand-built overlay/inbetween/PopoverButton framework.
    Popovers are now standard Gtk.Popover widgets attached to Gtk.MenuButtons.
    This class retains only:
      - init(): store main_window/workspace + pre-create the popovers used via
        get_popover() by the headerbar (new_document / open_document /
        document_switcher).
      - create_popover(name): idempotent lazy factory for hamburger_menu /
        preview_zoom_level / context_menu. It also wires the popover's
        map/closed signals to the 'popup'/'popdown' change codes so that
        Shortcuts can pause the app shortcut controller while a popover is open.
      - get_popover(name): lookup.
      - add_change_code/connect/disconnect: tiny observer bus.
    '''

    popovers = dict()
    main_window = None
    workspace = None

    connected_functions = dict()  # observers' functions called on change codes

    def init(main_window, workspace):
        PopoverManager.main_window = main_window
        PopoverManager.workspace = workspace

        # Pre-create the popovers referenced through get_popover() by the
        # headerbar (open-doc / new-doc / document-switcher buttons).
        from setzer.popovers.new_document.new_document import NewDocument
        from setzer.popovers.document_chooser.document_chooser import DocumentChooser
        from setzer.popovers.document_switcher.document_switcher import DocumentSwitcher
        PopoverManager.popovers['new_document'] = NewDocument()
        PopoverManager.popovers['open_document'] = DocumentChooser(PopoverManager.workspace)
        PopoverManager.popovers['document_switcher'] = DocumentSwitcher(PopoverManager.workspace)

    def get_popover(name):
        return PopoverManager.popovers.get(name)

    def create_popover(name):
        # Idempotent: several call sites (shortcutsbar, workspace context_menu,
        # actions) request the same popover instance.
        if name in PopoverManager.popovers:
            return PopoverManager.popovers[name]

        popover = None
        if name == 'hamburger_menu':
            popover = HamburgerMenu(PopoverManager.workspace)
        elif name == 'preview_zoom_level':
            popover = PreviewZoomLevel(PopoverManager, PopoverManager.workspace)
        elif name == 'context_menu':
            popover = ContextMenu(PopoverManager, PopoverManager.workspace)

        if popover is not None:
            PopoverManager.popovers[name] = popover
            # Wire map/closed to the popup/popdown change codes for popovers
            # that pause the app shortcut controller (context_menu /
            # preview_zoom_level). hamburger_menu is triggered natively via a
            # Gtk.MenuButton and historically did not emit change codes.
            if name in ('context_menu', 'preview_zoom_level'):
                popover_widget = popover.view
                popover_widget.connect('map', lambda w: PopoverManager.add_change_code('popup', name))
                popover_widget.connect('closed', lambda w: PopoverManager.add_change_code('popdown', name))

        return popover

    def add_change_code(change_code, parameter=None):
        # 与 Observable.add_change_code 对齐：迭代前 list() 拷贝回调集合，避免
        # 某回调内 connect/disconnect 同一 change_code 触发
        # `RuntimeError: Set changed size during iteration` 吞掉后续回调。
        # `is not None` 替代 `!= None`：避免 parameter 为重载 __eq__ 的对象
        # （如 GVariant）时误判及 __eq__ 自身开销。
        callbacks = PopoverManager.connected_functions.get(change_code)
        if not callbacks:
            return
        for callback in list(callbacks):
            if parameter is not None:
                callback(parameter)
            else:
                callback()

    def connect(change_code, callback):
        if change_code in PopoverManager.connected_functions:
            PopoverManager.connected_functions[change_code].add(callback)
        else:
            PopoverManager.connected_functions[change_code] = {callback}

    def disconnect(change_code, callback):
        if change_code in PopoverManager.connected_functions:
            PopoverManager.connected_functions[change_code].discard(callback)
            if len(PopoverManager.connected_functions[change_code]) == 0:
                del(PopoverManager.connected_functions[change_code])

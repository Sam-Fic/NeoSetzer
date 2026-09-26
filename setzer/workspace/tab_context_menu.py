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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, Gtk

from setzer.app.service_locator import ServiceLocator


class TabContextMenu(object):
    '''标签条（Adw.TabBar）右键菜单。

    菜单对「指针下的标签」生效。libadwaita 没有公开的 per-tab 命中测试 API，
    这里利用 TabBar 内部稳定结构反查：TabBar 的内容是两个 ScrolledWindow
    （固定区带 css class 'pinned'）各包一个 AdwTabBox，每个标签是盒内的一个
    tabboxchild，且盒内 tabboxchild 的排列顺序与该组页面（按页序过滤后）的
    顺序一致。因此：pick 取指针下最深控件 → 沿祖先链找 tabboxchild → 算出
    它在所属盒内的序号 → 对回该组页面的页序。任何一步与预期结构不符都回退
    为当前选中页——宁可作用于可见的选中标签，也不让菜单失效或崩溃。

    菜单项是 'win.pin-tab' / 'win.close-tab'（actions.py 注册），目标文档由
    get_target_document() 提供：菜单打开期间为右键命中的文档，菜单关闭即清
    空——快捷键等无菜单路径由 actions 回退到当前活跃文档。
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self._target_document = None

        # 父控件固定为标签条：pointing-to 坐标与 GestureClick 的 x/y 同属
        # document_tabs 坐标系，无需像编辑器右键菜单那样在弹出时换父。
        self.popover = Gtk.PopoverMenu()
        self.popover.set_has_arrow(False)
        self.popover.set_parent(self.main_window.document_tabs)
        self.popover.connect('closed', self.on_popover_closed)

        # 只监听副键：主键 press/released 已由 WorkspacePresenter 的
        # release-sync 手势处理，拖拽排序走 libadwaita 内部主键手势，
        # 副键手势不参与序列竞争，不会复现 tab-reorder 问题。
        self.click = Gtk.GestureClick()
        self.click.set_button(Gdk.BUTTON_SECONDARY)
        self.click.connect('pressed', self.on_tab_bar_right_click)
        self.main_window.document_tabs.add_controller(self.click)

    def get_target_document(self):
        '''右键菜单当前作用的目标文档；菜单未打开时为 None。'''
        return self._target_document

    def on_popover_closed(self, popover):
        self._target_document = None

    def on_tab_bar_right_click(self, gesture, n_press, x, y):
        if n_press != 1:
            return
        page = self._get_page_at_coords(x, y)
        if page is None:
            return
        document = self.workspace.presenter._page_to_doc.get(page)
        if document is None:
            return
        self._target_document = document
        self.popover.set_menu_model(self._build_menu_model(
            self.workspace.is_document_pinned(document)))
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def _build_menu_model(self, pinned):
        '''按目标文档的固定状态建菜单：固定/取消固定共用 win.pin-tab，
        标签文案在弹出时确定（Gio.Menu 不支持动态文案）。'''
        section = Gio.Menu()
        if pinned:
            section.append(_('Unpin Tab'), 'win.pin-tab')
        else:
            section.append(_('Pin Tab'), 'win.pin-tab')
        section.append(_('Close Tab'), 'win.close-tab')
        model = Gio.Menu()
        model.append_section(None, section)
        return model

    def _get_page_at_coords(self, x, y):
        '''标签条坐标 → Adw.TabPage。结构不符/异常时回退当前选中页。'''
        view = self.main_window.document_stack
        try:
            widget = self.main_window.document_tabs.pick(x, y, Gtk.PickFlags.DEFAULT)
            while widget is not None and widget.get_css_name() != 'tabboxchild':
                widget = widget.get_parent()
            if widget is None:
                return view.get_selected_page()
            index = self._tabboxchild_index(widget)
            is_pinned = self._is_in_pinned_box(widget)
            # get_nth_page 按页序排列；固定页排在前段且组内保持相对顺序，
            # 按是否固定过滤即得两个盒子各自的页面顺序。
            ordered = [view.get_nth_page(i) for i in range(view.get_n_pages())]
            group = [p for p in ordered
                     if bool(p.get_property('pinned')) == is_pinned]
            if index is not None and 0 <= index < len(group):
                return group[index]
        except Exception:
            pass
        return view.get_selected_page()

    @staticmethod
    def _tabboxchild_index(tabboxchild):
        '''tabboxchild 在所属盒内的序号（按同级 tabboxchild 计数）。'''
        parent = tabboxchild.get_parent()
        if parent is None:
            return None
        index = 0
        child = parent.get_first_child()
        while child is not None:
            if child is tabboxchild:
                return index
            if child.get_css_name() == 'tabboxchild':
                index += 1
            child = child.get_next_sibling()
        return None

    @staticmethod
    def _is_in_pinned_box(widget):
        '''沿祖先链找包着标签盒的 ScrolledWindow，看是否带 'pinned' class。'''
        ancestor = widget.get_parent()
        while ancestor is not None:
            if isinstance(ancestor, Gtk.ScrolledWindow) and 'pinned' in ancestor.get_css_classes():
                return True
            ancestor = ancestor.get_parent()
        return False

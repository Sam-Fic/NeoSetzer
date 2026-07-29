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
from gi.repository import Gdk, Gtk

from setzer.app.font_manager import FontManager
from setzer.popovers.popover_manager import PopoverManager


class ContextMenu(object):
    '''Workspace right-click (secondary-button) context menu for documents.

    popover_pointer 与 F12「更多」弹出菜单（PopoverManager 'context_menu' /
    ContextMenuView）共享同一份 ``Gio.Menu`` model，因此右键菜单与 F12 菜单
    样式与内容完全一致——均为原生 ``Gtk.PopoverMenu``（同汉堡菜单），而非早先
    的 AdwPopoverMenu（Gtk.Popover + ListBox + boxed-list）。LaTeX-only section
    由 ContextMenuView.rebuild_latex_section() 重建，对两个 popover 同时生效
    （共享 model 中的 latex_section 引用）。zoom 控件是每个 popover 各自的
    custom child（各持一个 reset 按钮，标签由 actions.py 分别更新），因为
    Gtk.PopoverMenu.add_child 注册的 custom widget 是 per-popover 的。
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.document = None

        # The shortcutsbar "more" popover (F12). Also the source of the shared
        # Gio.Menu model — actions.py updates its reset_zoom_button on zoom.
        self.popover_more = PopoverManager.create_popover('context_menu')

        # 右键 popover：原生 Gtk.PopoverMenu，共享 F12 菜单的 model。
        # 样式（菜单项排版、快捷键标签、分隔线、zoom 行）与 F12 完全一致。
        # 定位见 popup_at_cursor：标准上下文菜单——光标落在 popover 的一个角上
        # （默认左上角，靠近窗口边界时 GTK 自动翻转/贴边切到右上/左下/右下角）。
        self.popover_pointer = Gtk.PopoverMenu()
        self.popover_pointer.set_size_request(288, -1)
        self.popover_pointer.set_has_arrow(False)
        self.popover_pointer.set_menu_model(self.popover_more.view.model)
        self.popover_pointer.add_child(self._build_zoom_widget(), 'zoom-controls')
        self.popover_pointer.connect('map', self.on_popover_map)

        self.workspace.connect('new_active_document', self.on_new_active_document)

    def _build_zoom_widget(self):
        '''右键 popover 自己的 zoom 控件行。与 ContextMenuView._build_zoom_widget
        结构一致（label + 减/重置/增），但 reset 按钮引用存为 reset_zoom_button_pointer，
        供 actions.py 在缩放变化时更新标签。'''
        box = Gtk.CenterBox()
        box.set_orientation(Gtk.Orientation.HORIZONTAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        zoom_label = Gtk.Label(label=_('Zoom'))
        box.set_start_widget(zoom_label)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        button_zoom_out = Gtk.Button()
        button_zoom_out.set_icon_name('value-decrease-symbolic')
        button_zoom_out.add_css_class('flat')
        button_zoom_out.set_action_name('win.zoom-out')
        inner_box.append(button_zoom_out)

        self.reset_zoom_button_pointer = Gtk.Button.new_with_label("{:.0%}".format(FontManager.zoom_level))
        self.reset_zoom_button_pointer.add_css_class('flat')
        self.reset_zoom_button_pointer.set_action_name('win.reset-zoom')
        inner_box.append(self.reset_zoom_button_pointer)

        button_zoom_in = Gtk.Button()
        button_zoom_in.set_icon_name('value-increase-symbolic')
        button_zoom_in.add_css_class('flat')
        button_zoom_in.set_action_name('win.zoom-in')
        inner_box.append(button_zoom_in)

        box.set_end_widget(inner_box)
        return box

    def on_new_active_document(self, workspace=None, parameter=None):
        # LaTeX section 的显隐由 ContextMenuView.rebuild_latex_section 处理
        # （popovers/context_menu 的 on_new_active_document 回调），对共享 model
        # 生效，本 popover 自动反映。此处只更新 document 引用供 popup_at_cursor 判空。
        self.document = self.workspace.active_document

    def on_popover_map(self, popover):
        popover.grab_focus()

    def popup_at_cursor(self, x, y):
        if self.document == None: return

        # 挂到 source_view：x, y 来自挂在 source_view 上的
        # secondary_click_controller（document_controller.py:61），是 source_view
        # 坐标系。挂到 source_view 后 set_pointing_to 的 rect 与坐标系统一，
        # 避免之前挂到 document.view 导致的 gutter 宽度横向偏移。
        #
        # 标准上下文菜单定位——让光标落在 popover 的一个角上。
        # GTK 默认让 popover 横向居中于 pointing-to rect（光标在 popover 顶边
        # 中央）。要把光标推到左上角，需把 popover 整体右移半个宽度，使光标
        # 对齐 popover 左边缘——与 preview 的 context_menu set_offset(130, 0)
        # （宽度 260 / 2 = 130）同思路。这里宽度固定 288（set_size_request，
        # 菜单 model 不含会撑宽的动态内容），offset 取 144 = 288 / 2。
        # 不用 measure：PopoverMenu 未 map 时内部 GtkStack 测量返回不可靠
        # （可能为 0，使 offset 退化为 0）。纵向默认在 pointing-to 下方展开
        # （光标落顶边），空间不足时 GTK 自动翻到上方（光标落底边）；横向接近
        # 右边界时 GTK 自动贴边/翻转。配合右移偏移，光标始终落在四个角之一：
        # 默认左上，下方不够翻到左下，右边不够翻到右上/右下。
        source_view = self.document.view.source_view
        self.popover_pointer.unparent()
        self.popover_pointer.set_parent(source_view)
        self.popover_pointer.set_offset(144, 0)

        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        self.popover_pointer.set_pointing_to(rect)
        self.popover_pointer.popup()

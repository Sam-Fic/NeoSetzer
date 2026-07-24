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

from setzer.widgets.fixed_width_label.fixed_width_label import FixedWidthLabel
from setzer.popovers.popover_manager import PopoverManager


class PreviewPanelView(Gtk.Box):
    '''PDF 预览面板的视图层。

    Pass-12 重构：与左侧栏（Symbols / Document Structure）保持一致的
    "内嵌工具栏 + 内容区" 结构：
      - 顶部 .sidebar-toolbar 工具栏：左侧 paging_label（page xx of xx），
        右侧 zoom_out / zoom_level / zoom_in / recolor / external 按钮。
      - 下方 .preview-card 内容区：PDF stack，带边距与圆角，呈"卡片"外观。
    工具栏样式与左侧栏统一（.sidebar-toolbar CSS class），不再使用 Gtk.ActionBar
    （原 ActionBar 在面板顶部绘制了一条 inset 分隔线，与左侧栏样式不一致）。
    标题栏不再覆盖预览面板，由 workspace_viewgtk 将 headerbar overlay 移到
    document_stack_wrapper 上，预览侧栏整体（含工具栏）与左侧栏行为一致。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_size_request(300, -1)
        self.add_css_class('preview')

        # ---- 顶部内嵌工具栏（与左侧栏 .sidebar-toolbar 统一外观）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        # 左侧：page xx of xx（类似左侧栏的 section_label，dim-label 灰化）
        self.paging_label = FixedWidthLabel(100)
        self.paging_label.set_xalign(0)
        self.paging_label.add_css_class('dim-label')
        self.paging_label.set_halign(Gtk.Align.START)
        self.paging_label.set_hexpand(True)
        self.toolbar.append(self.paging_label)

        # 右侧：zoom_out / zoom_level / zoom_in / recolor / external
        self.zoom_out_button = Gtk.Button(icon_name='zoom-out-symbolic')
        self.zoom_out_button.set_tooltip_text(_('Zoom out'))
        self.zoom_out_button.add_css_class('flat')
        self.zoom_out_button.set_can_focus(False)
        self.toolbar.append(self.zoom_out_button)

        self.zoom_level_label = FixedWidthLabel(66)
        self.zoom_level_button = Gtk.MenuButton()
        self.zoom_level_button.set_popover(PopoverManager.create_popover('preview_zoom_level').view)
        self.zoom_level_button.set_can_focus(False)
        self.zoom_level_button.set_tooltip_text(_('Set zoom level'))
        self.zoom_level_button.add_css_class('flat')
        self.zoom_level_button.set_child(self.zoom_level_label)
        self.toolbar.append(self.zoom_level_button)

        self.zoom_in_button = Gtk.Button(icon_name='zoom-in-symbolic')
        self.zoom_in_button.set_tooltip_text(_('Zoom in'))
        self.zoom_in_button.add_css_class('flat')
        self.zoom_in_button.set_can_focus(False)
        self.toolbar.append(self.zoom_in_button)

        self.recolor_pdf_toggle = Gtk.ToggleButton()
        self.recolor_pdf_toggle.set_icon_name('color-symbolic')
        self.recolor_pdf_toggle.set_tooltip_text(_('Match theme colors'))
        self.recolor_pdf_toggle.add_css_class('flat')
        self.recolor_pdf_toggle.set_can_focus(False)
        self.toolbar.append(self.recolor_pdf_toggle)

        self.external_viewer_button = Gtk.Button(icon_name='web-browser-symbolic')
        self.external_viewer_button.set_tooltip_text(_('External Viewer'))
        self.external_viewer_button.add_css_class('flat')
        self.external_viewer_button.set_can_focus(False)
        self.toolbar.append(self.external_viewer_button)

        self.append(self.toolbar)

        # ---- PDF 内容区：带边距 + 圆角的 card ----
        # set_overflow(HIDDEN) 让 PDF drawingarea 被裁剪到 border-radius 圆角内。
        # GTK4 CSS 不支持 overflow 属性，必须用 widget API。
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_css_class('preview-card')
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.empty_placeholder = Gtk.Box()
        self.stack.add_named(self.empty_placeholder, 'empty')

        self.append(self.stack)

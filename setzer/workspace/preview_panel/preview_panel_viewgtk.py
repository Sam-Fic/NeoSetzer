#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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

from setzer.popovers.popover_manager import PopoverManager


class PreviewPanelView(Gtk.Box):
    '''PDF 预览面板的视图层。

    Pass-12 重构：与左侧栏（Symbols / Document Structure）保持一致的
    "内嵌工具栏 + 内容区" 结构：
      - 顶部 .sidebar-toolbar 工具栏：左侧 page spinner + of N 标签，
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
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        # 预览/帮助切换按钮（单按钮，点击切换图标）
        self.switch_button = Gtk.Button()
        # 初始图标展示目标面板（Help），实际图标在 presenter 初始化时按当前
        # 显示的面板统一同步（_sync_switch_icons），不依赖本地预设。
        self.switch_button.set_child(Gtk.Image(icon_name='help-browser-symbolic'))
        self.switch_button.set_can_focus(False)
        self.switch_button.set_tooltip_text(_('Switch to Help'))
        self.switch_button.add_css_class('flat')

        # 页码指示器（可输入跳转）— SpinButton + "of N" 标签
        self.page_spin = Gtk.SpinButton()
        self.page_spin.set_range(1, 1)
        self.page_spin.set_increments(1, 1)
        self.page_spin.set_digits(0)
        self.page_spin.set_halign(Gtk.Align.START)
        self.page_spin.set_hexpand(False)
        self.page_spin.set_editable(True)
        self.page_spin.set_can_focus(True)
        # 数字居中（GTK CSS 不支持 text-align，需用 Editable 接口设置）。
        self.page_spin.set_alignment(0.5)
        for child in self.page_spin:
            if isinstance(child, Gtk.Button):
                child.set_visible(False)
        self.page_spin.add_css_class('preview-page-entry')
        self.toolbar.append(self.page_spin)

        self.paging_of_label = Gtk.Label()
        self.paging_of_label.set_xalign(0)
        self.paging_of_label.set_hexpand(False)
        self.paging_of_label.add_css_class('dim-label')
        self.toolbar.append(self.paging_of_label)

        # 构建失败提示：居中显示在页码区与右侧按钮之间的空白区，红色突出严重性。
        # 该标签始终占位（hexpand）以把右侧按钮推到最右；无提示时文本为空。
        self.stale_label = Gtk.Label()
        self.stale_label.set_xalign(0.5)
        self.stale_label.set_halign(Gtk.Align.CENTER)
        self.stale_label.set_hexpand(True)
        self.stale_label.add_css_class('preview-stale-label')
        self.toolbar.append(self.stale_label)

        # 右侧：zoom_out / fit_width / zoom_level / zoom_in / recolor / external
        self.zoom_out_button = Gtk.Button(icon_name='zoom-out-symbolic')
        self.zoom_out_button.set_tooltip_text(_('Zoom out'))
        self.zoom_out_button.add_css_class('flat')
        self.zoom_out_button.set_can_focus(False)
        self.toolbar.append(self.zoom_out_button)

        self.fit_width_button = Gtk.Button(icon_name='xsi-view-fit-width-symbolic')
        self.fit_width_button.set_tooltip_text(_('Fit to Width'))
        self.fit_width_button.add_css_class('flat')
        self.fit_width_button.set_can_focus(False)
        self.toolbar.append(self.fit_width_button)

        self.zoom_level_label = Gtk.Label()
        self.zoom_level_label.set_xalign(0.5)
        self.zoom_level_label.set_halign(Gtk.Align.CENTER)
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
        self.external_viewer_button.set_tooltip_text(_('Open in external PDF viewer'))
        self.external_viewer_button.add_css_class('flat')
        self.external_viewer_button.set_can_focus(False)
        self.toolbar.append(self.external_viewer_button)

        # 弹出为独立窗口：多显示器场景下把预览拖到另一块屏。图标 window-new-symbolic
        # 是 GNOME「新建/弹出窗口」惯用图标。popped_out 状态下此按钮由 presenter 隐藏
        # （独立窗口已 detached，收回走窗口关闭按钮）。
        self.detach_button = Gtk.Button(icon_name='window-new-symbolic')
        self.detach_button.set_tooltip_text(_('Detach preview to separate window'))
        self.detach_button.add_css_class('flat')
        self.detach_button.set_can_focus(False)
        self.toolbar.append(self.detach_button)

        self.toolbar.append(self.switch_button)

        self.append(self.toolbar)

        # ---- PDF 内容区：带边距 + 圆角的 card ----
        # set_overflow(HIDDEN) 让 PDF drawingarea 被裁剪到 border-radius 圆角内。
        # GTK4 CSS 不支持 overflow 属性，必须用 widget API。
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.empty_placeholder = Gtk.Box()
        self.stack.add_named(self.empty_placeholder, 'empty')

        self.append(self.stack)

        # 链接目标提示栏：由 PreviewPanelPresenter 在文档切换时将当前
        # PreviewView 的 target_label_revealer 挂到此处（stack 之外），
        # 避免被 stack 的 overflow:HIDDEN 裁剪到圆角内。
        self.target_bar_placeholder = Gtk.Box()
        self.target_bar_placeholder.set_hexpand(True)
        self.append(self.target_bar_placeholder)


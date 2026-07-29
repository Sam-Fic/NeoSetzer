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
from gi.repository import Gtk, Adw, Gio

from setzer.keyboard_shortcuts import shortcut_tooltips
from setzer.popovers.popover_manager import PopoverManager


class HeaderBar(object):
    '''组合持有 Adw.HeaderBar（Adw.HeaderBar 是 final 类型，不能被子类化）。

    所有子控件作为本 wrapper 的普通属性暴露，外部属性访问（preview_toggle /
    center_button / menu_button 等）零改动；唯一需要 .widget 前缀的是把
    headerbar 本身加进容器的调用（见 workspace_viewgtk add_top_bar）。
    '''

    def __init__(self):
        self.widget = Adw.HeaderBar()

        # sidebar toggles — 合并为单一按钮控制侧栏显隐
        self.sidebar_toggle = Gtk.ToggleButton()
        self.sidebar_toggle.set_child(Gtk.Image(icon_name='sidebar-show-symbolic'))
        self.sidebar_toggle.set_can_focus(False)
        # tooltip 中的快捷键随设置动态渲染（原先写死 "(F2)"，早已与实际绑定脱节）
        shortcut_tooltips.set_tooltip(self.sidebar_toggle, _('Toggle sidebar (Document Structure / Symbols)'), 'document_structure', 'symbols')
        self.sidebar_toggle.add_css_class('headerbar-plain')
        self.sidebar_toggle.add_css_class('headerbar-icon')

        self.widget.pack_start(self.sidebar_toggle)

        # open document: 单一 SplitButton，合并原先两个共用 document-open-symbolic
        # 图标、却绑定不同快捷键（Ctrl+O / Shift+Ctrl+O）的互斥按钮——它们随
        # 最近文档有无切换显隐，对用户而言是同一个"打开"按钮却有两种行为，易混淆。
        # 现在：主操作（点击 / Ctrl+O）打开文件选择对话框；下拉箭头展开"最近文档"
        # 对话框。SplitButton 自带的下拉箭头从视觉上区分了两种行为，不再共用
        # 同一图标的纯按钮。
        self.open_document_button = Adw.SplitButton()
        self.open_document_button.set_child(Gtk.Image(icon_name='document-open-symbolic'))
        self.open_document_button.set_can_focus(False)
        shortcut_tooltips.set_tooltip(self.open_document_button, _('Open a document'), 'open_document')
        self.open_document_button.set_action_name('win.open-document-dialog')
        self.open_document_button.add_css_class('headerbar-plain')
        self.open_document_button.add_css_class('headerbar-icon')
        # 下拉菜单：仅含"最近文档"一项，点击展开 DocumentChooser 对话框。
        open_menu = Gio.Menu()
        open_menu.append(_('Recent Documents'), 'win.open-recent-documents')
        self.open_document_button.set_menu_model(open_menu)
        open_popover = self.open_document_button.get_popover()
        if open_popover is not None:
            open_popover.add_css_class('menu')

        # new document: Adw.SplitButton — 左半部分默认新建 LaTeX 文档，
        # 右侧箭头展开气泡选择文档类型（LaTeX / BibTeX）。
        self.new_document_button = Adw.SplitButton()
        self.new_document_button.set_child(Gtk.Image(icon_name='document-new-symbolic'))
        self.new_document_button.set_can_focus(False)
        self.new_document_button.set_tooltip_text(_('Create a new LaTeX document'))
        self.new_document_button.set_action_name('win.new-latex-document')
        self.new_document_button.add_css_class('headerbar-plain')
        self.new_document_button.add_css_class('headerbar-icon')
        # PopoverManager 在 create_widgets() 前已 init，此处直接绑定 menu_model。
        # 原 setup_popovers() 方法从未被调用，导致箭头点不动——这是 bug 根因。
        # 若 popover 为 None（初始化顺序错误），打印警告而非静默失败——
        # 静默失败时箭头点不动且无任何错误提示，开发时难以定位。
        popover = PopoverManager.get_popover('new_document')
        if popover is None:
            print('Setzer warning: PopoverManager.get_popover("new_document") '
                  'returned None; new_document button menu unavailable. '
                  'Ensure PopoverManager.init() is called before HeaderBar creation.',
                  flush=True)
        else:
            self.new_document_button.set_menu_model(popover.view.model)
            menu_popover = self.new_document_button.get_popover()
            if menu_popover is not None:
                menu_popover.add_css_class('menu')

        self.widget.pack_start(self.open_document_button)
        self.widget.pack_start(self.new_document_button)

        # workspace menu (standard Libadwaita main menu via Gio.Menu)
        self.hamburger = PopoverManager.create_popover('hamburger_menu')
        self.menu_button = self.hamburger.get_menu_button()
        self.menu_button.set_can_focus(False)
        self.menu_button.add_css_class('headerbar-plain')
        self.menu_button.add_css_class('headerbar-icon')

        # preview/help toggle — 合并为单一按钮控制右侧栏显隐
        self.preview_help_toggle = Gtk.ToggleButton()
        self.preview_help_toggle.set_child(Gtk.Image(icon_name='sidebar-show-right-symbolic'))
        self.preview_help_toggle.set_can_focus(False)
        # 原先写死 "(F9)"，实际 preview=Ctrl+Shift+P、help=F1，现随设置动态渲染
        shortcut_tooltips.set_tooltip(self.preview_help_toggle, _('Toggle preview panel (PDF Preview / Help)'), 'preview', 'help')
        self.preview_help_toggle.add_css_class('headerbar-plain')
        self.preview_help_toggle.add_css_class('headerbar-icon')

        # build button wrapper (contains Build / stop / clean / timer)
        self.build_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Pass-12: 预览/帮助按钮回到各自侧栏的内嵌工具栏（与左侧栏一致），
        # 标题栏不再持有 panel_buttons_stack。pack_end 顺序恢复为原始布局：
        # menu → build → toggles（从右到左）。
        self.widget.pack_end(self.menu_button)
        self.widget.pack_end(self.build_wrapper)
        self.widget.pack_end(self.preview_help_toggle)

        # title / open documents popover
        self.open_docs_popover = PopoverManager.get_popover('document_switcher')

        # Adw.WindowTitle provides the title + subtitle (document name / folder)
        self.document_title = Adw.WindowTitle()
        self.document_title.set_title('')
        self.document_title.set_subtitle('')

        # 文件名右侧的下箭头，暗示该按钮可点击（展开已打开文档列表）
        self.center_down_arrow = Gtk.Image(icon_name='pan-down-symbolic')
        self.center_down_arrow.add_css_class('dim-label')
        self.center_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.center_box.append(self.document_title)
        self.center_box.append(self.center_down_arrow)

        self.center_button = Gtk.Button()
        shortcut_tooltips.set_tooltip(self.center_button, _('Show open documents'), 'show_open_docs')
        self.center_button.set_can_focus(False)
        self.center_button.set_halign(Gtk.Align.CENTER)
        self.center_button.set_child(self.center_box)
        self.center_button.add_css_class('headerbar-plain')
        self.center_button.add_css_class('headerbar-icon')
        self.center_button.add_css_class('flat')
        self.center_button.connect('clicked', self._on_center_button_clicked)

        self.center_title_welcome = Adw.WindowTitle()
        self.center_title_welcome.set_title(_('Welcome to Setzer'))

        self.center_widget = Gtk.Stack()
        self.center_widget.set_valign(Gtk.Align.FILL)
        self.center_widget.add_named(self.center_button, 'button')
        self.center_widget.add_named(self.center_title_welcome, 'welcome')
        # welcome↔document 模式切换时加 CROSSFADE 过渡（200ms 与 libadwaita
        # 默认动画时长一致）。与 preview_help_stack / Sidebar 的过渡行为对称，
        # 避免标题中心区域在打开/关闭文档时硬切。
        self.center_widget.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.center_widget.set_transition_duration(200)

        self.widget.set_title_widget(self.center_widget)

    def _on_center_button_clicked(self, button):
        self.open_docs_popover.show()

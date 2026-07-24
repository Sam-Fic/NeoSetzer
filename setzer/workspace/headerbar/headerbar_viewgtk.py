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
from gi.repository import Gtk, Adw

from setzer.popovers.popover_manager import PopoverManager


class HeaderBar(object):
    '''组合持有 Adw.HeaderBar（Adw.HeaderBar 是 final 类型，不能被子类化）。

    所有子控件作为本 wrapper 的普通属性暴露，外部属性访问（preview_toggle /
    center_button / menu_button 等）零改动；唯一需要 .widget 前缀的是把
    headerbar 本身加进容器的调用（见 workspace_viewgtk add_top_bar）。
    '''

    def __init__(self):
        self.widget = Adw.HeaderBar()

        # sidebar toggles
        self.document_structure_toggle = Gtk.ToggleButton()
        self.document_structure_toggle.set_child(Gtk.Image(icon_name='view-list-symbolic'))
        self.document_structure_toggle.set_can_focus(False)
        self.document_structure_toggle.set_tooltip_text(_('Toggle document structure') + ' (F2)')
        self.document_structure_toggle.add_css_class('headerbar-plain')
        self.document_structure_toggle.add_css_class('headerbar-icon')

        self.symbols_toggle = Gtk.ToggleButton()
        self.symbols_toggle.set_child(Gtk.Image(icon_name='emoji-symbols-symbolic'))
        self.symbols_toggle.set_can_focus(False)
        self.symbols_toggle.set_tooltip_text(_('Toggle symbols') + ' (F3)')
        self.symbols_toggle.add_css_class('headerbar-plain')
        self.symbols_toggle.add_css_class('headerbar-icon')

        # 直接 pack 进 HeaderBar，由 Adw 自动提供相邻控件的标准间距，
        # 不手动用零 spacing 的 Box 包裹（避免两个按钮连在一起）。
        self.widget.pack_start(self.document_structure_toggle)
        self.widget.pack_start(self.symbols_toggle)

        # open document buttons (icon-only). 两个按钮互斥：
        # - open_document_blank_button: 无最近文档时显示，触发文件选择对话框
        # - open_document_button: 有最近文档时显示，展开最近文档气泡
        # 视觉上共用同一图标，对用户而言是同一个"打开"按钮。
        self.open_document_blank_button = Gtk.Button(icon_name='document-open-symbolic')
        self.open_document_blank_button.set_can_focus(False)
        self.open_document_blank_button.set_tooltip_text(_('Open a document') + ' (' + _('Ctrl') + '+O)')
        self.open_document_blank_button.set_action_name('win.open-document-dialog')
        self.open_document_blank_button.add_css_class('headerbar-plain')
        self.open_document_blank_button.add_css_class('headerbar-icon')

        self.open_document_button = Gtk.Button(icon_name='document-open-symbolic')
        self.open_document_button.set_can_focus(False)
        self.open_document_button.set_tooltip_text(_('Open a document') + ' (' + _('Shift') + '+' + _('Ctrl') + '+O)')
        self.open_document_button.connect('clicked', lambda b: PopoverManager.get_popover('open_document').show())
        self.open_document_button.add_css_class('headerbar-plain')
        self.open_document_button.add_css_class('headerbar-icon')

        # new document: Adw.SplitButton — 左半部分默认新建 LaTeX 文档，
        # 右侧箭头展开气泡选择文档类型（LaTeX / BibTeX）。
        self.new_document_button = Adw.SplitButton()
        self.new_document_button.set_child(Gtk.Image(icon_name='document-new-symbolic'))
        self.new_document_button.set_can_focus(False)
        self.new_document_button.set_tooltip_text(_('Create a new document'))
        self.new_document_button.set_action_name('win.new-latex-document')
        self.new_document_button.add_css_class('headerbar-plain')
        self.new_document_button.add_css_class('headerbar-icon')
        # PopoverManager 在 create_widgets() 前已 init，此处直接绑定 menu_model。
        # 原 setup_popovers() 方法从未被调用，导致箭头点不动——这是 bug 根因。
        popover = PopoverManager.get_popover('new_document')
        if popover is not None:
            self.new_document_button.set_menu_model(popover.view.model)
            menu_popover = self.new_document_button.get_popover()
            if menu_popover is not None:
                menu_popover.add_css_class('menu')

        self.widget.pack_start(self.open_document_button)
        self.widget.pack_start(self.open_document_blank_button)
        self.widget.pack_start(self.new_document_button)

        # workspace menu (standard Libadwaita main menu via Gio.Menu)
        self.hamburger = PopoverManager.create_popover('hamburger_menu')
        self.menu_button = self.hamburger.get_menu_button()
        self.menu_button.set_can_focus(False)
        self.menu_button.add_css_class('headerbar-plain')
        self.menu_button.add_css_class('headerbar-icon')

        # save document button (icon only)
        self.save_document_button = Gtk.Button(icon_name='document-save-symbolic')
        self.save_document_button.set_can_focus(False)
        self.save_document_button.set_tooltip_text(_('Save the current document') + ' (' + _('Ctrl') + '+S)')
        self.save_document_button.set_action_name('win.save')
        self.save_document_button.add_css_class('headerbar-plain')
        self.save_document_button.add_css_class('headerbar-icon')

        # help and preview toggles
        self.preview_toggle = Gtk.ToggleButton()
        self.preview_toggle.set_child(Gtk.Image(icon_name='view-paged-symbolic'))
        self.preview_toggle.set_can_focus(False)
        self.preview_toggle.set_tooltip_text(_('Toggle preview') + ' (F9)')
        self.preview_toggle.add_css_class('headerbar-plain')
        self.preview_toggle.add_css_class('headerbar-icon')
        self.help_toggle = Gtk.ToggleButton()
        self.help_toggle.set_child(Gtk.Image(icon_name='help-browser-symbolic'))
        self.help_toggle.set_can_focus(False)
        self.help_toggle.set_tooltip_text(_('Toggle help') + ' (F1)')
        self.help_toggle.add_css_class('headerbar-plain')
        self.help_toggle.add_css_class('headerbar-icon')

        # build button wrapper (contains Save and Build / stop / clean / timer)
        self.build_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Pass-12: 预览/帮助按钮回到各自侧栏的内嵌工具栏（与左侧栏一致），
        # 标题栏不再持有 panel_buttons_stack。pack_end 顺序恢复为原始布局：
        # menu → build → toggles → save（从右到左）。
        self.widget.pack_end(self.menu_button)
        self.widget.pack_end(self.build_wrapper)
        self.widget.pack_end(self.preview_toggle)
        self.widget.pack_end(self.help_toggle)
        self.widget.pack_end(self.save_document_button)

        # title / open documents popover
        self.open_docs_popover = PopoverManager.get_popover('document_switcher')

        # Adw.WindowTitle provides the title + subtitle (document name / folder)
        self.document_title = Adw.WindowTitle()
        self.document_title.set_title('')
        self.document_title.set_subtitle('')

        self.center_button = Gtk.Button()
        self.center_button.set_tooltip_text(_('Show open documents') + ' (' + _('Ctrl') + '+T)')
        self.center_button.set_can_focus(False)
        self.center_button.set_halign(Gtk.Align.CENTER)
        self.center_button.set_child(self.document_title)
        self.center_button.add_css_class('headerbar-plain')
        self.center_button.add_css_class('headerbar-icon')
        self.center_button.connect('clicked', self._on_center_button_clicked)

        self.center_title_welcome = Adw.WindowTitle()
        self.center_title_welcome.set_title(_('Welcome to Setzer'))

        self.center_widget = Gtk.Stack()
        self.center_widget.set_valign(Gtk.Align.FILL)
        self.center_widget.add_named(self.center_button, 'button')
        self.center_widget.add_named(self.center_title_welcome, 'welcome')

        self.widget.set_title_widget(self.center_widget)

    def _on_center_button_clicked(self, button):
        self.open_docs_popover.show()

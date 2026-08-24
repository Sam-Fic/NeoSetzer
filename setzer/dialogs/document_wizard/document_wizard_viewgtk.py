#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Gio, GLib, Gdk, GdkPixbuf

import os

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class DocumentWizardView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        # 内容自然高度会把对话框压得很矮（如文档类页），这里用
        # content_width/height 给出一个舒适的初始/最小尺寸。注意这只设定
        # 下限：对话框仍可被用户拖拽缩放，窄屏时由窗口管理器裁剪，各页面
        # 内部继续靠 Clamp、BreakpointBin 和滚动容器自适应宽窄窗口。
        self.set_content_width(840)
        self.set_content_height(900)
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.center_box = Gtk.CenterBox()
        self.center_box.set_orientation(Gtk.Orientation.HORIZONTAL)
        # Fill the topbox vertically so the page_stack (and therefore the
        # ScrolledWindow inside each page) gets the dialog's full content
        # height to scroll within, instead of only the natural height of
        # the visible page.
        self.center_box.set_vexpand(True)
        self.pages = list()

        self.title_widget = Adw.WindowTitle(title=_('Create a template document'))

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))
        self.cancel_button.set_tooltip_text(_('Close the dialog without creating a document'))

        self.back_button = Gtk.Button.new_with_mnemonic(_('_Back'))
        self.back_button.set_tooltip_text(_('Go to the previous wizard page'))

        # 使用 Gio.Menu + Gtk.PopoverMenu，与汉堡菜单使用同样的组件。
        self.template_actions_button = Gtk.MenuButton()
        self.template_actions_button.set_icon_name('view-more-symbolic')
        self.template_actions_button.set_tooltip_text(_('Template actions'))
        self._wizard_actions = Gio.SimpleActionGroup()
        self.template_actions_button.insert_action_group(
            'wizard', self._wizard_actions)
        template_menu_model = Gio.Menu()
        template_menu_model.append(
            _('Save as _Preset'), 'wizard.save-as-preset')
        template_menu_model.append(
            _('Save _Document Template'), 'wizard.save-document-template')
        self.template_actions_button.set_menu_model(template_menu_model)

        self.next_button = Gtk.Button.new_with_mnemonic(_('_Next'))
        self.next_button.set_tooltip_text(_('Go to the next wizard page'))
        self.next_button.add_css_class('suggested-action')

        self.create_button = Gtk.Button.new_with_mnemonic(_('_Create'))
        self.create_button.set_tooltip_text(_('Create the document with the selected options'))
        self.create_button.add_css_class('suggested-action')

        self.headerbar.set_title_widget(self.title_widget)
        self.headerbar.pack_start(self.cancel_button)
        self.headerbar.pack_start(self.back_button)
        self.headerbar.pack_start(self.template_actions_button)
        self.headerbar.pack_end(self.create_button)
        self.headerbar.pack_end(self.next_button)

        self.page_stack = Gtk.Stack()
        # Let each page use its own natural height instead of being stretched
        # to the tallest page (which left a large blank area on short pages like
        # the document-class chooser).
        self.page_stack.set_vhomogeneous(False)
        # Fill the dialog's content area so a long page (e.g. the Packages
        # list on General settings) can scroll inside its ScrolledWindow
        # instead of overflowing past the dialog.
        self.page_stack.set_vexpand(True)
        self.center_box.set_center_widget(self.page_stack)
        self.topbox.append(self.center_box)



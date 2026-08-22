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
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf

import os

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class DocumentWizardView(DialogView):

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)

        # 让 Adw.Dialog 按内容和可用窗口空间协商尺寸；各页面使用 Clamp 和
        # 滚动容器处理宽/窄窗口，而不是固定为单一桌面尺寸。
        self.headerbar.set_title_widget(Gtk.Label(label=_('Create a template document')))
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

        self.title_label = Gtk.Label(label=_('Create a template document'))
        self.title_label.add_css_class('title')
        self.subtitle_label = Gtk.Label(label='')
        self.subtitle_label.add_css_class('subtitle')

        self.title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.title_box.append(self.title_label)
        self.title_box.append(self.subtitle_label)

        self.title_widget = Gtk.CenterBox()
        self.title_widget.set_orientation(Gtk.Orientation.VERTICAL)
        self.title_widget.set_center_widget(self.title_box)

        self.cancel_button = Gtk.Button.new_with_mnemonic(_('_Cancel'))

        # 既有入口只保存向导配置预设（文档类、页边距、包等）。
        self.save_template_button = Gtk.Button.new_with_mnemonic(_('Save as _Preset'))
        # #205：完整保存当前 LaTeX 缓冲区的源文本快照，供后续新建文档使用。
        self.save_document_template_button = Gtk.Button.new_with_mnemonic(
            _('Save _Document Template'))

        self.back_button = Gtk.Button.new_with_mnemonic(_('_Back'))

        # 保存预设与保存完整源模板是较少使用的模板管理操作。放入同一个
        # popover 后，标题栏只保留导航和创建这两个主任务。
        self.template_actions_button = Gtk.MenuButton()
        self.template_actions_button.set_icon_name('view-more-symbolic')
        self.template_actions_button.set_tooltip_text(_('Template actions'))
        template_actions_popover = Gtk.Popover()
        template_actions_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6)
        template_actions_box.set_margin_top(6)
        template_actions_box.set_margin_bottom(6)
        template_actions_box.set_margin_start(6)
        template_actions_box.set_margin_end(6)
        self.save_template_button.set_halign(Gtk.Align.FILL)
        self.save_document_template_button.set_halign(Gtk.Align.FILL)
        template_actions_box.append(self.save_template_button)
        template_actions_box.append(self.save_document_template_button)
        template_actions_popover.set_child(template_actions_box)
        self.template_actions_button.set_popover(template_actions_popover)

        self.next_button = Gtk.Button.new_with_mnemonic(_('_Next'))
        self.next_button.add_css_class('suggested-action')

        self.create_button = Gtk.Button.new_with_mnemonic(_('_Create'))
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



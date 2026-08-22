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
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.app.service_locator import ServiceLocator
from setzer.example_project.opener import ExampleProjectOpener
from setzer.dialogs.first_run_tutorial.tutorial_content import (
    DEFAULT_SHORTCUTS,
    get_configured_shortcut,
    get_tutorial_tips,
)


def maybe_show_first_run_tutorial():
    '''首次启动引导：应用真正首次启动时弹一次欢迎引导。

    由 settings 标志位保证只自动弹一次：first_run_tutorial_shown 置 True
    后不再自动弹。偏好页的“再次显示首次引导”按钮可随时手动重看。
    '''
    settings = ServiceLocator.get_settings()
    if settings.get_value('preferences', 'first_run_tutorial_shown'):
        return
    # 延迟导入，避免与 dialog_locator 形成循环依赖。
    from setzer.dialogs.dialog_locator import DialogLocator
    DialogLocator.get_dialog('first_run_tutorial').run()


class FirstRunTutorialDialog(object):
    '''首次运行欢迎引导（Adw.Dialog），介绍 NeoSetzer 的核心工作流。'''

    def __init__(self, main_window):
        self.main_window = main_window
        self.settings = ServiceLocator.get_settings()
        self.example_project_opener = ExampleProjectOpener(
            ServiceLocator.get_workspace(), main_window)
        self.setup()

    def setup(self):
        self.view = Adw.Dialog()
        self.view.set_title(_('Welcome to NeoSetzer'))
        # Adw.Dialog 会按 content 自然撑开并可被用户拖拽缩放；
        # 这里给一个舒适的默认尺寸，避免初次弹出过窄。
        self.view.set_content_width(620)
        self.view.set_content_height(560)

        headerbar = Adw.HeaderBar()
        headerbar.set_show_start_title_buttons(False)
        headerbar.set_show_end_title_buttons(False)
        self.start_button = Gtk.Button(label=_('Start Writing'))
        self.start_button.add_css_class('suggested-action')
        self.start_button.connect('clicked', lambda b: self.view.close())
        headerbar.pack_end(self.start_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(headerbar)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(580)
        clamp.set_margin_top(24)
        clamp.set_margin_bottom(24)
        clamp.set_margin_start(24)
        clamp.set_margin_end(24)
        scrolled.set_child(clamp)
        toolbar.set_content(scrolled)

        self.view.set_child(toolbar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        clamp.set_child(box)

        intro = Gtk.Label()
        intro.set_text(_('Start with a document, then build and preview it. You can '
                         'reopen these tips at any time from Preferences.'))
        intro.set_wrap(True)
        intro.set_xalign(0.0)
        intro.add_css_class('caption')
        intro.add_css_class('dim-label')
        box.append(intro)

        # 核心功能要点由无 GTK 内容模块集中定义，便于验证内容顺序、
        # 快捷键回退和本地化占位符。快捷键按用户当前配置动态展示，避免
        # 教程在用户改绑后仍显示过期的固定按键说明。
        tips = get_tutorial_tips(
            self.get_shortcut_label('save_and_build'),
            self.get_shortcut_label('command_palette'),
        )
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        listbox.add_css_class('boxed-list')
        box.append(listbox)
        for icon_name, title, subtitle in tips:
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(28)
            row.add_prefix(image)
            listbox.append(row)

        # 引导用户打开示例文档（与欢迎页入口保持一致）。
        example_button = Gtk.Button()
        example_button.set_icon_name('document-open-symbolic')
        example_button.set_label(_('Create Example Project…'))
        example_button.set_halign(Gtk.Align.CENTER)
        example_button.connect('clicked', self.on_open_example_clicked)
        box.append(example_button)

    def get_shortcut_label(self, action_name):
        '''Return a localized accelerator label with a defensive fallback.'''
        shortcut = get_configured_shortcut(self.settings, action_name)
        try:
            label = Gtk.accelerator_get_label(shortcut)
        except (TypeError, ValueError):
            label = ''
        if label:
            return label

        fallback = DEFAULT_SHORTCUTS[action_name]
        try:
            return Gtk.accelerator_get_label(fallback) or fallback
        except (TypeError, ValueError):
            return fallback

    def on_open_example_clicked(self, button):
        self.example_project_opener.choose_and_open(self.view.close)

    def run(self):
        # 标记已展示，确保只自动弹一次（无论以何种方式关闭）。
        self.settings.set_value('preferences', 'first_run_tutorial_shown', True)
        self.settings.pickle()
        self.view.present(self.main_window)

    def show_again(self):
        # 偏好里“再次显示首次引导”按钮：忽略标志，直接弹出。
        self.view.present(self.main_window)

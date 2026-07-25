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
from gi.repository import Gtk
from gi.repository import Adw
from gi.repository import GLib
from gi.repository import GdkPixbuf

from setzer.dialogs.document_wizard.pages.page import Page, PageView
from setzer.app.service_locator import ServiceLocator

import os
import threading


class BeamerSettingsPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = BeamerSettingsPageView()

        self.image_loading_lock = threading.Lock()
        threading.Thread(target=self.load_beamer_images, daemon=True).start()

    def observe_view(self):
        self.image_loading_lock.acquire()
        self.image_loading_lock.release()

        def row_selected(box, row, user_data=None):
            child_name = row.get_title()
            self.current_values['beamer']['theme'] = child_name
            for i in range(0, 2):
                image_box = self.view.preview_image_boxes[child_name][i]
                if image_box.get_center_widget() == None:
                    image_box.set_center_widget(self.view.preview_images[child_name][i])
            self.view.preview_stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.view.preview_stack.set_visible_child_name(child_name + '_0')

            button = self.view.preview_buttons[child_name][0]
            button.set_child(self.view.preview_button_images[child_name][0])
            button = self.view.preview_buttons[child_name][1]
            button.set_child(self.view.preview_button_images[child_name][1])
            self.view.preview_button_stack.set_visible_child_name(child_name)

        def option_toggled(row, pspec, option_name):
            self.current_values['beamer']['option_' + option_name] = row.get_active()

        def preview_button_clicked(button, theme_name, number):
            stack = self.view.preview_stack
            if number == 0:
                stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
            elif number == 1:
                stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
            stack.set_visible_child_name(theme_name + '_' + str(number))

        self.view.themes_list.connect('row-selected', row_selected)
        self.view.option_show_navigation.connect('notify::active', option_toggled, 'show_navigation')
        self.view.option_top_align.connect('notify::active', option_toggled, 'top_align')
        for name in self.view.theme_names:
            for i in range(0, 2):
                button = self.view.preview_buttons[name][i]
                button.set_can_focus(False)
                button.connect('clicked', preview_button_clicked, name, i)

    def load_beamer_images(self):
        with self.image_loading_lock:
            for name in self.view.theme_names:
                for i in range(0, 2):
                    image = Gtk.Picture.new_for_filename(os.path.join(ServiceLocator.get_resources_path(), 'document_wizard', 'beamerpreview_' + name + '_page_' + str(i) + '.png'))
                    image.set_size_request(346, 260)
                    self.view.preview_images[name].append(image)
                    image = Gtk.Picture.new_for_filename(os.path.join(ServiceLocator.get_resources_path(), 'document_wizard', 'beamerpreview_' + name + '_page_' + str(i) + '.png'))
                    image.set_size_request(100, 75)
                    self.view.preview_button_images[name].append(image)

    def load_presets(self, presets):
        try:
            row = self.view.themes_list_rows[presets['beamer']['theme']]
        except Exception:
            row = self.view.themes_list_rows[self.current_values['beamer']['theme']]
        self.view.themes_list.select_row(row)

        for setter_function, value_name in [
            (self.view.option_show_navigation.set_active, 'option_show_navigation'),
            (self.view.option_top_align.set_active, 'option_top_align')
        ]:
            try:
                value = presets['beamer'][value_name]
            except TypeError:
                value = self.current_values['beamer'][value_name]
            setter_function(value)

    def on_activation(self):
        pass


class BeamerSettingsPageView(PageView):

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 2: ' + _('Beamer settings')

        self.theme_names = ['Warsaw', 'Malmoe', 'Luebeck', 'Copenhagen', 'Szeged', 'Singapore', 'Frankfurt', 'Darmstadt', 'Dresden', 'Ilmenau', 'Berlin', 'Hannover', 'Marburg', 'Goettingen', 'PaloAlto', 'Berkeley', 'Montpellier', 'JuanLesPins', 'Antibes', 'Rochester', 'Pittsburgh', 'EastLansing', 'CambridgeUS', 'AnnArbor', 'Madrid', 'Boadilla', 'Bergen', 'default']

        self.themes_list_scrolled_window = Gtk.ScrolledWindow()
        self.themes_list_scrolled_window.set_size_request(348, 230)
        self.themes_list = Gtk.ListBox()
        self.themes_list.set_size_request(346, -1)
        self.themes_list.set_can_focus(False)
        self.themes_list.add_css_class('boxed-list')
        self.themes_list_rows = dict()
        for name in self.theme_names:
            row = Adw.ActionRow()
            row.set_title(name)
            self.themes_list_rows[name] = row
            self.themes_list.prepend(row)
        self.themes_list.set_vexpand(False)
        # 内嵌滚动窗口定高，列表滚到底时最后一项需底部留白，
        # 否则紧贴 230px 窗口底边（与 page.py wrap_content 修复同因）。
        self.themes_list.set_margin_bottom(18)
        self.themes_list_scrolled_window.set_child(self.themes_list)

        self.group_options = Adw.PreferencesGroup()
        self.group_options.set_title(_('Options'))
        self.option_show_navigation = Adw.SwitchRow()
        self.option_show_navigation.set_title(_('Show navigation buttons'))
        self.option_top_align = Adw.SwitchRow()
        self.option_top_align.set_title(_('Align content to the top of pages'))
        self.option_top_align.set_subtitle(_('("t" option, it\'s centered by default)'))
        self.group_options.add(self.option_show_navigation)
        self.group_options.add(self.option_top_align)

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.form.append(self.themes_list_scrolled_window)
        self.form.append(self.group_options)

        self.preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.preview_stack_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.preview_stack = Gtk.Stack()
        self.preview_button_stack = Gtk.Stack()
        self.preview_button_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.preview_images = dict()
        self.preview_image_boxes = dict()
        self.preview_buttons = dict()
        self.preview_button_widgets = dict()
        self.preview_button_images = dict()
        for name in self.theme_names:
            self.preview_images[name] = list()
            self.preview_image_boxes[name] = list()
            self.preview_buttons[name] = list()
            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            self.preview_button_widgets[name] = button_box
            self.preview_button_stack.add_named(button_box, name)
            for i in range(0, 2):
                image_box = Gtk.CenterBox()
                image_box.set_orientation(Gtk.Orientation.HORIZONTAL)
                self.preview_image_boxes[name].append(image_box)
                self.preview_stack.add_named(image_box, name + '_' + str(i))
                button = Gtk.Button()
                button.set_margin_end(12)
                button.set_margin_top(12)
                self.preview_buttons[name].append(button)
                self.preview_button_images[name] = list()
                self.preview_button_widgets[name].append(button)
        self.preview_stack_wrapper.append(self.preview_stack)
        self.preview.append(self.preview_stack_wrapper)
        self.preview.append(self.preview_button_stack)
        self.preview.set_margin_top(18)
        self.preview.set_margin_start(18)
        self.preview.set_margin_end(18)

        self.content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
        self.content.append(self.wrap_content(self.form, maximum_size=420, tightening_threshold=360))
        self.content.append(self.preview)

        self.append(self.content)

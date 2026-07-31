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
            self._update_preview(child_name)

        def option_toggled(row, pspec, option_name):
            self.current_values['beamer']['option_' + option_name] = row.get_active()

        self.view.themes_list.connect('row-selected', row_selected)
        self.view.option_show_navigation.connect('notify::active', option_toggled, 'show_navigation')
        self.view.option_top_align.connect('notify::active', option_toggled, 'top_align')

        # Show initial preview
        initial = self.current_values['beamer']['theme']
        if initial in self.view.preview_images:
            self._update_preview(initial)

    def _update_preview(self, theme_name):
        images = self.view.preview_images.get(theme_name, [])
        child = self.view.preview_box.get_first_child()
        while child:
            self.view.preview_box.remove(child)
            child = self.view.preview_box.get_first_child()
        for img in images:
            self.view.preview_box.append(img)

    def load_beamer_images(self):
        with self.image_loading_lock:
            for name in self.view.theme_names:
                images = []
                for i in range(0, 2):
                    image = Gtk.Picture.new_for_filename(os.path.join(ServiceLocator.get_resources_path(), 'document_wizard', 'beamerpreview_' + name + '_page_' + str(i) + '.png'))
                    image.set_size_request(208, 200)
                    images.append(image)
                self.view.preview_images[name] = images

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

        self.themes_list = Gtk.ListBox()
        self.themes_list.set_can_focus(False)
        self.themes_list.add_css_class('boxed-list')
        self.themes_list_rows = dict()
        for name in self.theme_names:
            row = Adw.ActionRow()
            row.set_title(name)
            self.themes_list_rows[name] = row
            self.themes_list.prepend(row)

        self.group_options = Adw.PreferencesGroup()
        self.group_options.set_title(_('Options'))
        self.option_show_navigation = Adw.SwitchRow()
        self.option_show_navigation.set_title(_('Show navigation buttons'))
        self.option_show_navigation.set_tooltip_text(_(
            'Show or hide the Beamer navigation bar at the bottom of slides. '
            'Disabling gives a cleaner look for presentations.'))
        self.option_top_align = Adw.SwitchRow()
        self.option_top_align.set_title(_('Align content to the top of pages'))
        self.option_top_align.set_subtitle(_('("t" option, it\'s centered by default)'))
        self.option_top_align.set_tooltip_text(_(
            'When enabled, slide content is top-aligned (the [t] option). '
            'When disabled, content is vertically centered — the Beamer default.'))
        self.group_options.add(self.option_show_navigation)
        self.group_options.add(self.option_top_align)

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.form.append(self.themes_list)
        self.form.append(self.group_options)

        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.preview_box.set_halign(Gtk.Align.CENTER)

        self.preview_images = dict()

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.append(self.form)
        self.content.append(self.preview_box)

        self.append(self.wrap_content(self.content))

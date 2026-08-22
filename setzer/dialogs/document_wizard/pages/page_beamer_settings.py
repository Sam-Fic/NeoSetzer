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
from gi.repository import Gtk, Adw, GLib

from setzer.dialogs.document_wizard.pages.page import Page, PageView
from setzer.app.service_locator import ServiceLocator

import os


# 每批加载图片数量：8 是经验值；56 张图仅 7 批，首屏前完成且不阻塞 UI。
_LOAD_BATCH_SIZE = 8


class BeamerSettingsPage(Page):

    def __init__(self, current_values):
        self.current_values = current_values
        self.view = BeamerSettingsPageView()

        self._loading_queue = [
            (name, i)
            for name in self.view.theme_names
            for i in range(0, 2)
        ]
        GLib.idle_add(self._load_images_batch)

    def observe_view(self):
        def row_selected(box, row, user_data=None):
            child_name = row.get_title()
            self.current_values['beamer']['theme'] = child_name
            self._update_preview(child_name)

        def option_toggled(row, pspec, option_name):
            self.current_values['beamer']['option_' + option_name] = row.get_active()

        self.view.themes_list.connect('row-selected', row_selected)
        self.view.option_show_navigation.connect('notify::active', option_toggled, 'show_navigation')
        self.view.option_top_align.connect('notify::active', option_toggled, 'top_align')

        # Show initial preview（与 batch loader 共用同一把锁，避免竞态）
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

    def _load_images_batch(self):
        '''GLib.idle_add 回调：每次处理一批图片，返回 True 表示继续调度。

        idle 回调必须在某次返回 False 结束，否则在部分 PyGObject 版本中
        None 会被当作“继续调用”，导致该回调被无限重复触发、CPU 占满。
        '''
        queue = self._loading_queue
        if not queue:
            return False

        resources_path = ServiceLocator.get_resources_path()
        for _ in range(_LOAD_BATCH_SIZE):
            if not queue:
                break
            name, index = queue.pop(0)
            image = Gtk.Picture.new_for_filename(
                os.path.join(resources_path, 'document_wizard',
                             'beamerpreview_' + name + '_page_' + str(index) + '.png')
            )
            image.set_size_request(208, 200)
            self.view.preview_images.setdefault(name, []).append(image)

        if queue:
            return True

        # 全部加载完成：若当前选中主题的预览图已就绪，展示默认预览。
        # 这解决了“默认主题为 default，但初始预览因图片未就绪而未显示”的问题。
        theme = self.current_values['beamer']['theme']
        if self.view.preview_images.get(theme):
            self._update_preview(theme)
        return False

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
        # 主题决定生成的 presentation 样式，进入页面后可直接用方向键选择。
        self.view.themes_list.grab_focus()


class BeamerSettingsPageView(PageView):

    def __init__(self):
        PageView.__init__(self)

        self.headerbar_subtitle = _('Step') + ' 2: ' + _('Beamer settings')

        self.theme_names = ['Warsaw', 'Malmoe', 'Luebeck', 'Copenhagen', 'Szeged', 'Singapore', 'Frankfurt', 'Darmstadt', 'Dresden', 'Ilmenau', 'Berlin', 'Hannover', 'Marburg', 'Goettingen', 'PaloAlto', 'Berkeley', 'Montpellier', 'JuanLesPins', 'Antibes', 'Rochester', 'Pittsburgh', 'EastLansing', 'CambridgeUS', 'AnnArbor', 'Madrid', 'Boadilla', 'Bergen', 'default']

        self.themes_list = Gtk.ListBox()
        self.themes_list.set_can_focus(True)
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

        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.preview_box.set_halign(Gtk.Align.CENTER)

        self.preview_images = dict()

        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.form.append(self.themes_list)
        self.form.append(self.group_options)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.content.append(self.preview_box)
        self.content.append(self.form)

        self.append(self.wrap_content(self.content))

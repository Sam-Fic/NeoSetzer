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
from gi.repository import Gtk, GObject


class BuildWidgetView(Gtk.Box):

    def __init__(self):
        # spacing=6 对齐 Adw.HeaderBar 默认子控件间距（Gtk.Box 自身默认 spacing=0 会粘连）
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_can_focus(False)

        self.timer = 0
        self.timer_active = False
        self.state_change_count = 0

        self.build_button = Gtk.Button()

        self.idle_icon = Gtk.Image(icon_name='system-run-symbolic')
        self.idle_label = Gtk.Label(label=_('Save and Build'))
        self.idle_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.idle_box.append(self.idle_icon)
        self.idle_box.append(self.idle_label)

        self.active_icon = Gtk.Image(icon_name='process-stop-symbolic')
        self.timer_label = Gtk.Label(label='0:00')
        self.active_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.active_box.append(self.active_icon)
        self.active_box.append(self.timer_label)

        self.build_button.set_child(self.idle_box)
        self.build_button.set_tooltip_text(_('Save and build .pdf-file from document') + ' (F5)')
        self.build_button.add_css_class('suggested-action')

        # clean_button 嵌套在 BuildWidgetView(Gtk.Box) 里，不是 HeaderBar 直接子控件，
        # 不会被 HeaderBar 自动 flat，需显式加 .flat 与标题栏图标按钮保持一致外观。
        self.clean_button = Gtk.Button()
        self.clean_button.set_child(Gtk.Image(icon_name='edit-clear-all-symbolic'))
        self.clean_button.set_tooltip_text(_('Cleanup build files'))
        self.clean_button.add_css_class('flat')

        self.result_revealer = Gtk.Revealer()
        self.result_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.result_label = Gtk.Label(label='')
        self.result_revealer.set_child(self.result_label)
        self.result_revealer.set_visible(False)

        self.append(self.clean_button)
        self.append(self.result_revealer)
        self.prepend(self.build_button)
        
    def switch_to_building(self):
        self.build_button.set_child(self.active_box)
        self.build_button.set_tooltip_text(_('Stop building'))
        self.build_button.set_action_name(None)
        self.build_button.remove_css_class('suggested-action')
        self.build_button.add_css_class('destructive-action')

    def switch_to_idle(self):
        self.build_button.set_child(self.idle_box)
        self.build_button.set_tooltip_text(_('Save and build .pdf-file from document') + ' (F5)')
        self.build_button.set_action_name('win.save-and-build')
        self.build_button.remove_css_class('destructive-action')
        self.build_button.add_css_class('suggested-action')

    def start_timer(self):
        self.timer_active = True
        GObject.timeout_add(500, self.increment_timer)

    def increment_timer(self):
        if self.timer_active:
            self.timer += 500
            if self.timer // 1000 >= 1:
                self.timer_label.set_text('{}:{:02}'.format(self.timer // 60000, (self.timer % 60000) // 1000))
        return self.timer_active

    def stop_timer(self):
        self.timer_active = False

    def reset_timer(self):
        self.timer = 0
        self.timer_label.set_text('0:00')

    def show_result(self, text=''):
        self.result_label.set_markup(text)
        self.result_revealer.set_visible(True)
        self.state_change_count += 1
        GObject.timeout_add(5, self.reveal, self.state_change_count)

    def has_result(self):
        text = self.result_label.get_text()
        if text[:6] in ['Succes', 'Failed']:
            return True
        else:
            return False

    def hide_result(self, duration):
        self.state_change_count += 1
        GObject.timeout_add(duration, self.unreveal, self.state_change_count)

    def hide_result_now(self):
        self.result_revealer.set_transition_type(Gtk.RevealerTransitionType.NONE)
        self.result_revealer.set_reveal_child(False)
        self.result_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)

    def reveal(self, state_change_count):
        if self.state_change_count == state_change_count:
            self.result_revealer.set_reveal_child(True)
        return False

    def unreveal(self, state_change_count):
        if self.state_change_count == state_change_count:
            self.result_revealer.set_reveal_child(False)
        return False



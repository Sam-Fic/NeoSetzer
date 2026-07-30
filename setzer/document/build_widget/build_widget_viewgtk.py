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
from gi.repository import Gtk, GLib

from setzer.keyboard_shortcuts import shortcut_tooltips


class BuildWidgetView(Gtk.Box):

    def __init__(self):
        # spacing=6 对齐 Adw.HeaderBar 默认子控件间距，对应 CSS 变量
        # --setzer-spacing-sm（见 style_gtk.css）。Gtk.Box.spacing 无法用
        # CSS 设置，此处保留 Python 指定但与 CSS 变量值保持一致。
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_can_focus(False)

        self.timer = 0
        self.timer_active = False
        self._timer_timeout_id = None

        self.build_button = Gtk.Button()

        # idle 状态直接用一个 Image 作 child，与标题栏其它 Gtk.Button(icon_name=...)
        # 的构造完全一致，避免多余的 Gtk.Box 包裹导致图标对齐/尺寸不一致。
        self.idle_icon = Gtk.Image(icon_name='system-run-symbolic')
        self.build_button.set_child(self.idle_icon)

        self.active_icon = Gtk.Image(icon_name='process-stop-symbolic')
        self.timer_label = Gtk.Label(label='0:00')
        # 按钮内只保留 [停止图标] + 计时器；构建阶段 “阶段名 · Pass N” 不放进
        # 按钮（会把它撑长），改放按钮 tooltip（见 set_stage / clear_stage）。
        self.active_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.active_box.append(self.active_icon)
        self.active_box.append(self.timer_label)

        # 构建态 tooltip 基文本；阶段信息经 set_stage 追加其后，clear_stage 复位。
        # 放在 __init__（运行时 gettext 已安装）以便 clear_stage 在 switch_to_building
        # 前调用也不会 AttributeError。
        self._building_tooltip_base = _('Stop building')

        shortcut_tooltips.set_tooltip(self.build_button, _('Save and build .pdf-file from document'), 'save_and_build')
        self.build_button.add_css_class('suggested-action')

        self.clean_button = Gtk.Button()
        self.clean_button.set_child(Gtk.Image(icon_name='edit-clear-all-symbolic'))
        self.clean_button.set_tooltip_text(_('Cleanup build files'))
        self.clean_button.add_css_class('flat')

        self.append(self.clean_button)
        self.prepend(self.build_button)

        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        self.stop_timer()

    def switch_to_building(self):
        self.build_button.set_child(self.active_box)
        # 不带动作名注册：覆盖 idle 状态的条目，构建期间改键也不会误刷回旧文案
        shortcut_tooltips.set_tooltip(self.build_button, self._building_tooltip_base)
        self.build_button.set_action_name(None)
        self.build_button.remove_css_class('suggested-action')
        self.build_button.add_css_class('destructive-action')

    def switch_to_idle(self):
        self.build_button.set_child(self.idle_icon)
        shortcut_tooltips.set_tooltip(self.build_button, _('Save and build .pdf-file from document'), 'save_and_build')
        self.build_button.set_action_name('win.save-and-build')
        self.build_button.remove_css_class('destructive-action')
        self.build_button.add_css_class('suggested-action')

    def start_timer(self):
        self.timer_active = True
        # 1000ms 间隔（原 500ms）：显示精度为 1 秒，无需更高频率。
        # 用 timeout_add_seconds 允许系统聚合定时器以节省功耗。
        self._timer_timeout_id = GLib.timeout_add_seconds(1, self.increment_timer)

    def increment_timer(self):
        if self.timer_active:
            self.timer += 1000
            if self.timer // 1000 >= 1:
                self.timer_label.set_text('{}:{:02}'.format(self.timer // 60000, (self.timer % 60000) // 1000))
        else:
            self._timer_timeout_id = None
        return self.timer_active

    def stop_timer(self):
        self.timer_active = False
        if self._timer_timeout_id is not None:
            GLib.source_remove(self._timer_timeout_id)
            self._timer_timeout_id = None

    def reset_timer(self):
        self.timer = 0
        self.timer_label.set_text('0:00')

    def set_stage(self, name, index):
        # 阶段信息放 tooltip（N 递增，不写死 N/M；job 队列在构建中动态增长）。
        # 形如 “Stop building — LaTeX · Pass 2”。
        stage_text = '{} · {}'.format(name, _('Pass {}').format(index))
        self.build_button.set_tooltip_text('{} — {}'.format(self._building_tooltip_base, stage_text))

    def clear_stage(self):
        # 构建结束/停止：tooltip 回到 “Stop building” 基文本，去掉阶段信息。
        self.build_button.set_tooltip_text(self._building_tooltip_base)

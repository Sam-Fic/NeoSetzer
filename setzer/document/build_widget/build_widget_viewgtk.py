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
        # 存储 timeout id 以便文档关闭时直接移除,避免计时器在 widget 已销毁后
        # 仍每 500ms 触发 increment_timer(原实现仅靠 timer_active=False 让回调
        # 自行退出,但若文档关闭时构建仍在进行,stop_timer 不会被调用,计时器
        # 会永久泄漏)。
        self._timer_timeout_id = None
        self.state_change_count = 0
        # 跟踪 hide_result 的延迟回调 id。原实现不跟踪，widget 销毁时若仍有
        # 挂起的隐藏回调（duration 可达数秒），_hide_result 会访问已销毁的
        # result_popover 触发 GTK 警告。destroy 时一并取消。
        self._hide_result_timeout_id = None

        self.build_button = Gtk.Button()

        # idle 状态直接用一个 Image 作 child，与标题栏其它 Gtk.Button(icon_name=...)
        # 的构造完全一致，避免多余的 Gtk.Box 包裹导致图标对齐/尺寸不一致。
        self.idle_icon = Gtk.Image(icon_name='system-run-symbolic')
        self.build_button.set_child(self.idle_icon)

        self.active_icon = Gtk.Image(icon_name='process-stop-symbolic')
        self.timer_label = Gtk.Label(label='0:00')
        self.active_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.active_box.append(self.active_icon)
        self.active_box.append(self.timer_label)

        self.build_button.set_tooltip_text(_('Save and build .pdf-file from document') + ' (F5)')
        # 保留 suggested-action 以显示绿色强调色。idle_box 嵌套已移除，且 build_button
        # 的 child 现在直接是 Image（与标题栏其它图标按钮构造一致），宽度由 GTK 自然决定。
        self.build_button.add_css_class('suggested-action')

        # clean_button 嵌套在 BuildWidgetView(Gtk.Box) 里，不是 HeaderBar 直接子控件，
        # 不会被 HeaderBar 自动 flat，需显式加 .flat 与标题栏图标按钮保持一致外观。
        self.clean_button = Gtk.Button()
        self.clean_button.set_child(Gtk.Image(icon_name='edit-clear-all-symbolic'))
        self.clean_button.set_tooltip_text(_('Cleanup build files'))
        self.clean_button.add_css_class('flat')

        self.result_label = Gtk.Label(label='')

        self.result_popover = Gtk.Popover()
        self.result_popover.set_child(self.result_label)
        self.result_popover.set_parent(self.build_button)
        self.result_popover.set_autohide(False)
        self.result_popover.set_has_arrow(True)
        self.result_popover.set_position(Gtk.PositionType.BOTTOM)

        self.append(self.clean_button)
        self.prepend(self.build_button)

        # widget 销毁时取消所有挂起的 timeout（构建计时器 + 结果隐藏回调），
        # 避免回调访问已释放的 result_popover / timer_label。
        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        self.stop_timer()
        if self._hide_result_timeout_id is not None:
            GObject.source_remove(self._hide_result_timeout_id)
            self._hide_result_timeout_id = None

    def switch_to_building(self):
        self.build_button.set_child(self.active_box)
        self.build_button.set_tooltip_text(_('Stop building'))
        self.build_button.set_action_name(None)
        self.build_button.remove_css_class('suggested-action')
        self.build_button.add_css_class('destructive-action')

    def switch_to_idle(self):
        self.build_button.set_child(self.idle_icon)
        self.build_button.set_tooltip_text(_('Save and build .pdf-file from document') + ' (F5)')
        self.build_button.set_action_name('win.save-and-build')
        self.build_button.remove_css_class('destructive-action')
        self.build_button.add_css_class('suggested-action')

    def start_timer(self):
        self.timer_active = True
        self._timer_timeout_id = GObject.timeout_add(500, self.increment_timer)

    def increment_timer(self):
        if self.timer_active:
            self.timer += 500
            if self.timer // 1000 >= 1:
                self.timer_label.set_text('{}:{:02}'.format(self.timer // 60000, (self.timer % 60000) // 1000))
        else:
            self._timer_timeout_id = None
        return self.timer_active

    def stop_timer(self):
        self.timer_active = False
        if self._timer_timeout_id is not None:
            GObject.source_remove(self._timer_timeout_id)
            self._timer_timeout_id = None

    def reset_timer(self):
        self.timer = 0
        self.timer_label.set_text('0:00')

    def show_result(self, text=''):
        self.state_change_count += 1
        self.result_label.set_markup(text)
        if self.get_parent() is None or text == '':
            self.result_popover.popdown()
            return
        self.result_popover.popup()

    def has_result(self):
        return self.result_popover.get_visible()

    def hide_result(self, duration):
        self.state_change_count += 1
        # 取消前一个挂起的隐藏回调，避免多个 timeout 叠加（连续构建时
        # 旧回调仍会 popdown 刚弹出的新结果）。
        if self._hide_result_timeout_id is not None:
            GObject.source_remove(self._hide_result_timeout_id)
        self._hide_result_timeout_id = GObject.timeout_add(duration, self._hide_result, self.state_change_count)

    def hide_result_now(self):
        self.state_change_count += 1
        if self._hide_result_timeout_id is not None:
            GObject.source_remove(self._hide_result_timeout_id)
            self._hide_result_timeout_id = None
        self._hide_result(self.state_change_count)

    def _hide_result(self, state_change_count):
        self._hide_result_timeout_id = None
        if self.state_change_count == state_change_count:
            self.result_popover.popdown()
        return False

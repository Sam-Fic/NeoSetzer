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

"""批注输入 popover：右键「Add AI Annotation…」与批注面板「编辑」共用。

Gtk.Popover（非模态、锚定选区/按钮）：TextView 写要求 + 预制提示词
菜单（AI 页工具栏同款 MenuButton）+ 保存按钮。保存回调
(comment, preset_name)；preset_name 为 None 表示自定义（仅用户批注，
无预制指令）。
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gio, Gtk, Pango

from setzer.helpers.text_input_card import create_text_input_card


class AnnotationPopover(Gtk.Popover):

    def __init__(self):
        Gtk.Popover.__init__(self)
        self.set_autohide(True)
        self._on_save_cb = None
        self._root_gesture = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(320, -1)

        title = Gtk.Label(label=_('AI Annotation'))
        title.add_css_class('heading')
        title.set_xalign(0.0)
        box.append(title)

        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_accepts_tab(False)
        self.text_view.set_size_request(-1, 80)
        # 与「发送到 AI Agent」弹窗的 prompt 区同款圆角输入卡片
        box.append(create_text_input_card(
            self.text_view, min_height=80, max_height=160))

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # 预设选择：与 AI 页工具栏的 preset 菜单同款组件（MenuButton +
        # label + 下箭头，popover 用 Gio.Menu 有状态 action，选中项自动
        # 打圆点）。
        self.preset_button = Gtk.MenuButton()
        self.preset_button.set_tooltip_text(_('Preset instruction'))
        self.preset_button.set_hexpand(True)
        self.preset_button.set_can_focus(False)

        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.preset_label = Gtk.Label(label=_('Custom (no preset)'))
        self.preset_label.set_xalign(0.0)
        self.preset_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.preset_label.set_hexpand(True)
        preset_box.append(self.preset_label)
        arrow_icon = Gtk.Image(icon_name='pan-down-symbolic')
        arrow_icon.set_pixel_size(16)
        preset_box.append(arrow_icon)
        self.preset_button.set_child(preset_box)
        button_row.append(self.preset_button)

        self._preset_action = Gio.SimpleAction.new_stateful(
            'select', GLib.VariantType('s'), GLib.Variant('s', ''))
        self._preset_action.connect('change-state', self._on_preset_change_state)
        preset_group = Gio.SimpleActionGroup()
        preset_group.add_action(self._preset_action)
        self.preset_button.insert_action_group('preset', preset_group)

        # 纯图标保存按钮（名称由 tooltip 展示）；图标与项目其他保存按钮一致。
        self.save_button = Gtk.Button(icon_name='document-save-symbolic')
        self.save_button.add_css_class('suggested-action')
        self.save_button.set_tooltip_text(_('Save Annotation'))
        self.save_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Save Annotation')])
        self.save_button.connect('clicked', self._on_save_clicked)
        button_row.append(self.save_button)

        box.append(button_row)

        self.set_child(box)
        self.connect('map', self._on_map)
        self.connect('unmap', self._on_unmap)

    def set_presets(self, presets):
        '''presets: [{'name', 'instruction'}, ...]；首项恒为「自定义」。'''
        menu = Gio.Menu()
        item = Gio.MenuItem.new(_('Custom (no preset)'), 'preset.select')
        item.set_action_and_target_value('preset.select', GLib.Variant('s', ''))
        menu.append_item(item)
        for preset in (presets or []):
            name = preset.get('name', '')
            item = Gio.MenuItem.new(name, 'preset.select')
            item.set_action_and_target_value('preset.select', GLib.Variant('s', name))
            menu.append_item(item)
        self.preset_button.set_menu_model(menu)
        popover = self.preset_button.get_popover()
        if popover is not None:
            popover.add_css_class('menu')

    def present_for(self, parent, rect, comment='', preset=None, on_save_cb=None):
        '''锚定 parent（rect 非 None 时指向该 Gdk.Rectangle）并预填内容。'''
        self._on_save_cb = on_save_cb
        self.text_view.get_buffer().set_text(comment or '')
        self._preset_action.change_state(GLib.Variant('s', preset or ''))
        self.unparent()
        self.set_parent(parent)
        if rect is not None:
            self.set_pointing_to(rect)
        self.popup()

    def _on_map(self, popover):
        self.text_view.grab_focus()
        # 兜底「点击弹窗外关闭」：映射期间在根窗口 capture 阶段拦截所有按下。
        # set_autohide 只在部分场景可靠，这里保证同窗口任意区域按下即收起。
        root = self.get_root()
        if root is not None and self._root_gesture is None:
            gesture = Gtk.GestureClick()
            gesture.set_button(0)
            gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            gesture.connect('pressed', self._on_root_pressed)
            root.add_controller(gesture)
            self._root_gesture = gesture

    def _on_unmap(self, popover):
        if self._root_gesture is not None:
            root = self._root_gesture.get_widget()
            if root is not None:
                root.remove_controller(self._root_gesture)
            self._root_gesture = None

    def _on_root_pressed(self, gesture, n_press, x, y):
        root = self.get_root()
        if root is None:
            return
        picked = root.pick(x, y, Gtk.PickFlags.DEFAULT)
        if picked is not None and (picked == self or picked.is_ancestor(self)):
            return
        # 消费掉这次按下，避免点击下方控件（如同一编辑按钮）立刻重新弹出。
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.popdown()

    def _on_preset_change_state(self, action, value):
        action.set_state(value)
        name = value.get_string()
        self.preset_label.set_text(name or _('Custom (no preset)'))

    def _on_save_clicked(self, button):
        cb = self._on_save_cb
        self._on_save_cb = None
        buffer = self.text_view.get_buffer()
        comment = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        preset = self._preset_action.get_state().get_string() or None
        self.popdown()
        if cb is not None:
            cb(comment, preset)
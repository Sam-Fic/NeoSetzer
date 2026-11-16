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
下拉 + 保存按钮。保存回调 (comment, preset_name)；preset_name 为 None
表示自定义（仅用户批注，无预制指令）。
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


class AnnotationPopover(Gtk.Popover):

    def __init__(self):
        Gtk.Popover.__init__(self)
        self.set_autohide(True)
        self._on_save_cb = None
        self._preset_names = []

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
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(80)
        scrolled.set_max_content_height(160)
        scrolled.set_child(self.text_view)
        scrolled.add_css_class('card')
        box.append(scrolled)

        self.preset_dropdown = Gtk.DropDown()
        self.preset_dropdown.set_tooltip_text(_('Preset instruction'))
        box.append(self.preset_dropdown)

        self.save_button = Gtk.Button(label=_('Save Annotation'))
        self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked', self._on_save_clicked)
        box.append(self.save_button)

        self.set_child(box)
        self.connect('map', self._on_map)

    def set_presets(self, presets):
        '''presets: [{'name', 'instruction'}, ...]；首项恒为「自定义」。'''
        self._preset_names = [None] + [p.get('name') for p in (presets or [])]
        labels = [_('Custom (no preset)')] + [p.get('name', '') for p in (presets or [])]
        self.preset_dropdown.set_model(Gtk.StringList.new(labels))
        self.preset_dropdown.set_selected(0)

    def present_for(self, parent, rect, comment='', preset=None, on_save_cb=None):
        '''锚定 parent（rect 非 None 时指向该 Gdk.Rectangle）并预填内容。'''
        self._on_save_cb = on_save_cb
        self.text_view.get_buffer().set_text(comment or '')
        index = self._preset_names.index(preset) if preset in self._preset_names else 0
        self.preset_dropdown.set_selected(index)
        self.unparent()
        self.set_parent(parent)
        if rect is not None:
            self.set_pointing_to(rect)
        self.popup()

    def _on_map(self, popover):
        self.text_view.grab_focus()

    def _on_save_clicked(self, button):
        cb = self._on_save_cb
        self._on_save_cb = None
        buffer = self.text_view.get_buffer()
        comment = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        selected = self.preset_dropdown.get_selected()
        preset = self._preset_names[selected] if 0 <= selected < len(self._preset_names) else None
        self.popdown()
        if cb is not None:
            cb(comment, preset)
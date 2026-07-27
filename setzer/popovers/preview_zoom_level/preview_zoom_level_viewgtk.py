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
from gi.repository import Gtk, Gio, GLib


class PreviewZoomLevelView(Gtk.PopoverMenu):
    '''Preview zoom-level popover (triggered from the preview panel).

    Built from a ``Gio.Menu`` model on a native ``Gtk.PopoverMenu`` — the same
    form as the hamburger menu and the context menu. Section breaks are
    rendered automatically by ``append_section``; actions are real GActions
    on the main window (``preview-fit-mode`` for the fit items,
    ``preview-set-zoom-level`` for the numeric zoom levels) — both stateful
    actions whose state drives the checkmark via GTK.
    '''

    LEVELS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]

    def __init__(self, popover_manager):
        Gtk.PopoverMenu.__init__(self)
        self.set_size_request(220, -1)
        self.set_menu_model(self._build_model())

    def _build_model(self):
        model = Gio.Menu()

        fit_section = Gio.Menu()
        for label, target in (
            (_('Fit to Width'), 'width'),
            (_('Fit to Text Width'), 'text-width'),
            (_('Fit to Height'), 'height'),
        ):
            item = Gio.MenuItem.new(label, 'win.preview-fit-mode')
            item.set_action_and_target_value(
                'win.preview-fit-mode',
                GLib.Variant('s', target)
            )
            fit_section.append_item(item)
        model.append_section(None, fit_section)

        # 普通 action+target 项。对钩由有状态的 win.preview-set-zoom-level
        # action 的 state 自动绘制（与 set-build-interpreter 菜单一致）：GTK 在
        # target 等于 action 当前 state 的项前显示对钩。无需在菜单模型里拼
        # 字面字符或手动设 role/:checked。
        zoom_section = Gio.Menu()
        for level in self.LEVELS:
            label = '{0:.0f}%'.format(level * 100)
            item = Gio.MenuItem.new(label, 'win.preview-set-zoom-level')
            item.set_action_and_target_value(
                'win.preview-set-zoom-level',
                GLib.Variant('d', level)
            )
            zoom_section.append_item(item)
        model.append_section(None, zoom_section)

        return model

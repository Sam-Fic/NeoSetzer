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
from gi.repository import Gtk, GLib


class AsyncSvg(Gtk.Picture):

    def __init__(self, filename, width, height):
        Gtk.Picture.__init__(self)

        self.filename = filename
        self.set_size_request(width, height)

        GLib.idle_add(self.load_image)

    def load_image(self):
        # idle 回调必须显式返回 False,否则在部分 PyGObject 版本中
        # None 会被当作“继续调用”,导致该回调被无限重复触发、CPU 占满。
        # 用 try/except 守卫:若 widget 在 idle 触发前已被销毁,
        # set_filename 会抛异常,静默忽略即可。
        try:
            self.set_filename(self.filename)
        except Exception:
            pass
        return False



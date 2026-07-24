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
from gi.repository import Pango
from gi.repository import Gtk

from setzer.app.service_locator import ServiceLocator


class FontManager():

    main_window = None
    default_font_string = None
    font_string = None
    zoom_level = 1.0
    # font_desc 缓存：get_font_desc 被 actions._update_actions_now（每次光标/字体
    # 变化触发）调用做缩放边界检查，原实现每次 Pango.FontDescription.from_string
    # 重新解析 font_string。font_string 仅在 zoom in/out/reset（经
    # propagate_font_setting）时变化，两次变化间结果恒定。在 propagate_font_setting
    # 中重建缓存，get_font_desc 直接返回。调用者只读 get_size()，无修改风险。
    _font_desc = None

    def init(main_window):
        FontManager.main_window = main_window

        FontManager.default_font_string = 'monospace 11'
        FontManager.font_string = 'monospace 11'
        FontManager._font_desc = None

    def propagate_font_setting():
        # font_string 可能已变（zoom in/out/reset），重建缓存供本方法及后续
        # get_font_desc 调用复用。
        FontManager._font_desc = Pango.FontDescription.from_string(FontManager.font_string)
        font_desc = FontManager._font_desc
        font_size = font_desc.get_size() / Pango.SCALE
        font_family = font_desc.get_family()

        data = ('textview.monospace { font-size: ' + str(font_size) + 'pt; font-family: ' + font_family + '; }\n'
                'listbox.monospace row, listbox.monospace row label { font-size: ' + str(font_size) + 'pt; font-family: ' + font_family + '; }')
        FontManager.main_window.css_provider_font_size.load_from_string(data)

        settings = ServiceLocator.get_settings()
        if settings.get_value('preferences', 'use_system_font'):
            font_string = FontManager.default_font_string
        else:
            font_string = settings.get_value('preferences', 'font_string')
        font_desc = Pango.FontDescription.from_string(font_string)
        FontManager.zoom_level = FontManager.get_font_desc().get_size() / font_desc.get_size()

    def get_char_width(text_view, char='A'):
        context = text_view.get_pango_context()
        layout = Pango.Layout.new(context)
        layout.set_text(char, -1)
        char_width, line_height_1 = layout.get_pixel_size()
        return char_width

    def get_line_height(text_view):
        context = text_view.get_pango_context()
        metrics = context.get_metrics()
        return (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE

    def get_font_desc():
        if FontManager._font_desc is None:
            FontManager._font_desc = Pango.FontDescription.from_string(FontManager.font_string)
        return FontManager._font_desc

    def get_system_font():
        return FontManager.default_font_string



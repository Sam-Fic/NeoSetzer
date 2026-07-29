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

import json

from setzer.app.service_locator import ServiceLocator


class FontManager():

    main_window = None
    default_font_string = None
    font_string = None
    zoom_level = 1.0
    # 保存的编辑器字号缩放倍率：1.0 = 默认。与 font_string 分离，使 system font 模式
    # 下的缩放偏好也能跨重启持久化。
    saved_zoom_level = 1.0
    # 字体缩放范围与步长：原硬编码在 document_controller.py 的 on_scroll 中
    # （6pt 下限、24pt 上限、1.1× 步长）。提取为模块级常量后，document_controller
    # 与未来其他缩放入口（如菜单项）共用同一份定义，避免值漂移。
    FONT_SIZE_MIN_PT = 6
    FONT_SIZE_MAX_PT = 24
    FONT_ZOOM_FACTOR = 1.1
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

        # font_family 直接拼进 CSS 字符串存在注入风险（字体名含 " ; } 等会破坏
        # CSS 结构）。虽然 font_family 来自 Pango 解析后的字体名（通常安全），
        # 但 font_string 是用户偏好（settings.json），攻击者若能改 settings 即
        # 可注入恶意 CSS。用 json.dumps 产生双引号包裹 + 反斜杠/控制字符转义
        # 的字符串，JSON 字符串转义是 CSS 字符串转义的子集，安全且无副作用。
        # 例：json.dumps('Monospace') -> '"Monospace"'，直接拼进 font-family: ...。
        quoted_family = json.dumps(font_family)
        data = ('textview.monospace { font-size: ' + str(font_size) + 'pt; font-family: ' + quoted_family + '; }\n'
                'listbox.monospace row, listbox.monospace row label { font-size: ' + str(font_size) + 'pt; font-family: ' + quoted_family + '; }')
        FontManager.main_window.css_provider_font_size.load_from_string(data)

        settings = ServiceLocator.get_settings()
        if settings.get_value('preferences', 'use_system_font'):
            font_string = FontManager.default_font_string
        else:
            font_string = settings.get_value('preferences', 'font_string')
        font_desc = Pango.FontDescription.from_string(font_string)
        # zoom_level = 当前（含缩放）字号 / 基准（无缩放）字号。
        # 注意此处两个 font_string 是不同变量：
        #   - FontManager.font_string（类属性，第 57 行用过）：含缩放的当前字号，
        #     由 zoom in/out/reset 修改；FontManager.get_font_desc() 返回的
        #     _font_desc 缓存即基于它，故分子是「缩放后字号」。
        #   - font_string（局部变量，第 75-77 行赋值）：基准字号（系统默认或
        #     用户偏好），不含缩放；font_desc 基于它，故分母是「基准字号」。
        # 两者命名相同但作用域不同，勿混淆——局部 font_string 不会改类属性。
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

    def apply_zoom_to_font(base_font_string, zoom_factor):
        '''Apply a zoom factor (e.g., 1.2 for 120%) to a base font string and return
        the resulting font string. This is used when loading the font on startup
        to restore the saved zoom level.'''
        font_desc = Pango.FontDescription.from_string(base_font_string)
        current_size = font_desc.get_size()
        new_size = int(current_size * zoom_factor)
        # Clamp to valid range
        new_size = max(int(FontManager.FONT_SIZE_MIN_PT * Pango.SCALE),
                       min(int(FontManager.FONT_SIZE_MAX_PT * Pango.SCALE), new_size))
        font_desc.set_size(new_size)
        return font_desc.to_string()



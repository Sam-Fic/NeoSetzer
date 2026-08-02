#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from gi.repository import Gdk, Gtk, Adw


class ColorManager():

    main_window = None
    # 颜色缓存：name -> Gdk.RGBA（lookup_color 的原始结果）。
    # get_ui_color 在每帧 draw 中被多次调用（gutter/preview），lookup_color 是
    # C 级 CSS 级联查找。颜色仅在主题切换时变化，缓存命中时省去级联查找。
    # 注意：get_ui_color 返回的是缓存对象的**引用**（非副本），调用者不可
    # 修改返回值。已审计全部 20 处调用者均只读；如未来需修改，调用者应自行
    # Gdk.RGBA() 复制。主题切换时 on_theme_changed 清空整个缓存。
    _color_cache = {}
    # 颜色字符串缓存：name -> '#rrggbb' / '#rrggbbaa'。get_ui_color_string 在
    # help_panel.update_colors 等处调用，原实现每次 get_ui_color + 3×_to_byte +
    # format 重新格式化。颜色仅在主题切换时变化，与 _color_cache 同生命周期失效。
    _color_string_cache = {}
    _color_string_alpha_cache = {}

    # 自定义颜色名 -> 内置 Libadwaita/GTK 调色板同名回退（语义相近）
    fallback_colors = {
        'window_fg_color': 'window_fg_color',
        'window_bg_color': 'window_bg_color',
        'view_fg_color': 'view_fg_color',
        'view_bg_color': 'view_bg_color',
        'view_hover_color': 'view_hover_color',
        'borders': 'borders',
        'error_color': 'error_color',
        'link_color': 'link_color',
        'link_color_visited': 'visited_link_color',
        'link_color_active': 'link_color',
        'fg_color_light': 'window_fg_color',
        'popover_bg_color': 'popover_bg_color',
        'list_selection_color': 'accent_color',
        'list_selection_hover_color': 'accent_color',
        'ac_bg': 'view_bg_color',
        'ac_selection_bg': 'accent_color',
        'ac_text': 'view_fg_color',
        'highlight_tag_textview': 'accent_color',
        'highlight_tag_preview': 'accent_color',
        'highlight_begin_end_textview': 'accent_color',
        'dim_fg_color': 'view_fg_color',
        'line_highlighting_color': 'accent_color',
        'code_folding_hover': 'accent_color',
    }

    def init(main_window):
        ColorManager.main_window = main_window
        ColorManager._color_cache = {}
        ColorManager._color_string_cache = {}
        ColorManager._color_string_alpha_cache = {}
        # 主题切换（明↔暗）时清空缓存，下次 get_ui_color 重新查找
        Adw.StyleManager.get_default().connect('notify::dark', ColorManager.on_theme_changed)

    def on_theme_changed(style_manager, pspec):
        ColorManager._color_cache = {}
        ColorManager._color_string_cache = {}
        ColorManager._color_string_alpha_cache = {}

    def get_ui_color(name):
        '''返回 UI 主题色（Gdk.RGBA）。

        返回缓存对象的**引用**，调用者不可修改返回值——修改会污染缓存影响
        所有后续调用（主题切换前一直生效）。如需修改（如调整 alpha），请
        自行 `rgba = Gdk.RGBA()` 复制后再改。已审计全部 20 处调用者，均
        只读不修改，原实现每次返回副本是纯分配浪费——get_ui_color 在每帧
        draw 中被多次调用（gutter/preview/highlight），省去 Gdk.RGBA 分配
        + 4 次属性赋值可显著降低高频绘制路径的 GC 压力。
        '''
        cached = ColorManager._color_cache.get(name)
        if cached is not None:
            return cached

        # main_window 尚未 init（应用启动早期，ColorManager.init 调用前）或
        # 已被销毁（关闭过程中）时，直接返回不透明黑色兜底，避免
        # AttributeError: 'NoneType' object has no attribute 'get_style_context'。
        # 不缓存此兜底值——一旦 main_window 就绪，下次调用应走真正的
        # lookup_color 并缓存正确结果；若缓存黑色会在主题切换前一直错位。
        if ColorManager.main_window is None:
            return Gdk.RGBA(0, 0, 0, 1)

        style_context = ColorManager.main_window.get_style_context()
        found, rgba = style_context.lookup_color(name)
        if not found:
            # 回退到 GTK/Libadwaita 内置调色板同名色
            fallback = ColorManager.fallback_colors.get(name, name)
            found, rgba = style_context.lookup_color(fallback)
        if not found:
            # 最后兜底：不透明黑色，避免崩溃
            rgba = Gdk.RGBA(0, 0, 0, 1)

        ColorManager._color_cache[name] = rgba
        return rgba

    def _to_byte(value):
        # Theme colors coming out of color computations (mix/shade/alpha) can
        # be slightly out of the [0, 1] range. ``format(v, '02x')`` only sets a
        # *minimum* width, so an out-of-range component would emit 3+ hex
        # digits and produce a malformed color string (e.g. '#13c8e8b') that
        # Pango refuses to parse. Clamp and round to a valid byte.
        return max(0, min(255, int(round(value * 255))))

    def get_ui_color_string(name):
        s = ColorManager._color_string_cache.get(name)
        if s is not None:
            return s
        color_rgba = ColorManager.get_ui_color(name)
        s = '#{:02x}{:02x}{:02x}'.format(
            ColorManager._to_byte(color_rgba.red),
            ColorManager._to_byte(color_rgba.green),
            ColorManager._to_byte(color_rgba.blue))
        ColorManager._color_string_cache[name] = s
        return s

    def get_ui_color_string_with_alpha(name):
        s = ColorManager._color_string_alpha_cache.get(name)
        if s is not None:
            return s
        color_rgba = ColorManager.get_ui_color(name)
        s = '#{:02x}{:02x}{:02x}{:02x}'.format(
            ColorManager._to_byte(color_rgba.red),
            ColorManager._to_byte(color_rgba.green),
            ColorManager._to_byte(color_rgba.blue),
            ColorManager._to_byte(color_rgba.alpha))
        ColorManager._color_string_alpha_cache[name] = s
        return s



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

'''应用级 UI 缩放管理器（Ctrl+加/减/0，及右键菜单缩放按钮）。

机制：GtkSettings:gtk-xft-dpi（单位 1024 × dpi，-1 = 后端默认）。它控制
Pango 文字渲染分辨率，与 GNOME「文本缩放」无障碍机制同源：调整后菜单、
标签、按钮、编辑器（CSS 以 pt 定义字号）等文字整体随动，视觉上即缩放
整个应用。图标与像素级 padding 不随动——GTK4 没有整体变换 API，这与
系统文本缩放的行为一致，是该平台上「应用缩放」的通行做法。

与编辑器字号缩放（FontManager，Ctrl+滚轮）正交、可叠加：
编辑器有效字号 = 基准字号 × FontManager.zoom_level × UIZoomManager.zoom_level。

接管原则（尽量少干预系统设置）：
- 恰为 100% 时不写该设置，系统 DPI / 文本缩放的后续变化仍由系统接管；
- 进入非 100% 时以 init 时读到的原始值为基准（含 Windows 入口强制写的
  96×1024，见 setzer.in），写入 原始值 × 倍率；
- 回到 100% 时把原始值原样写回（保留 -1 哨兵），彻底交还系统。

已知取舍：若用户在应用打开期间修改系统文本缩放/DPI，非 100% 状态下不会
实时跟随（我们的写入会覆盖系统值）；回到 100% 即恢复接管前读到的原始值。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from setzer.helpers import zoom_math


class UIZoomManager():

    # 当前缩放倍率（1.0 = 100%）。所有读取方（状态栏、右键菜单、动作
    # enable 判定）统一从这里取值。
    zoom_level = 1.0
    # init 时注入的 Settings 引用（setzer.in activate() 中传入），
    # 持久化用。不走 ServiceLocator，保持模块可脱离其 gi 依赖单测。
    _settings = None
    # init 时读到的原始 gtk-xft-dpi（保留 -1 等哨兵原样）；None = 尚未 init。
    _base_dpi = None

    def init(settings):
        '''启动时恢复持久化的缩放。须在主窗口创建后调用（Gtk.Settings 需
        已有默认 Display），见 setzer.in 的 activate()。'''
        UIZoomManager._settings = settings
        try:
            saved = settings.get_value('preferences', 'ui_zoom_level')
        except Exception:
            saved = None
        UIZoomManager.zoom_level = zoom_math.clamp_level(saved)
        UIZoomManager._base_dpi = UIZoomManager._read_raw_dpi()
        # 100% 不接管系统设置（见模块 docstring）；仅非 100% 时写入。
        if not zoom_math.is_default(UIZoomManager.zoom_level):
            UIZoomManager._apply()

    def zoom_in():
        UIZoomManager._set_level(zoom_math.next_zoom_in(UIZoomManager.zoom_level))

    def zoom_out():
        UIZoomManager._set_level(zoom_math.next_zoom_out(UIZoomManager.zoom_level))

    def reset():
        UIZoomManager._set_level(1.0)

    def is_zoomed():
        '''是否偏离 100%（用于 reset 动作的 enable 判定）。'''
        return not zoom_math.is_default(UIZoomManager.zoom_level)

    def can_zoom_in():
        return zoom_math.can_zoom_in(UIZoomManager.zoom_level)

    def can_zoom_out():
        return zoom_math.can_zoom_out(UIZoomManager.zoom_level)

    def get_zoom_percent():
        '''状态栏 / 右键菜单按钮的当前缩放文本（如 '110%'）。'''
        return zoom_math.format_percent(UIZoomManager.zoom_level)

    def _set_level(level):
        level = zoom_math.clamp_level(level)
        if level == UIZoomManager.zoom_level:
            return False
        UIZoomManager.zoom_level = level
        UIZoomManager._apply()
        UIZoomManager._persist()
        return True

    def _persist():
        if UIZoomManager._settings is None:
            return
        try:
            # round(…, 6) 消除浮点尾差，避免长期反复缩放后存档值漂移。
            UIZoomManager._settings.set_value(
                'preferences', 'ui_zoom_level', round(UIZoomManager.zoom_level, 6))
        except Exception:
            pass

    def _read_raw_dpi():
        try:
            gtk_settings = Gtk.Settings.get_default()
            if gtk_settings is None:
                return None
            return gtk_settings.get_property('gtk-xft-dpi')
        except Exception:
            return None

    def _apply():
        '''把当前倍率写进 gtk-xft-dpi；100% 时把原始值原样写回。'''
        gtk_settings = None
        try:
            gtk_settings = Gtk.Settings.get_default()
        except Exception:
            return
        if gtk_settings is None or UIZoomManager._base_dpi is None:
            return
        try:
            if zoom_math.is_default(UIZoomManager.zoom_level):
                gtk_settings.set_property('gtk-xft-dpi', UIZoomManager._base_dpi)
            else:
                gtk_settings.set_property(
                    'gtk-xft-dpi',
                    zoom_math.zoomed_dpi(UIZoomManager._base_dpi, UIZoomManager.zoom_level))
        except Exception:
            # 属性不可写（后端限制）等异常下静默降级：缩放不生效，但
            # 百分比指示与持久化照常工作，应用不崩溃。
            pass

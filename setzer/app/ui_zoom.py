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

机制三部分，缺一不可（实测 GTK 4.22 + libadwaita 1.9）：

1. 文字：GtkSettings:gtk-xft-dpi（单位 1024 × dpi，-1 = 后端默认）。它与 GNOME
   「文本缩放」同源，只影响**未声明 CSS font-size** 的默认字体（菜单、标签、
   按钮等），这部分随动。
2. 图标与显式字号：CSS px/em 是设备无关量，**不随 dpi 变化** —— 只写 dpi 会得到
   「文字变大、图标原地不动」的错位。故按同一倍率生成一份镜像样式表（见
   helpers/zoom_css），以高于 USER 的优先级装载：主题的全部 -gtk-icon-size 声明
   按倍率取整重写，自研样式里的 icon-size/em 字号自动从 style_gtk.css 抽取镜像。
   100% 时不装 provider（与未启用缩放零差异）。
3. 编辑器：字号由 FontManager 以显式 pt 写进 CSS，不经过本模块 —— 它监听
   add_observer 回调，在同一倍率下重发自己的样式表。图标/em 字号与编辑器
   字号三条路正交可叠加。

图标像素级 padding、min-height 等几何量仍按系统比例（GTK4 无整体变换 API，
重写整套主题风险远大于收益）；图标变大后按钮靠内容自然撑开，不会被裁切。

与编辑器字号缩放（FontManager，Ctrl+滚轮）正交、可叠加：
编辑器有效字号 = 基准字号 × FontManager.zoom_level × UIZoomManager.zoom_level。

接管原则（尽量少干预系统设置）：
- 恰为 100% 时不写 dpi、不装 provider，系统 DPI / 文本缩放的后续变化仍由系统接管；
- 进入非 100% 时以 init 时读到的原始值为基准（含 Windows 入口强制写的
  96×1024，见 setzer.in），写入 原始值 × 倍率；
- 回到 100% 时把原始值原样写回（保留 -1 哨兵），彻底交还系统。

惰性自举（_ensure_init）：init() 的唯一显式调用点在 setzer.in —— 那是 meson
configure_file 的**模板**，生成的 builddir/setzer[_dev].py 若未重新构建就会是
旧副本（Python 包本身走 PYTHONPATH 实时生效，入口脚本却不会），届时本模块
的 init 从未被调用 → _base_dpi 为 None → _apply() 静默 no-op，表现为「快捷键
有反应、百分比在变、界面缩放纹丝不动」。故所有公开方法都先 _ensure_init()：
未显式 init 时自行从 ServiceLocator 取 Settings、读基准 dpi 并恢复持久化倍率，
使本特性不再依赖入口脚本的新旧。

已知取舍：若用户在应用打开期间修改系统文本缩放/DPI，非 100% 状态下不会
实时跟随（我们的写入会覆盖系统值）；回到 100% 即恢复接管前读到的原始值。
'''

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, Gdk

from setzer.helpers import zoom_css, zoom_math

# 镜像样式表的装载优先级：必须高于 USER（Setzer 自己的 style_gtk.css 与
# FontManager 的字号 provider 都装在 USER=800），否则我们的 `*` 兜底值无法
# 压过主题/自研声明。不在模块顶层取常量：gi-free 单测里 Gtk 是个空桩模块。
_USER_CSS_PRIORITY_FALLBACK = 800


class UIZoomManager():

    # 当前缩放倍率（1.0 = 100%）。所有读取方（状态栏、右键菜单、动作
    # enable 判定）统一从这里取值。
    zoom_level = 1.0
    # init 时注入的 Settings 引用（setzer.in activate() 中传入），持久化用。
    # 未经 init 时由 _locate_settings() 延迟 import ServiceLocator 兜底获取。
    _settings = None
    # init 时读到的原始 gtk-xft-dpi（保留 -1 等哨兵原样）；None = 基准未确定。
    _base_dpi = None
    # 基准 dpi 是否已确定（True 后不再重读，避免把我们自己写的值当基准）。
    _dpi_ready = False
    # 持久化倍率是否已恢复到 zoom_level（与 _dpi_ready 分离：headless 下无
    # Gtk.Settings 时仍要能恢复倍率、维护百分比与落盘，只是不接管 dpi）。
    _level_restored = False
    # 是否被显式 init 过。未 init（入口脚本陈旧）才允许走 ServiceLocator 兜底；
    # 显式 init(None) 表示「故意不落盘」，不得被兜底覆盖。
    _explicit_init = False
    # 图标/em 字号的镜像样式表 provider（None = 未装载；100% 恒为 None）。
    _css_provider = None
    _css_installed = False
    # 自研样式表里抽出的可镜像声明（None = 尚未读取；读取失败缓存为空元组）。
    _app_declarations = None
    # 倍率变化后的副作用回调（如 FontManager 重发编辑器字号 CSS）。
    _observers = []

    def add_observer(callback):
        '''登记「缩放倍率变化」回调（幂等）。回收入参为新的 zoom_level。

        持强引用、无反登记接口 ⇒ 只给 FontManager / 工作区级帮助面板这类
        全局单例用，临时控件不要登记。'''
        if callback not in UIZoomManager._observers:
            UIZoomManager._observers.append(callback)

    def get_zoom_level():
        '''当前倍率（供 FontManager 等读取方使用，顺带触发自举）。'''
        UIZoomManager._ensure_init()
        return UIZoomManager.zoom_level

    def init(settings=None):
        '''启动时恢复持久化的缩放。须在主窗口创建后调用（Gtk.Settings 需
        已有默认 Display），见 setzer.in 的 activate()。

        幂等，且与惰性自举等价 —— 显式调用只是把基准确定得更早（避免首帧
        后文字突然变大的一次跳变）。'''
        UIZoomManager._explicit_init = True
        UIZoomManager._ensure_init(settings)

    def _ensure_init(injected_settings=None):
        '''惰性完成一次性初始化；任何公开方法调用前都先过这里。'''
        if injected_settings is not None and not UIZoomManager._dpi_ready:
            UIZoomManager._settings = injected_settings
        if UIZoomManager._settings is None and not UIZoomManager._explicit_init:
            UIZoomManager._settings = UIZoomManager._locate_settings()
        if not UIZoomManager._level_restored:
            UIZoomManager._level_restored = True
            UIZoomManager.zoom_level = zoom_math.clamp_level(UIZoomManager._read_saved_level())
        if UIZoomManager._dpi_ready:
            return True
        base = UIZoomManager._read_raw_dpi()
        if base is None:
            # 还没有默认 Display（极早期调用 / headless）：不固化基准，下次再试。
            return False
        UIZoomManager._base_dpi = base
        UIZoomManager._dpi_ready = True
        # 100% 不接管系统设置（见模块 docstring）；仅非 100% 时写入。
        if not zoom_math.is_default(UIZoomManager.zoom_level):
            UIZoomManager._apply()
            UIZoomManager._notify_observers()
        return True

    def _locate_settings():
        '''未显式 init 时兜底取 Settings 单例（入口脚本陈旧的场景）。
        延迟 import：保持本模块可被 gi-free 单测直接加载。'''
        try:
            from setzer.app.service_locator import ServiceLocator
            return ServiceLocator.get_settings()
        except Exception:
            return None

    def _read_saved_level():
        if UIZoomManager._settings is None:
            return None
        try:
            return UIZoomManager._settings.get_value('preferences', 'ui_zoom_level')
        except Exception:
            return None

    def zoom_in():
        UIZoomManager._ensure_init()
        UIZoomManager._set_level(zoom_math.next_zoom_in(UIZoomManager.zoom_level))

    def zoom_out():
        UIZoomManager._ensure_init()
        UIZoomManager._set_level(zoom_math.next_zoom_out(UIZoomManager.zoom_level))

    def reset():
        UIZoomManager._ensure_init()
        UIZoomManager._set_level(1.0)

    def is_zoomed():
        '''是否偏离 100%（用于 reset 动作的 enable 判定）。'''
        UIZoomManager._ensure_init()
        return not zoom_math.is_default(UIZoomManager.zoom_level)

    def can_zoom_in():
        UIZoomManager._ensure_init()
        return zoom_math.can_zoom_in(UIZoomManager.zoom_level)

    def can_zoom_out():
        UIZoomManager._ensure_init()
        return zoom_math.can_zoom_out(UIZoomManager.zoom_level)

    def get_zoom_percent():
        '''状态栏 / 右键菜单按钮的当前缩放文本（如 '110%'）。'''
        UIZoomManager._ensure_init()
        return zoom_math.format_percent(UIZoomManager.zoom_level)

    def _set_level(level):
        UIZoomManager._ensure_init()
        level = zoom_math.clamp_level(level)
        if level == UIZoomManager.zoom_level:
            return False
        UIZoomManager.zoom_level = level
        UIZoomManager._apply()
        UIZoomManager._persist()
        UIZoomManager._notify_observers()
        return True

    def _notify_observers():
        for callback in list(UIZoomManager._observers):
            try:
                callback(UIZoomManager.zoom_level)
            except Exception:
                # 副作用失败（如主窗口尚未就绪）不影响缩放本身；该读取方下次
                # 重建样式时自然会拿到正确倍率。
                pass

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
        '''把当前倍率写进 gtk-xft-dpi（文字）与镜像样式表（图标/em 字号）；
        100% 时把原始 dpi 原样写回并卸掉 provider。'''
        gtk_settings = None
        try:
            gtk_settings = Gtk.Settings.get_default()
        except Exception:
            pass
        if gtk_settings is not None and UIZoomManager._base_dpi is not None:
            try:
                if zoom_math.is_default(UIZoomManager.zoom_level):
                    gtk_settings.set_property('gtk-xft-dpi', UIZoomManager._base_dpi)
                else:
                    gtk_settings.set_property(
                        'gtk-xft-dpi',
                        zoom_math.zoomed_dpi(UIZoomManager._base_dpi, UIZoomManager.zoom_level))
            except Exception:
                # 属性不可写（后端限制）等异常下静默降级：文字缩放不生效，但
                # 百分比指示与持久化照常工作，应用不崩溃。
                pass
        UIZoomManager._apply_icon_zoom()

    def _apply_icon_zoom():
        '''装载/刷新/卸载图标与 em 字号的镜像样式表（见 helpers/zoom_css）。'''
        try:
            display = Gdk.Display.get_default()
        except Exception:
            return
        if display is None:
            return
        css = zoom_css.build_css(
            UIZoomManager.zoom_level, UIZoomManager._load_app_declarations())
        try:
            if not css:
                if UIZoomManager._css_provider is not None:
                    Gtk.StyleContext.remove_provider_for_display(
                        display, UIZoomManager._css_provider)
                UIZoomManager._css_provider = None
                UIZoomManager._css_installed = False
                return
            if UIZoomManager._css_provider is None:
                UIZoomManager._css_provider = Gtk.CssProvider()
            # load_from_string 整体替换内容 ⇒ 同一 provider 反复装载即是刷新，
            # 不会堆积 display 级 provider。
            UIZoomManager._css_provider.load_from_string(css)
            if not UIZoomManager._css_installed:
                Gtk.StyleContext.add_provider_for_display(
                    display, UIZoomManager._css_provider,
                    getattr(Gtk, 'STYLE_PROVIDER_PRIORITY_USER',
                            _USER_CSS_PRIORITY_FALLBACK) + 100)
                UIZoomManager._css_installed = True
        except Exception:
            # 样式解析失败（主题改名导致选择器非法等）：图标不随动，文字缩放、
            # 百分比与持久化照常。丢弃这个 provider，下次缩放重建。
            UIZoomManager._css_provider = None
            UIZoomManager._css_installed = False

    def _load_app_declarations():
        '''从自研样式表（style_gtk.css）抽取需镜像的声明，读一次缓存。

        与 workspace_viewgtk 装载的是同一份文件；读不到就只用主题基准表，
        代价是那几处自研图标/字号不随动（不会报错）。'''
        if UIZoomManager._app_declarations is not None:
            return UIZoomManager._app_declarations
        declarations = ()
        try:
            from setzer.app.service_locator import ServiceLocator
            path = os.path.join(ServiceLocator.get_resources_path(), 'style_gtk.css')
            with open(path, encoding='utf-8') as handle:
                declarations = tuple(
                    zoom_css.parse_scalable_declarations(handle.read()))
        except Exception:
            declarations = ()
        UIZoomManager._app_declarations = declarations
        return declarations

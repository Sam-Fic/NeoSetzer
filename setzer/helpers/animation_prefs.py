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

'''「减弱动画」偏好（preferences.reduce_animations）的中枢。

覆盖范围：侧栏/预览栏的滑入滑出、所有 Gtk.Stack 与 Gtk.Revealer 的过渡、
侧栏列表的滚动动画、SyncTeX 高亮的逐帧淡出。
**不覆盖**窗口与对话框（如偏好设置窗口）自身的打开/关闭动画 —— 那是窗口
管理器和 Adw.Window/Adw.Dialog 的呈现动画，用户明确要求保留。

因此本模块绝不做「全局常关 gtk-enable-animations」：那会连窗口动画一起关掉。
取而代之：
  - 过渡类（Stack/Revealer）：把 transition-type 置 NONE、duration 置 0；
  - libadwaita 的 Adw.*SplitView 没有 transition 属性，只能在 set_show_sidebar
    这一次同步调用期间临时关掉 gtk-enable-animations（AdwAnimation 在
    update_state 时读到该设置即直接跳到末值），调用返回立刻恢复，窗口动画
    不受影响 —— 见 :func:`no_transition` / :func:`set_show_sidebar`；
  - 逐帧淡出（tick callback / timeout 循环）：调用方用 :func:`is_reduced`
    判断后直接进末态。

惰性自举（_ensure_installed）与 setzer/app/ui_zoom.py 同例：显式 install() 的
唯一调用点在 setzer.in，而 setzer.in 是 meson configure_file 的模板，未重新
构建时生成物可能是旧副本；故所有公开入口都先尝试从 ServiceLocator 兜底取得
Settings，使本特性不依赖入口脚本的新旧。

gi 导入做成 try/except：本模块需要能被无界面单测直接 import（测试注入假 Gtk）。
'''

import weakref
from contextlib import contextmanager

try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
except Exception:  # pragma: no cover - 仅在 gi/typelib 缺失时走到
    Gtk = None

# 经过 set_transition 登记过的控件。弱引用集合：控件销毁后自动摘除，
# 否则每个文档的搜索栏 revealer 都会被本模块永久持有。
_registered = weakref.WeakSet()

_reduced = False
_settings = None
_observing = False

# Gtk.StackTransitionType.NONE 与 Gtk.RevealerTransitionType.NONE 同为 0，
# 且 PyGObject 接受把 int 赋给枚举属性，故无需按控件类型区分取值。
_NONE = 0


def install(settings):
    '''启动时接入 Settings（见 setzer.in 的 activate()）。幂等。'''
    global _settings, _reduced, _observing
    # 先读值再赋值：读失败时不留半初始化的模块状态（_settings 已置但没接上
    # 观察者 → 惰性自举永远不再重试，偏好从此失效）。
    reduced = bool(settings.get_value('preferences', 'reduce_animations'))
    _settings = settings
    _reduced = reduced
    if not _observing:
        settings.connect('settings_changed', _on_settings_changed)
        _observing = True
    apply_mode()


def _ensure_installed():
    '''未显式 install 时兜底取 Settings 单例（入口脚本陈旧的场景）。

    取不到就静默返回（_reduced 维持 False = 保持既有动画行为），下次调用重试。
    '''
    global _settings
    if _observing or _settings is not None:
        return
    try:
        from setzer.app.service_locator import ServiceLocator
        settings = ServiceLocator.get_settings()
    except Exception:
        return
    if settings is None:
        return
    try:
        install(settings)
    except Exception:
        _settings = None


def is_reduced():
    '''当前是否应减弱动画。逐帧淡出与滚动动画的调用方直接读它。'''
    _ensure_installed()
    return _reduced


def _on_settings_changed(observable, parameter=None):
    section, item, _value = parameter or (None, None, None)
    if section != 'preferences' or item not in (None, 'reduce_animations'):
        return
    global _reduced
    reduced = bool(_settings.get_value('preferences', 'reduce_animations'))
    if reduced == _reduced:
        return
    _reduced = reduced
    apply_mode()


def set_transition(widget, transition_type, duration=200):
    '''代替 `set_transition_type()` + `set_transition_duration()`。

    把「本应有」的过渡记在控件上，再按当前减弱状态落地；控件被登记后，
    用户中途切换偏好时 apply_mode() 会一并改回来。切换偏好后新建的控件
    （如新文档的搜索栏）在本函数内即时拿到正确状态，无需重建界面。
    '''
    _ensure_installed()
    widget._setzer_transition = (transition_type, duration)
    _registered.add(widget)
    _apply_widget(widget)


def apply_mode():
    '''把当前减弱状态重新落到所有已登记控件上。'''
    for widget in list(_registered):
        _apply_widget(widget)


def _apply_widget(widget):
    desired = getattr(widget, '_setzer_transition', None)
    if desired is None:
        return
    transition_type, duration = desired
    try:
        if _reduced:
            widget.set_transition_type(_NONE)
            widget.set_transition_duration(0)
        else:
            widget.set_transition_type(transition_type)
            widget.set_transition_duration(duration)
    except Exception:
        # 控件已销毁等异常不影响其余控件与偏好本身。
        pass


def _default_gtk_settings():
    if Gtk is None:
        return None
    try:
        return Gtk.Settings.get_default()
    except Exception:
        return None


@contextmanager
def no_transition():
    '''在 with 体内临时关闭工具包级动画。

    给没有 transition 属性的 libadwaita 控件用（Adw.*SplitView 的滑入滑出）。
    必须是「临时」：gtk-enable-animations 是全局的，常关会把窗口与对话框的
    打开/关闭动画一起关掉，而那不在减弱范围内。AdwAnimation 在
    adw_animation_update_state 里读该设置并直接跳到末值，所以只需覆盖到
    set_show_sidebar 这一次同步调用；with 退出即恢复，此刻尚无其它动画因
    这次翻转而启动。
    '''
    if not is_reduced():
        yield
        return
    gtk_settings = _default_gtk_settings()
    if gtk_settings is None:
        yield
        return
    try:
        previous = gtk_settings.get_property('gtk-enable-animations')
    except Exception:
        previous = True
    if not previous:
        yield
        return
    try:
        gtk_settings.set_property('gtk-enable-animations', False)
        yield
    finally:
        try:
            gtk_settings.set_property('gtk-enable-animations', previous)
        except Exception:
            pass


def set_show_sidebar(split_view, show):
    '''Adw.OverlaySplitView.set_show_sidebar 的减弱动画感知版本。'''
    with no_transition():
        split_view.set_show_sidebar(show)


def reset():
    '''仅供单测：清空模块状态与登记表。'''
    global _settings, _reduced, _observing
    _settings = None
    _reduced = False
    _observing = False
    _registered.clear()

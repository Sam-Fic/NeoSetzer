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
from gi.repository import Gtk, Gdk

from setzer.app.service_locator import ServiceLocator


# on_keypress 在 autocomplete 激活时每次按键都跑，原实现每次调
# Gdk.keyval_from_name 做 C 查表。模块级预计算为整数常量后热路径只做整数比较。
_KEYVAL_TAB = Gdk.keyval_from_name('Tab')
_KEYVAL_ISO_LEFT_TAB = Gdk.keyval_from_name('ISO_Left_Tab')
_KEYVAL_RETURN = Gdk.keyval_from_name('Return')
_KEYVAL_ESCAPE = Gdk.keyval_from_name('Escape')
_KEYVAL_DOWN = Gdk.keyval_from_name('Down')
_KEYVAL_UP = Gdk.keyval_from_name('Up')
_KEYVAL_PAGE_DOWN = Gdk.keyval_from_name('Page_Down')
_KEYVAL_PAGE_UP = Gdk.keyval_from_name('Page_Up')

# 补全弹窗内的导航键 → preferences 区设置项名（报告 #6 遗留项：登记为可配置项）。
_NAV_KEY_SETTINGS = {
    'previous': 'autocomplete_previous',
    'next': 'autocomplete_next',
    'previous_page': 'autocomplete_previous_page',
    'next_page': 'autocomplete_next_page',
    'accept': 'autocomplete_accept',
    'cancel': 'autocomplete_cancel',
}


class AutocompleteController(object):

    def __init__(self, autocomplete, document):
        self.autocomplete = autocomplete
        self.document = document

        # 可配置的手动触发键（默认 Ctrl+Space），由 Autocomplete 解析偏好后下发。
        self.trigger_keyval = 0
        self.trigger_mods = 0
        # 可配置的补全弹窗导航键（报告 #6 遗留项），由 Autocomplete 解析偏好后下发。
        self.nav_keys = self._parse_nav_keys()
        # IME 组字状态：组字期间不抢 Ctrl+Space，把按键让给输入法（报告 #6/A）。
        self._ime_composing = False

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

        # 监听输入法组字信号：preedit-start/end 标记是否正在组字。
        # 注意：GTK4 的 Gtk.TextView/GtkSource.View 已移除 get_im_context()，
        # 也没有公开可读的 im-context 属性，其内部 IMContext 无法被外部取得，
        # 因此 preedit 信号无法可靠挂接。这里做降级：尽量尝试获取（兼容旧绑定 /
        # 未来暴露），获取不到则跳过组字跟踪（_ime_composing 恒为 False），
        # 不影响补全主功能，仅 IME 组字期间的 Ctrl+Space 闸门失效（报告 #6/A）。
        im_context = None
        try:
            im_context = self.document.view.source_view.get_im_context()
        except AttributeError:
            try:
                im_context = self.document.view.source_view.get_property('im-context')
            except (AttributeError, TypeError):
                im_context = None
        if im_context is not None:
            im_context.connect('preedit-start', self._on_im_preedit_start)
            im_context.connect('preedit-end', self._on_im_preedit_end)

    def on_keypress(self, controller, keyval, keycode, state):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if keyval in [_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB]:
            # 仅当补全激活时 Tab 才被接管（接受补全）。未激活时不再自动开补全，
            # 让事件落到 document_controller：用于占位符跳转 / 方括号跳转 / 缩进，
            # 避免 Tab 语义过载（报告 #6）。
            if state & modifiers == 0 and self.autocomplete.is_active:
                self.autocomplete.tab()
                return True

        # 可配置的手动触发键（默认 Ctrl+Space）。输入法正在组字时把按键让给输入法，
        # 绝不抢键；否则触发补全并吞掉按键，避免 Linux 上 Ctrl+Space 意外切换输入法
        # 开关（IME 闸门，报告 #6/A）。
        if (self.trigger_keyval != 0 and keyval == self.trigger_keyval and
                (state & modifiers) == self.trigger_mods):
            if self._ime_composing:
                return False
            self.autocomplete.activate_if_possible()
            if self.autocomplete.is_active:
                return True
            return False

        if not self.autocomplete.is_active:
            return False

        # 组字期间不抢导航键，把按键让给输入法（报告 #6/A）。
        if self._ime_composing:
            return False

        # 可配置的弹窗导航键（报告 #6 遗留项：上/下一条、上一页/下一页、接受、取消）。
        # 任意键若被改绑为空字符串（禁用），其 keyval 为 0，跳过即可。
        for action, (kv, mods) in self.nav_keys.items():
            if kv == 0:
                continue
            if keyval == kv and (state & modifiers) == mods:
                self._apply_nav(action)
                return True

        return False

    def refresh_nav_keys(self):
        '''偏好变更后由 Autocomplete.on_settings_changed 调用，刷新导航键缓存。'''
        self.nav_keys = self._parse_nav_keys()

    def _apply_nav(self, action):
        ac = self.autocomplete
        if action == 'previous':
            ac.select_previous()
        elif action == 'next':
            ac.select_next()
        elif action == 'previous_page':
            ac.page_up()
        elif action == 'next_page':
            ac.page_down()
        elif action == 'accept':
            ac.submit()
        elif action == 'cancel':
            ac.deactivate()

    @staticmethod
    def _parse_nav_key(settings, setting_name):
        accel = settings.get_value('preferences', setting_name)
        if not isinstance(accel, str) or accel == '':
            return (0, 0)
        _success, keyval, mods = Gtk.accelerator_parse(accel)
        return (keyval, mods)

    def _parse_nav_keys(self):
        settings = ServiceLocator.get_settings()
        return {action: self._parse_nav_key(settings, name)
                for action, name in _NAV_KEY_SETTINGS.items()}

    def set_trigger(self, keyval, mods):
        '''由 Autocomplete 解析偏好后下发手动触发键（报告 #6/B）。'''
        self.trigger_keyval = keyval
        self.trigger_mods = mods

    def _on_im_preedit_start(self, context):
        self._ime_composing = True

    def _on_im_preedit_end(self, context):
        self._ime_composing = False



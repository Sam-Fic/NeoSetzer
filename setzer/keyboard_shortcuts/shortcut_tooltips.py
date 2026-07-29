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

'''带快捷键提示的 tooltip 统一注册处。

以前各视图把快捷键直接写死在 tooltip 字符串里（如 "Bold (Ctrl+B)"），
用户在偏好设置里重绑按键后 tooltip 不会跟着变，与真实快捷键脱节。

现在视图通过 set_tooltip(widget, base_text, *action_names) 注册：
- tooltip 文本由 settings 中该动作当前的快捷键实时渲染；
- 监听 settings 的 'settings_changed'（keyboard_shortcuts 段），
  用户改键后所有已注册控件的 tooltip 立即刷新；
- 通过弱引用持有控件，随文档关闭等场景自动失效，不产生泄漏。
'''

import weakref

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk


_entries = list()
_settings = None


def set_tooltip(widget, base_text, *action_names):
    '''注册并立即应用形如 "base_text (快捷键1 / 快捷键2)" 的 tooltip。

    action_names 为 settings['keyboard_shortcuts'] 中的动作名；传空则为
    纯文本 tooltip（同时会覆盖该控件之前注册的带快捷键条目，适用于
    "Stop building" 这类临时状态文案）。'''

    _prune()
    for entry in _entries:
        if entry[0]() is widget:
            _entries.remove(entry)
            break
    _entries.append((weakref.ref(widget), base_text, action_names))
    _apply(widget, base_text, action_names)


def format_shortcut(trigger):
    '''把 '<Control><Shift>s' 之类的 trigger 串渲染成 "Ctrl+Shift+S"。'''

    if not trigger:
        return ''
    parsed = Gtk.accelerator_parse(trigger)
    # PyGObject 对 gtk_accelerator_parse 返回 (ok, keyval, mods)
    if parsed[0] and parsed[1]:
        return Gtk.accelerator_get_label(parsed[1], parsed[2])
    # 解析失败时（如 '<Control>f2' 这类小写 keysym 或录制存下的非标准串）
    # 退回手工拼接。注意 '>' 是修饰符与键名的分隔符，须分别处理。
    mod_names = {'Control': _('Ctrl'), 'Shift': _('Shift'), 'Alt': _('Alt')}
    parts = list()
    for chunk in trigger.split('<'):
        if not chunk:
            continue
        mod, sep, key = chunk.partition('>')
        if sep:
            parts.append(mod_names.get(mod, mod))
        else:
            key = mod
        if key:
            parts.append(key.upper() if len(key) <= 3 else key)
    return '+'.join(parts)


def get_action_label(action_name):
    '''返回动作当前快捷键的显示文本；未绑定时返回空串。'''

    shortcuts = _get_settings().get_value('keyboard_shortcuts', None)
    if not isinstance(shortcuts, dict):
        return ''
    return format_shortcut(shortcuts.get(action_name, ''))


def _apply(widget, base_text, action_names):
    labels = [label for label in (get_action_label(name) for name in action_names) if label]
    if labels:
        widget.set_tooltip_text(base_text + ' (' + ' / '.join(labels) + ')')
    else:
        widget.set_tooltip_text(base_text)


def _prune():
    _entries[:] = [entry for entry in _entries if entry[0]() is not None]


def _get_settings():
    global _settings
    if _settings is None:
        from setzer.app.service_locator import ServiceLocator
        _settings = ServiceLocator.get_settings()
        _settings.connect('settings_changed', _on_settings_changed)
    return _settings


def _on_settings_changed(settings, parameter):
    section, item, value = parameter
    if section != 'keyboard_shortcuts':
        return
    _prune()
    for ref, base_text, action_names in _entries:
        widget = ref()
        if widget is not None:
            _apply(widget, base_text, action_names)

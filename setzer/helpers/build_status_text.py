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

'''状态栏构建结果文本的纯逻辑格式化（gi-free，可单测）。

翻译在调用时经 builtins._ / builtins.ngettext 解析（由 setzer.in 启动
时注入）；单元测试环境无注入时回退原文与英文复数规则，与
git_section_viewgtk 的委托模式一致。文本刻意不带任何符号，仅纯文字
描述：如 "Build succeeded" / "2 errors, 3 warnings"。
'''

import builtins


def _(text):
    fn = getattr(builtins, '_', None)
    return fn(text) if fn is not None else text


def _ngettext(singular, plural, n):
    fn = getattr(builtins, 'ngettext', None)
    if fn is not None:
        return fn(singular, plural, n)
    return singular if n == 1 else plural


def format_build_status(error_count, warning_count):
    '''最近一次构建的结果文本。0 错误 0 警告视为构建成功；否则按
    "N error(s), M warning(s)" 列出非零项（badbox 不计入状态栏，
    在构建日志中查看）。'''
    if error_count <= 0 and warning_count <= 0:
        return _('Build succeeded')
    parts = []
    if error_count > 0:
        parts.append(_ngettext('{n} error', '{n} errors', error_count).format(n=error_count))
    if warning_count > 0:
        parts.append(_ngettext('{n} warning', '{n} warnings', warning_count).format(n=warning_count))
    return ', '.join(parts)

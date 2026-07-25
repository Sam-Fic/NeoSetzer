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

'''gi/repository 桩模块（备用）。

设计原则：优先把可测纯逻辑剥离到 gi-free 模块（如 setzer/helpers/），
使其可在无 GTK 环境下直接 import 测试。仅当无法剥离时（如必须测整个
Settings 类）才用本桩：在测试文件顶部 `import conftest_stub` 后再
`import setzer.settings.settings`，本桩会在 sys.modules 注入伪 gi，
跳过 `gi.require_version(...)` 与 `from gi.repository import ...`。

桩只覆盖 Settings 等被测目标实际用到的方法；新增用例时按需扩展。
'''

import sys
import types


def install():
    '''注入伪 gi / gi.repository 到 sys.modules。幂等。'''
    if 'gi' in sys.modules:
        return  # 真实 gi 已加载（开发机有 GTK 环境），不覆盖

    gi = types.ModuleType('gi')
    gi.require_version = lambda *a, **kw: None

    repository = types.ModuleType('gi.repository')

    # Gtk：Settings 等用到 Gtk.TextView（set_defaults 中获取默认字体）。
    # 用 lambda 返回 mock 对象避免 AttributeError。
    gtk = types.ModuleType('Gtk')
    gtk.TextView = type('TextView', (), {
        '__init__': lambda self: None,
        'set_monospace': lambda self, v: None,
        'get_pango_context': lambda self: types.SimpleNamespace(
            get_font_description=lambda: types.SimpleNamespace(
                to_string=lambda: 'Monospace 10')),
    })
    repository.Gtk = gtk

    repository.Pango = types.ModuleType('Pango')

    gi.repository = repository
    sys.modules['gi'] = gi
    sys.modules['gi.repository'] = repository


# 导入本模块即自动安装桩，简化测试文件用法：`import conftest_stub`
install()

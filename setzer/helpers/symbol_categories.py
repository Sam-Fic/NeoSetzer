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

'''符号分类白名单与校验。刻意不 import gi，便于单元测试。

category 参数来自用户配置（recent_symbols / favorite_symbols），
理论可被恶意 settings 或 .stzs 注入路径遍历。白名单与 setzer.in
入口文件中 icon_theme.add_search_path 的 6 个 folder 保持一致，
是封闭集合。任何修改须同步 setzer.in 入口。
'''


# 与 setzer.in:140 中 icon_theme.add_search_path 的 6 个 folder 保持一致。
ALLOWED_CATEGORIES = frozenset({
    'arrows', 'greek_letters', 'misc_math',
    'misc_text', 'operators', 'relations',
})


def is_valid_category(category):
    '''校验 category 是否在白名单内。

    返回 True 表示合法（可安全拼接到 symbols/<category>.xml 路径）。
    None、空字符串、含 ``..`` / ``/`` 的路径、非字符串类型一律拒绝。

    先做 isinstance(str) 守卫，避免 unhashable 类型（list/dict）触发
    frozenset 的 ``in`` 操作抛 TypeError。
    '''
    if not isinstance(category, str):
        return False
    return category in ALLOWED_CATEGORIES

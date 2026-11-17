#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

"""AI Agent 功能开关的单一查询点。

`preferences.ai_fix_enabled`（Preferences → AI Settings → Enable AI agent）
是 AI 相关全部入口的总开关：关闭时这些入口不应只是「点了弹提示」，而应
直接从界面上消失（标题栏按钮、侧栏 AI 面板、Build Log 的 AI 修复按钮、
源码 / PDF 右键菜单里的批注项等）。

本模块只做布尔查询，不依赖 GTK；默认值取不到时按「启用」处理，避免把
功能误判为关闭而让入口全部消失。
"""


def is_ai_agent_enabled(settings=None):
    '''返回 AI Agent 是否启用。settings 省略时从 ServiceLocator 取全局设置。'''
    if settings is None:
        try:
            from setzer.app.service_locator import ServiceLocator
            settings = ServiceLocator.get_settings()
        except Exception:
            settings = None
    if settings is None:
        return True
    try:
        return bool(settings.get_value('preferences', 'ai_fix_enabled'))
    except Exception:
        return True

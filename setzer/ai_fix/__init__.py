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

"""AI 修复集成包。

把构建日志里的报错 + 源码上下文组装成 prompt，调用外部 Agent CLI
（OpenCode / Claude Code / Aider / Codex / Gemini 或用户自定义工具）
在无头（subprocess）或有头（外部终端 TUI）模式下修复。

本包刻意保持与 GTK 无关的纯逻辑模块（presets / prompt_builder / agent_runner
的渲染部分），仅 preview_dialog 涉及 GTK，便于测试与复用。
"""

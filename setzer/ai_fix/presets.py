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

"""内置 Agent 工具预设。

每个工具的字段：
  - name:             唯一标识（同时用作显示名，settings.ai_fix_active_tool 指向它）
  - executable:       CLI 二进制名，用于可用性检测（subprocess '<exe> --version'）
  - headed_template:  有头模式命令模板（仅 Agent 命令本身，终端由 agent_runner 包裹）。
                      所有内置预设均自动发送 {prompt}，无需手动粘贴。
                      支持 {prompt} / {file} / {cwd} 占位符。
  - builtin:          True 表示内置预设（不可删除，可编辑/重置）。

无头模式已移除（安全考虑：Agent 在后台直接修改文件不安全）。
仅保留有头模式：用户在终端里看到 Agent 的每一步操作。

CLI 调用形式已通过 web 核实（2026-07）：
  - opencode: TUI `opencode --prompt "{p}"`（初始 prompt 注入 TUI）
  - claude:   `claude "{p}"`（位置参数=初始 prompt）
  - codex:    `codex "{p}"`（无 exec=TUI 带初始 prompt）
  - gemini:   `gemini -i "{p}"`（--prompt-interactive）
  - aider:    `aider --message "{p}" --no-auto-commits`（可见终端一次性运行，
              不加 --yes 让用户在终端审批。Aider 的 TUI 无「交互+初始 prompt」flag，
              这是其 CLI 客观限制，故有头为可见一次性运行而非多轮交互）。
"""

import copy


# 占位符：模板中支持替换的标记。
PLACEHOLDER_PROMPT = '{prompt}'
PLACEHOLDER_FILE = '{file}'
PLACEHOLDER_CWD = '{cwd}'


BUILTIN_TOOLS = [
    {
        'name': 'opencode',
        'executable': 'opencode',
        'headed_template': ['opencode', '--prompt', '{prompt}'],
        'builtin': True,
    },
    {
        'name': 'claude',
        'executable': 'claude',
        'headed_template': ['claude', '{prompt}'],
        'builtin': True,
    },
    {
        'name': 'codex',
        'executable': 'codex',
        'headed_template': ['codex', '{prompt}'],
        'builtin': True,
    },
    {
        'name': 'gemini',
        'executable': 'gemini',
        'headed_template': ['gemini', '-i', '{prompt}'],
        'builtin': True,
    },
    {
        'name': 'aider',
        'executable': 'aider',
        'headed_template': ['aider', '--message', '{prompt}', '--no-auto-commits'],
        'builtin': True,
    },
    {
        'name': 'pi',
        'executable': 'pi',
        'headed_template': ['pi', '--', '{prompt}'],
        'builtin': True,
    },
]


def default_tools():
    '''返回内置预设的深拷贝，供 settings 默认值与「重置/添加内置」按钮复用。

    深拷贝避免调用方修改污染 BUILTIN_TOOLS 常量（settings 持久化后会回写
    用户自定义的模板，绝不能反向写回本常量）。
    '''
    return copy.deepcopy(BUILTIN_TOOLS)


def builtin_names():
    '''返回内置预设的 name 列表，供「不可删除」判断使用。'''
    return [t['name'] for t in BUILTIN_TOOLS]

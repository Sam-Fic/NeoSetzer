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

CLI 调用形式已核实（opencode / pi / codebuddy 2026-11 本机 PTY 实测；
claude / codex / gemini / aider 2026-11 web 核实官方文档，本机未装）：
  - opencode: TUI `opencode --prompt "{p}"`（初始 prompt 注入 TUI 并自动
              提交。2026-11 本机实测 2.0.29：自动发送；TUI 开启 bracketed
              paste（ESC[?2004h），内嵌终端的多行注入按单条消息处理）
  - claude:   `claude "{p}"`（位置参数=初始 prompt，官方 CLI reference）
  - codex:    `codex "{p}"`（位置参数=TUI 初始 prompt，官方参考；
              脚本化一次性运行才用 `codex exec`，本预设走交互 TUI）
  - gemini:   `gemini -i "{p}"`（--prompt-interactive，初始 prompt 提交后
              保持交互会话，官方文档）
  - aider:    `aider --message "{p}" --no-auto-commits`（可见终端一次性运行，
              不加 --yes 让用户在终端审批。Aider 的 TUI 无「交互+初始 prompt」flag，
              这是其 CLI 客观限制，故有头为可见一次性运行而非多轮交互）。
  - pi:       `pi -- "{p}"`（`--` 结束选项解析，其后位置参数即初始 message，
              TUI 打开即自动提交，不逐行误拆。2026-11 本机实测 pi 1.1.0：
              初始 prompt 自动发送；TUI 自身开启 bracketed paste
              （ESC[?2004h），内嵌终端的多行注入按单条消息处理）。
  - codebuddy: `codebuddy "{p}"`（位置参数=初始 prompt，与 claude 同款。
              2026-11 本机实测 CodeBuddy Code 2.184.0：首次进某目录弹信任
              确认（回车选默认「Trust folder only」），确认后初始 prompt
              自动发送；TUI 开启 bracketed paste，多行注入按单条消息处理。
              别名 cbc 同用法）。
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
    {
        'name': 'codebuddy',
        'executable': 'codebuddy',
        'headed_template': ['codebuddy', '{prompt}'],
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


def merge_missing_builtins(tools):
    '''把已保存工具列表中缺失的内置预设补进去（按内置顺序追加到尾部）。

    内置预设不可经 UI 删除（见 builtin_names），保存列表缺某个内置只可能
    来自旧版本 settings（升级场景：内置清单随版本演进新增，而 ai_fix_tools
    一旦持久化就会被原样读回）。据此补全是安全的。同名工具以已保存版本
    为准（内置模板可编辑，不覆盖用户的修改）；用户自定义工具与既有顺序
    保持不变。

    Args:
        tools: 已保存的工具列表；非 list（异常落盘）时回退内置预设全量，
               空列表原样返回（运行时回退与偏好页自愈已覆盖）。

    Returns:
        list：无缺失时原对象原样返回（调用方据此判断是否需要回写）；
        有缺失时返回补全后的新列表。
    '''
    if not isinstance(tools, list):
        return default_tools()
    if not tools:
        return tools
    existing = {t.get('name') for t in tools if isinstance(t, dict)}
    missing = [copy.deepcopy(t) for t in BUILTIN_TOOLS if t['name'] not in existing]
    if not missing:
        return tools
    return list(tools) + missing


# 批注预制提示词：选区批注 / 单条直发时可选的指令模板。
# （新增 Git AI 提交提示词见文件末尾 GIT_COMMIT_PROMPT。）
# name 同时用作显示名与 settings.ai_annotation_presets 的键；
# instruction 是注入 prompt 的英文指令（与 build_log AI fix 的 prompt 一致，
# 面向模型用英文）。用户批注（comment）作为附加要求拼在指令之后。
ANNOTATION_PRESETS = [
    {
        'name': 'Improve writing',
        'instruction': 'Improve the wording, clarity and flow of the annotated passages. '
                       'Keep the LaTeX markup intact and preserve the original meaning.',
    },
    {
        'name': 'Fix LaTeX code',
        'instruction': 'Fix the LaTeX code in the annotated passages (syntax, packages, '
                       'typography). Make sure the document still compiles.',
    },
    {
        'name': 'Explain',
        'instruction': 'Explain the annotated passages. Do not modify any files; '
                       'answer in the chat only.',
    },
    {
        'name': 'Refactor',
        'instruction': 'Refactor the annotated passages for better structure and '
                       'maintainability without changing the rendered output.',
    },
]


def default_annotation_presets():
    '''返回批注预制提示词的深拷贝，供 settings 默认值使用。'''
    return copy.deepcopy(ANNOTATION_PRESETS)


def annotation_preset_instruction(presets, name):
    '''按 name 查指令文本；找不到（含 None/自定义）返回空串。'''
    if not name:
        return ''
    match = next((p for p in (presets or []) if p.get('name') == name), None)
    return match.get('instruction', '') if match else ''


# Git「AI 提交」预制提示词：面向模型用英文（与批注 / 构建修复一致）。
# 语义：只允许提交列出的文件，可按逻辑分组拆成多个提交；不推送、不改代码。
GIT_COMMIT_PROMPT = '''You are working in the git repository at {cwd}.

Commit the changes in the following files (this is the allowed scope):

{files}

Instructions:
1. Inspect the changes with `git status` and `git diff --cached`.
2. Group the changes into logically coherent commits. If the listed files
   contain unrelated changes, split them into multiple commits; never split
   one logical change across commits.
3. Stage and commit only paths from the list above. Never stage, commit,
   revert, modify or discard any other file.
4. If a listed file has unstaged edits, you may run `git add -- <path>` for
   that listed path to include its current content.
5. Write commit messages in the imperative mood: a short subject line and,
   when useful, a body explaining why.
6. Do not push.
7. When done, reply with a one-line summary for each commit you created.
'''


def build_git_commit_prompt(files, message_hint=None, cwd=None):
    '''组装 AI 提交提示词：勾选文件清单 + 可选的用户草稿信息。'''
    paths = '\n'.join('- ' + path for path in (files or []))
    prompt = GIT_COMMIT_PROMPT.format(cwd=cwd or '', files=paths)
    hint = (message_hint or '').strip()
    if hint:
        prompt += ('\nThe user drafted this commit message; use it as guidance '
                   'and complete or adjust it as needed:\n\n' + hint + '\n')
    return prompt

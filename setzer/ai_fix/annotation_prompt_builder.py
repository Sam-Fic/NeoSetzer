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

"""把选区批注组装成给 Agent CLI 的 prompt（替代 read 工具的精度契约）。

纯逻辑模块（不依赖 GTK），便于单测。输入是控制器在发送时刻从批注模型
提取的纯数据（文件路径 + 1-based 行区间 + 选区原文 + 用户要求），
发送前调用方已 save_to_disk，保证缓冲区=磁盘、行号精确。

设计要点：
  - 上下文契约 preamble：声明摘录与磁盘逐字一致、行号精确，要求模型
    不要为定位所引文本而重新 read 文件，直接按行号编辑。
  - 按文件分组；小文件（≤ WHOLE_FILE_MAX_LINES 行）直接贴全文件带行号，
    大文件贴各批注区间 ±CONTEXT_RADIUS 的并集（重叠区间合并去重）。
  - 每条批注：行区间 + 选区 verbatim 代码块 + 预制指令 + 用户批注。
"""

import os

from setzer.ai_fix.presets import annotation_preset_instruction
from setzer.ai_fix.prompt_builder import _read_file_lines, MAX_SOURCE_CHARS

# 批注上下文窗口半径（行）：批注区间两端各外扩 RADIUS 行。
CONTEXT_RADIUS = 10

# 小文件阈值：不超过该行数直接贴全文件（最强免读保证）。
WHOLE_FILE_MAX_LINES = 200


def _context_preamble(cwd, interpreter):
    '''上下文契约：让模型跳过 read 定位步骤，直接开始推理/修改。'''
    return '\n'.join([
        'You are working on a LaTeX project. Working directory: {}. '
        'LaTeX engine used for this document: {}.'.format(cwd or '(unsaved)', interpreter),
        '',
        'Context contract (important):',
        '- The files below were saved immediately before this message; the excerpts '
        'are verbatim copies of the current on-disk content.',
        '- Line numbers are exact, 1-based, and refer to the current on-disk files.',
        '- Do NOT re-read these files to locate the quoted passages: the excerpts '
        'already contain what you need. Start reasoning and edit directly using the '
        'given file paths and line numbers.',
        '- Only read a file if you genuinely need content outside the provided '
        'excerpts (e.g. the preamble or another section).',
        '- The editor auto-reloads files after your edits; do not ask the user to '
        'save or reload.',
    ])


def _format_numbered_lines(source_lines, start, end, markers=None):
    '''把 [start, end]（1-based 闭区间）格式化为带行号的 latex 代码块。

    markers: {line_no: marker_text}，附加在行尾（如批注起点标记）。
    '''
    markers = markers or {}
    lines = ['```latex']
    for ln in range(start, end + 1):
        text = source_lines[ln - 1].rstrip('\n').rstrip('\r')
        marker = markers.get(ln, '')
        lines.append('{:>5}: {}{}'.format(ln, text, marker))
    lines.append('```')
    return '\n'.join(lines)


def _merged_context_ranges(group, total):
    '''各批注区间 ±RADIUS 的并集，合并重叠/相邻区间。

    返回 [(start, end, {annotation_index, ...}), ...]（1-based）。
    '''
    wanted = []
    for idx, ann in enumerate(group):
        s = max(1, ann['start_line'] - CONTEXT_RADIUS)
        e = min(total, ann['end_line'] + CONTEXT_RADIUS)
        wanted.append((s, e, idx))
    wanted.sort()
    merged = []
    for s, e, idx in wanted:
        if merged and s <= merged[-1][1] + 1:
            ps, pe, pidx = merged[-1]
            merged[-1] = (ps, max(pe, e), pidx | {idx})
        else:
            merged.append((s, e, {idx}))
    return merged


def _group_by_file(annotations):
    '''按 filename 分组，保持原始顺序。'''
    groups = {}
    for ann in annotations:
        groups.setdefault(ann.get('filename'), []).append(ann)
    return groups


def _annotation_request(ann, presets):
    '''单条批注的要求文本：预制指令 + 用户批注。'''
    parts = []
    instruction = annotation_preset_instruction(presets, ann.get('preset'))
    if instruction:
        parts.append(instruction)
    comment = (ann.get('comment') or '').strip()
    if comment:
        parts.append('Additional user request: {}'.format(comment))
    if not parts:
        parts.append('Review the annotated passage and improve it where appropriate.')
    return ' '.join(parts)


def build_prompt_for_annotations(annotations, cwd, interpreter='default', presets=None):
    '''批量批注的 prompt。

    Args:
        annotations: list[dict]，每条含 filename / start_line / end_line /
            text（选区原文）/ comment / preset。行号 1-based，发送时刻现算。
        cwd: str，工作目录（绝对路径）。
        interpreter: str，LaTeX 引擎名。
        presets: 预制提示词列表（settings.ai_annotation_presets），None 用内置。

    Returns:
        str: 完整 prompt。annotations 为空返回空串。
    '''
    if not annotations:
        return ''
    if presets is None:
        from setzer.ai_fix.presets import ANNOTATION_PRESETS
        presets = ANNOTATION_PRESETS

    parts = [_context_preamble(cwd, interpreter), '']
    total_chars = 0
    truncated = False

    for filename, group in _group_by_file(annotations).items():
        rel_path = os.path.relpath(filename, cwd) if (filename and cwd) else (filename or '(unknown file)')
        parts.append('## File: {}'.format(rel_path))
        if filename:
            parts.append('Absolute path: {}'.format(filename))
        parts.append('')

        # 各批注：行区间 + 要求 + 选区 verbatim
        for idx, ann in enumerate(group, start=1):
            line_range = '{}-{}'.format(ann['start_line'], ann['end_line']) \
                if ann['end_line'] != ann['start_line'] else str(ann['start_line'])
            parts.append('### Annotation {} (lines {})'.format(idx, line_range))
            parts.append('Request: {}'.format(_annotation_request(ann, presets)))
            parts.append('Annotated passage (verbatim):')
            selection = (ann.get('text') or '').rstrip('\n')
            parts.append('```latex\n{}\n```'.format(selection))
            focus_text = (ann.get('focus_text') or '').strip()
            if focus_text:
                # PDF 派生批注：整行/整块仅作上下文，单独强调用户实际选中部分。
                parts.append('The passage above is the full line/block, provided as '
                             'context. The user specifically highlighted:')
                if ann.get('focus_kind') == 'pdf-text':
                    parts.append('(rendered PDF text; may differ from the source)')
                parts.append('```latex\n{}\n```'.format(focus_text))
            parts.append('')

        # 上下文：小文件全贴，大文件合并区间摘录
        source_lines = _read_file_lines(filename) if filename else None
        if not source_lines:
            parts.append('(source context unavailable — read the file if needed)')
            parts.append('')
            continue
        total = len(source_lines)

        if total <= WHOLE_FILE_MAX_LINES:
            markers = {ann['start_line']: '  <-- annotation {} starts here'.format(idx)
                       for idx, ann in enumerate(group, start=1)}
            block = _format_numbered_lines(source_lines, 1, total, markers)
            if total_chars + len(block) <= MAX_SOURCE_CHARS:
                parts.append('Full file content (with exact line numbers):')
                parts.append(block)
                total_chars += len(block)
            else:
                truncated = True
                parts.append('(file content omitted: prompt length limit reached)')
        else:
            parts.append('Relevant source context (with exact line numbers):')
            for s, e, idx_set in _merged_context_ranges(group, total):
                markers = {}
                for idx in idx_set:
                    ann = group[idx]
                    markers[ann['start_line']] = '  <-- annotation {} starts here'.format(idx + 1)
                block = _format_numbered_lines(source_lines, s, e, markers)
                if total_chars + len(block) > MAX_SOURCE_CHARS:
                    truncated = True
                    parts.append('(further context truncated to avoid prompt length limit)')
                    break
                parts.append(block)
                total_chars += len(block)
        parts.append('')

    if truncated:
        parts.append('Note: some context was truncated; read those files only if the '
                     'provided excerpts are insufficient.')
        parts.append('')
    parts.append('Carry out the requests above on the annotated passages. Keep changes '
                 'minimal and focused; briefly summarize what you changed afterwards.')
    return '\n'.join(parts)


def build_prompt_for_selection(filename, start_line, end_line, text, cwd,
                               comment='', preset=None, interpreter='default',
                               presets=None):
    '''单条直发（右键「Send Selection to Agent」）：同格式包一条临时批注。'''
    return build_prompt_for_annotations(
        [{'filename': filename, 'start_line': start_line, 'end_line': end_line,
          'text': text, 'comment': comment, 'preset': preset}],
        cwd, interpreter=interpreter, presets=presets)
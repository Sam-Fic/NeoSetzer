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

"""把构建日志里的报错 + .tex 源码上下文组装成给 Agent CLI 的 prompt。

纯逻辑模块（不依赖 GTK），便于单测。仅依赖 document 暴露的
`source_buffer`（GtkSource.Buffer）+ `get_filename()` / `get_dirname()`，
以及 build_log.items 元组结构 (type, ?, filename, line_number, description)。

设计要点：
  - 单条错误：取报错行 ±RADIUS 行的源码，带行号前缀，报错行加 `` <-- error here`` 标记。
  - 批量：按 filename 分组；对活动文档用 source_buffer 读源码（缓冲区=磁盘后保存），
    对非活动文档（如 .bib、子文件）直接从磁盘读（fallback 失败就只列错误不贴源码）。
  - 同一文件多个错误：合并上下文窗口取并集，避免大段源码被重复贴 N 次。
"""

import os

# 上下文窗口半径（行）。报错行 ± RADIUS 行被纳入 prompt。
CONTEXT_RADIUS = 5

# 单次批量最大源码片段字符数，防止极端情况 prompt 超长被 Agent CLI 截断。
MAX_SOURCE_CHARS = 8000


def read_source_context(source_lines, center_line, radius=CONTEXT_RADIUS):
    '''从已拆行的源码列表中取 [center_line-radius, center_line+radius] 区间。

    Args:
        source_lines: list[str]，每行（含行尾 '\\n'）。索引 0 = 第 1 行。
        center_line: 1-based 报错行号；<=0 或超出范围时退化为裁剪到合法区间。
        radius: 上下文行数半径。

    Returns:
        list[tuple(int, str, bool)] —— (line_no, line_text_without_newline, is_error_line)
        仅包含实际存在的行。行号 1-based。
    '''
    if not source_lines:
        return []

    total = len(source_lines)
    # center_line 为 -1 / None 时（badbox 等无具体行号），取文件开头 radius*2 行。
    if not center_line or center_line <= 0:
        start, end = 1, min(total, radius * 2)
    else:
        start = max(1, center_line - radius)
        end = min(total, center_line + radius)

    out = []
    for ln in range(start, end + 1):
        text = source_lines[ln - 1].rstrip('\n').rstrip('\r')
        out.append((ln, text, ln == center_line))
    return out


def format_context_block(context_tuples, filename):
    '''把 read_source_context 的输出格式化为 markdown 代码块。'''
    if not context_tuples:
        return ''
    lines = ['```latex']
    for ln, text, is_err in context_tuples:
        marker = '  <-- error here' if is_err else ''
        lines.append('{:>5}: {}{}'.format(ln, text, marker))
    lines.append('```')
    return '\n'.join(lines)


def _read_file_lines(path):
    '''从磁盘读文件并拆行；失败返回 None。供非活动文档 fallback 使用。'''
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read().splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def _get_source_lines(document, filename):
    '''取指定 filename 的源码拆行列表。

    优先用 document.source_buffer（活动文档，缓冲区=磁盘后保存）；
    若 filename 不等于活动文档路径，则从磁盘读。
    '''
    active_path = document.get_filename() if document is not None else None
    if filename and active_path:
        try:
            # realpath 规范化 symlinks，与 document.set_filename 一致。
            if os.path.realpath(filename) == os.path.realpath(active_path):
                buf = document.source_buffer
                start, end = buf.get_start_iter(), buf.get_end_iter()
                text = buf.get_text(start, end, True)
                return text.splitlines(keepends=True)
        except Exception:
            pass
    # 非活动文档或异常：从磁盘读
    if filename:
        return _read_file_lines(filename)
    return None


def _group_items_by_file(items):
    '''按 filename 分组；filename 为 None 的归到 None 键下。保持原始顺序。'''
    groups = {}
    for it in items:
        # it 元组：(type, ?, filename, line_number, description)
        filename = it[2] if len(it) > 2 else None
        groups.setdefault(filename, []).append(it)
    return groups


def build_prompt_for_item(document, item):
    '''单条错误的 prompt。

    Args:
        document: 当前活动 LaTeX 文档（提供 source_buffer / get_dirname）。
        item: build_log.items 元组 (type, ?, filename, line_number, description)。

    Returns:
        str: 完整 prompt。
    '''
    item_type = item[0] if len(item) > 0 else 'Error'
    filename = item[2] if len(item) > 2 else None
    line_no = item[3] if len(item) > 3 else -1
    description = item[4] if len(item) > 4 else ''

    cwd = document.get_dirname() if document is not None else ''
    rel_path = os.path.relpath(filename, cwd) if (filename and cwd) else (filename or '')

    source_lines = _get_source_lines(document, filename)
    context = read_source_context(source_lines or [], line_no)
    source_block = format_context_block(context, filename)

    parts = []
    parts.append('You are fixing a LaTeX build error. Working directory: {}'.format(cwd or '(unsaved)'))
    parts.append('')
    parts.append('File: {}'.format(rel_path or '(unknown)'))
    parts.append('{} at line {}: {}'.format(item_type, line_no if line_no and line_no > 0 else '?', description))
    parts.append('')
    parts.append('Relevant source context:')
    parts.append(source_block if source_block else '(source unavailable)')
    parts.append('')
    parts.append('Please fix the error by editing the file(s) directly. '
                 'Only change what is necessary to resolve the reported error.')
    return '\n'.join(parts)


def build_prompt_for_items(document, items):
    '''批量错误的 prompt：按文件分组，合并上下文窗口。

    Args:
        document: 活动文档。
        items: build_log.items 元组列表。

    Returns:
        str: 完整 prompt。如果 items 为空返回空串。
    '''
    if not items:
        return ''

    cwd = document.get_dirname() if document is not None else ''

    parts = []
    parts.append('You are fixing multiple LaTeX build errors. Working directory: {}'.format(cwd or '(unsaved)'))
    parts.append('')
    parts.append('Errors to fix (grouped by file):')
    parts.append('')

    total_chars = 0
    for filename, group in _group_items_by_file(items).items():
        rel_path = os.path.relpath(filename, cwd) if (filename and cwd) else (filename or '(unknown file)')
        parts.append('## File: {}'.format(rel_path))
        # 列出该文件所有错误
        for it in group:
            item_type = it[0]
            line_no = it[3] if len(it) > 3 else -1
            desc = it[4] if len(it) > 4 else ''
            ln_str = str(line_no) if (line_no and line_no > 0) else '?'
            parts.append('- {} at line {}: {}'.format(item_type, ln_str, desc))
        parts.append('')

        # 合并上下文窗口：取所有报错行的 ±RADIUS 并集
        source_lines = _get_source_lines(document, filename)
        if not source_lines:
            parts.append('(source context unavailable)')
            parts.append('')
            continue

        total = len(source_lines)
        wanted_ranges = []
        for it in group:
            ln = it[3] if len(it) > 3 else -1
            if not ln or ln <= 0:
                continue
            s, e = max(1, ln - CONTEXT_RADIUS), min(total, ln + CONTEXT_RADIUS)
            wanted_ranges.append((s, e, ln))
        if not wanted_ranges:
            # 无具体行号：贴文件开头
            wanted_ranges = [(1, min(total, CONTEXT_RADIUS * 2), -1)]

        # 合并重叠/相邻区间
        wanted_ranges.sort()
        merged = []
        for s, e, err_ln in wanted_ranges:
            if merged and s <= merged[-1][1] + 1:
                # 扩展上一区间；合并 error 行集合
                ps, pe, perr = merged[-1]
                err_set = set(perr) if isinstance(perr, (set, list, tuple)) else {perr}
                err_set.add(err_ln)
                merged[-1] = (ps, max(pe, e), err_set)
            else:
                merged.append((s, e, {err_ln}))

        parts.append('Relevant source context:')
        for s, e, err_lns in merged:
            context = []
            for ln in range(s, e + 1):
                text = source_lines[ln - 1].rstrip('\n').rstrip('\r')
                is_err = ln in err_lns and -1 not in err_lns
                marker = '  <-- error here' if is_err else ''
                context.append('{:>5}: {}{}'.format(ln, text, marker))
            block = '\n'.join(['```latex'] + context + ['```'])
            if total_chars + len(block) > MAX_SOURCE_CHARS:
                parts.append('(source context truncated to avoid prompt length limit)')
                break
            parts.append(block)
            total_chars += len(block)
        parts.append('')

    parts.append('Please fix all the errors listed above by editing the file(s) directly. '
                 'Only change what is necessary to resolve the reported errors.')
    return '\n'.join(parts)

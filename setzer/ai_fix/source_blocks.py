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

"""源码「整块」扩展（PDF 选区反向映射的上下文契约）。

用户选中公式/图片/表格的一部分时，prompt 应提供整个环境块而非片段。
本模块纯逻辑（无 GTK 依赖）：单遍扫描源码记录所有白名单环境与显示数学
的闭区间，expand_to_block 取包含指定行的最内层区间；未命中退化为整行。

单遍前向扫描而非局部上下扫：$$ 的开/闭配对、嵌套环境的内层优先都只有
全文件状态机才能判对（局部上扫会把上一公式的闭合 $$ 误认成开）。
"""

import re

# 视为「块」的环境：浮动体 + 表格 + 显示数学环境。inline math 不在此列。
BLOCK_ENVIRONMENTS = {
    'figure', 'figure*', 'table', 'table*', 'tabular', 'tabular*',
    'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*',
    'gather', 'gather*', 'multline', 'multline*', 'eqnarray', 'eqnarray*',
    'displaymath',
}

# 一行内按出现顺序取 begin/end/显示数学定界符 token。
_TOKEN_RE = re.compile(
    r'\\begin\{([^}]+)\}|\\end\{([^}]+)\}|\\\[|\\\]|\$\$')
_COMMENT_RE = re.compile(r'(?<!\\)%.*$')


def _tokens(text):
    '''yield (kind, name)：kind ∈ {'begin', 'end', 'open_math', 'close_math', 'dd'}。'''
    for match in _TOKEN_RE.finditer(text):
        if match.group(1) is not None:
            yield ('begin', match.group(1))
        elif match.group(2) is not None:
            yield ('end', match.group(2))
        elif match.group(0) == r'\[':
            yield ('open_math', r'\[')
        elif match.group(0) == r'\]':
            yield ('close_math', r'\]')
        else:
            yield ('dd', '$$')


def compute_block_ranges(source_lines):
    '''单遍扫描，返回 [(start, end, kind, env_name)]（1-based 闭区间）。

    kind='environment'（env_name 为环境名）或 'displaymath'（env_name None）。
    未闭合的环境/数学到 EOF 不记录（残缺块不提供）。
    '''
    ranges = []
    env_stack = []
    math_stack = []
    for line_no, raw in enumerate(source_lines, start=1):
        text = _COMMENT_RE.sub('', raw)
        for kind, name in _tokens(text):
            if kind == 'begin':
                if name in BLOCK_ENVIRONMENTS:
                    env_stack.append((name, line_no))
            elif kind == 'end':
                # 从栈顶向下找同名 begin（容忍跨层错配，取最近同名）。
                for i in range(len(env_stack) - 1, -1, -1):
                    if env_stack[i][0] == name:
                        start = env_stack[i][1]
                        del env_stack[i:]
                        if name in BLOCK_ENVIRONMENTS:
                            ranges.append((start, line_no, 'environment', name))
                        break
            elif kind == 'open_math':
                math_stack.append((r'\[', line_no))
            elif kind == 'close_math':
                if math_stack and math_stack[-1][0] == r'\[':
                    start = math_stack.pop()[1]
                    ranges.append((start, line_no, 'displaymath', None))
            elif kind == 'dd':
                if math_stack and math_stack[-1][0] == '$$':
                    start = math_stack.pop()[1]
                    ranges.append((start, line_no, 'displaymath', None))
                else:
                    math_stack.append(('$$', line_no))
    return ranges


def expand_to_block(source_lines, line):
    '''包含 line（1-based）的最内层块区间；未命中返回 (line, line, 'line', None)。

    返回 (start, end, kind, env_name)。嵌套时取 start 最大者（最内层），
    同 start 取 end 最小者。
    '''
    if not source_lines:
        return (line, line, 'line', None)
    line = max(1, min(line, len(source_lines)))
    best = None
    for start, end, kind, env_name in compute_block_ranges(source_lines):
        if start <= line <= end:
            if best is None or start > best[0] or (start == best[0] and end < best[1]):
                best = (start, end, kind, env_name)
    return best if best is not None else (line, line, 'line', None)
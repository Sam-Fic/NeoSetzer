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

'''数学区域扫描（纯逻辑，无 gi 依赖，headless 可测）。

扫描 LaTeX 源码，找出所有数学区域（含定界符本身），供 hover 公式预览
等消费方按 offset 查询「鼠标下是不是公式」。覆盖：

- 行内：``$...$``、``\\(...\\)``
- 显示：``$$...$$``、``\\[...\\]``
- 环境：equation / align / alignat / flalign / gather / multline /
  eqnarray / displaymath / math / IEEEeqnarray / dmath（含星号变体）

与真实 TeX 语义对齐的处理：

- 转义：``\\$`` 不是定界符；``\\\\``（换行命令）之后的 ``$`` 是定界符。
  实现为 token 化状态机：``\\.`` 优先吞掉「反斜杠+任一字符」，成对的
  ``\\\\`` 被整体吞掉后其后的 ``$`` 自然暴露为有效定界符。
- 注释：未转义 ``%`` 到行尾是注释，注释内的 ``$`` / ``\\begin{..}``
  不参与配对；数学区域内部的注释同样按 TeX 语义整段跳过。
- verbatim：verbatim / Verbatim / lstlisting / minted 环境内是字面
  文本，不做任何 token 解释，直接字面搜索 ``\\end{同名}`` 跳过。

状态机同一时刻至多一个开放区域，输出区域按 start 排序且互不重叠
（不报告嵌套/交叠，``\\begin{aligned}`` 等内层环境不影响外层闭合）。

无法闭合的区域（EOF 仍开放）整体丢弃——宁可不预览，不给错误区域。
'''

import re
from typing import NamedTuple, Optional, List


class MathRegion(NamedTuple):
    start: int   # 区域起点（开放定界符首字符 offset）
    end: int     # 区域终点（闭合定界符末字符后一位，Python 切片语义）
    raw: str     # 区域源文本 text[start:end]，含两侧定界符
    kind: str    # 'inline' | 'display'


# 支持 hover 预览的数学环境（LaTeX 标准 + 常见扩展）。星号变体由环境名
# 正则的 ``\\*?`` 统一处理，不需要单独列出。
_MATH_ENVIRONMENTS = frozenset((
    'equation', 'align', 'alignat', 'flalign', 'gather', 'multline',
    'eqnarray', 'displaymath', 'math', 'IEEEeqnarray', 'dmath',
))

# 这些环境内是字面文本，整段字面跳过（不配对任何数学定界符）。
_VERBATIM_ENVIRONMENTS = frozenset((
    'verbatim', 'verbatim*', 'Verbatim', 'Verbatim*', 'lstlisting', 'minted',
))

# 环境名：字母数字 + @（内部命令形态）+ 可选星号。
_ENV_NAME = r'[A-Za-z0-9@]+\*?'

# 主扫描 token。alternation 顺序即优先级：
# - begin/end 及四个反斜杠定界符 \( \) \[ \] 必须在 esc 之前，否则会被
#   \\. 吞成「\X + 普通文本」，永远匹配不到。它们与 esc 的冲突安全：
#   \\[2mm] 在第一个 \ 处四个定界符都不匹配（第二个字符不是定界符），
#   落到 esc 整体吞掉 \\，随后的 [2mm] 是普通文本——即换行命令的可选
#   间距参数不会被误当成显示数学定界符；
# - esc 吞掉剩余的「反斜杠+任一字符」（\$、\\、\%、\{ ...），后续
#   token 规则天然只对未转义字符生效；
# - comment 吞掉未转义 % 到行尾；
# - $$ 在 $ 之前，保证 $$ 优先按显示定界符匹配。
_TOKEN_REGEX = re.compile(
    r'\\end\s*\{(?P<end_env>' + _ENV_NAME + r')\}'
    r'|\\begin\s*\{(?P<begin_env>' + _ENV_NAME + r')\}'
    r'|(?P<open_paren>\\\()'
    r'|(?P<close_paren>\\\))'
    r'|(?P<open_bracket>\\\[)'
    r'|(?P<close_bracket>\\\])'
    r'|(?P<esc>\\.)'
    r'|(?P<comment>%[^\n]*)'
    r'|(?P<display_dollar>\$\$)'
    r'|(?P<inline_dollar>\$)'
)

# 外部状态下打开区域的 token → (kind, 期望的闭合 token 名)。
_OPENERS = {
    'display_dollar': ('display', 'display_dollar'),
    'inline_dollar': ('inline', 'inline_dollar'),
    'open_paren': ('inline', 'close_paren'),
    'open_bracket': ('display', 'close_bracket'),
}


def _math_env_base(env_name: str) -> str:
    '''去掉星号变体后缀，用于环境名集合成员判断（align* → align）。'''
    return env_name[:-1] if env_name.endswith('*') else env_name


def _skip_verbatim(text: str, pos: int, env_name: str) -> Optional[int]:
    '''verbatim 环境起点之后的下一个扫描位置；未闭合返回 None。

    字面搜索 \\end{同名}（verbatim 内不做转义/注释解释），未闭合视为
    其后全是字面文本。
    '''
    close_marker = '\\end{' + env_name + '}'
    close_at = text.find(close_marker, pos)
    if close_at == -1:
        return None
    return close_at + len(close_marker)


_VERBATIM_BEGIN_REGEX = re.compile(r'\\begin\s*\{(?P<begin_env>' + _ENV_NAME + r')\}')


def scan_verbatim_spans(text: str) -> List[tuple]:
    '''返回 verbatim 类环境的字面文本区间 [start, end) 列表。

    区间含两侧的 \\begin{...} / \\end{...} 标记本身。未闭合的环境其区间
    延伸到文本结尾。供「按环境名在源码里找片段」的消费方排除示例代码：
    文档里贴一段示例 \\begin{tikzpicture} 不该被当成可编译的图。
    '''
    spans: List[tuple] = []
    pos = 0
    while True:
        match = _VERBATIM_BEGIN_REGEX.search(text, pos)
        if match is None:
            return spans
        env_name = match.group('begin_env')
        if env_name not in _VERBATIM_ENVIRONMENTS:
            pos = match.end()
            continue
        next_pos = _skip_verbatim(text, match.end(), env_name)
        if next_pos is None:
            spans.append((match.start(), len(text)))
            return spans
        spans.append((match.start(), next_pos))
        pos = next_pos


def find_math_regions(text: str) -> List[MathRegion]:
    '''扫描 text，返回按 start 排序、互不重叠的 MathRegion 列表。'''
    regions = []
    open_start = None
    open_kind = None
    open_close = None   # 期望闭合的 token 名（区域为环境时为 None）
    open_env = None     # 区域为数学环境时的环境名（保留星号，闭合须精确一致）

    pos = 0
    length = len(text)
    while pos < length:
        match = _TOKEN_REGEX.search(text, pos)
        if match is None:
            break
        pos = match.end()

        begin_env = match.group('begin_env')
        if begin_env is not None:
            if begin_env in _VERBATIM_ENVIRONMENTS:
                # verbatim 内全是字面文本：忽略其中的转义/注释/定界符。
                next_pos = _skip_verbatim(text, pos, begin_env)
                if next_pos is None:
                    break
                pos = next_pos
            elif _math_env_base(begin_env) in _MATH_ENVIRONMENTS and open_start is None:
                open_start = match.start()
                open_kind = 'display'
                open_close = None
                open_env = begin_env
            continue

        end_env = match.group('end_env')
        if end_env is not None:
            if open_env is not None and end_env == open_env:
                regions.append(MathRegion(
                    open_start, match.end(), text[open_start:match.end()], open_kind))
                open_start = open_kind = open_close = open_env = None
            continue

        if match.group('esc') is not None or match.group('comment') is not None:
            continue

        # lastgroup：begin/end/esc/comment 分支已排除，此处必为六种
        # 定界符 token 之一（每个分支恰好一个具名组）。
        token = match.lastgroup

        if open_start is None:
            if token in _OPENERS:
                open_kind, open_close = _OPENERS[token]
                open_start = match.start()
                open_env = None
            # 不成对的 close_* 在外部状态下是普通文本，忽略。
        elif token == open_close:
            regions.append(MathRegion(
                open_start, match.end(), text[open_start:match.end()], open_kind))
            open_start = open_kind = open_close = open_env = None
        # 区域内出现的其它定界符 token（如 $$..$$ 中间的单个 $）按
        # TeX 错误输入对待：忽略，等真正的闭合 token。

    # EOF 仍开放的区域丢弃（未闭合）。
    return regions


def find_region_at(regions: List[MathRegion], offset: int) -> Optional[MathRegion]:
    '''二分查 offset 所在区域。regions 须为 find_math_regions 的输出
    （按 start 排序、互不重叠）。不在任何区域内返回 None。'''
    lo, hi = 0, len(regions)
    while lo < hi:
        mid = (lo + hi) // 2
        if regions[mid].start <= offset:
            lo = mid + 1
        else:
            hi = mid
    if lo > 0:
        candidate = regions[lo - 1]
        if offset < candidate.end:
            return candidate
    return None

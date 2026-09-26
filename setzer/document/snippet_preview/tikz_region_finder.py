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

'''TikZ 图片段扫描（纯逻辑，无 gi 依赖，headless 可测）。

从 parser 已配对的 block 列表里找「offset 所在的 tikzpicture 环境」，
供侧栏实时预览按光标位置取片段。

数据源是 parser_latex 的 symbols['blocks']：每条为
``[begin_offset, end_offset, begin_line, end_line, env_name(, title)]``，
按 begin_offset 排序，end_offset 为 None 表示未闭合。配对是按环境名
各自 LIFO 做的，因此 tikzpicture 内嵌的 scope / axis 等环境不影响外层
tikzpicture 的配对结果。

与数学区域扫描一致地排除 verbatim/lstlisting 内的示例代码：blocks 是
纯正则产物，文档里贴一段 ``\\begin{tikzpicture}`` 示例同样会被配对。
'''

from typing import NamedTuple, List, Optional

from setzer.document.snippet_preview.math_region_finder import scan_verbatim_spans

# 支持光标跟随预览的环境。pgfplots 的 axis 等同类环境理论可用同一条
# 编译路径，但它们的 preamble 依赖更重（\\pgfplotsset 等），暂不纳入。
_TIKZ_ENVIRONMENTS = frozenset((
    'tikzpicture',
))


class TikzRegion(NamedTuple):
    start: int   # 环境起点（\begin 的反斜杠 offset）
    end: int     # 环境终点（\end{tikzpicture} 的 } 后一位，Python 切片语义）
    raw: str     # 区域源文本 text[start:end]，含 \begin/\end 与可选参数
    env: str     # 环境名（保留星号等原始形态）


def find_tikzpicture_at(blocks: List[list], text: str, offset: int) -> Optional[TikzRegion]:
    '''offset 所在的 tikzpicture 区域；不在任何图内返回 None。

    blocks 须为 parser 的 symbols['blocks']。多命中时取跨度最小者
    （内层优先），未闭合或环境名后缺 ``}`` 的块直接丢弃——宁可不出图，
    不给错误区域。
    '''
    best = None
    for block in blocks:
        if len(block) < 5:
            continue
        env = block[4]
        if env not in _TIKZ_ENVIRONMENTS:
            continue
        begin = block[0]
        end_keyword = block[1]
        if begin is None or end_keyword is None:
            continue
        # block[1] 指向 \end 的反斜杠，区域的真实终点是环境名的右花括号。
        close_brace = text.find('}', end_keyword)
        if close_brace == -1:
            continue
        end = close_brace + 1
        if not (begin <= offset < end):
            continue
        if best is None or (end - begin) < (best[1] - best[0]):
            best = (begin, end, env)

    if best is None:
        return None

    start, end, env = best
    for verbatim_start, verbatim_end in scan_verbatim_spans(text):
        if verbatim_start <= start < verbatim_end:
            return None
        if start < verbatim_start:
            break   # spans 按起点递增，后面的不可能再覆盖 start
    return TikzRegion(start, end, text[start:end], env)

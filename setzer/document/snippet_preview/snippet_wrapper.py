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

'''片段 snippet 包装（纯逻辑，无 gi 依赖，headless 可测）。

把片段源文本包装成可独立编译的完整 LaTeX 文档：公式（build_snippet_
document）与图形（build_figure_document）两条路径，公式 hover 预览与
TikZ 侧栏预览分别使用。

核心决策：复用原文档 preamble（``\\begin{document}`` 之前的全部内容），
让用户自定义宏（\\newcommand）与宏包（bm、siunitx、tikz...）在预览中
同样生效——错误渲染比慢渲染更糟。无 ``\\begin{document}`` 的文档
（snippet 文件、纯片段）回退到最小 preamble，保证任何输入至少有可编译
的兜底。
'''

import hashlib
import re

# 最小兜底 preamble：只加预览公式最常用的两个数学宏包，不猜用户的
# 完整环境（猜错的宏包反而制造编译失败）。
_MINIMAL_HEAD = (
    '\\documentclass{article}\n'
    '\\usepackage{amsmath}\n'
    '\\usepackage{amssymb}\n'
    '\\begin{document}\n'
    '\\pagestyle{empty}\n'
)

# 图形片段的最小兜底：只给 tikz 本身。\\usetikzlibrary 不猜——用户的库
# 清单写在 preamble 里，猜错（或漏猜）都是编译失败；无 preamble 可用的
# 场景本就无法完整还原用户的图。
_MINIMAL_HEAD_FIGURE = (
    '\\documentclass{article}\n'
    '\\usepackage{tikz}\n'
    '\\begin{document}\n'
    '\\pagestyle{empty}\n'
)

_BEGIN_DOCUMENT = '\\begin{document}'

_DOCUMENTCLASS_RE = re.compile(
    r'\\documentclass\s*(\[[^\]]*\])?\s*\{([^}]*)\}')


def _is_beamer_preamble(preamble):
    '''preamble 的 \\documentclass 是否为 beamer（逗号分隔的类名列表）。'''
    match = _DOCUMENTCLASS_RE.search(preamble)
    if match is None:
        return False
    return 'beamer' in [name.strip() for name in match.group(2).split(',')]


def find_begin_document_offset(document_text):
    '''返回 \\begin{document} 的起始 offset；找不到返回 None。'''
    index = document_text.find(_BEGIN_DOCUMENT)
    return index if index != -1 else None


def build_snippet_document(math_raw, document_text=None):
    '''把数学区域源文本包装成完整可编译文档。

    math_raw 含两侧定界符（$x$ / \\[x\\] / \\begin{equation}...\\end{equation}），
    原样放入 document body，各类定界符无需区分。document_text 为当前
    文档全文，用于截取 preamble；None 或无 \\begin{document} 时用最小
    preamble。
    '''
    head = None
    if document_text:
        index = document_text.find(_BEGIN_DOCUMENT)
        if index != -1:
            # preamble 原样保留（含 \documentclass / \usepackage /
            # \newcommand / %!TEX 注释——注释对编译无影响）。空页样式
            # 在 body 开头声明，晚于 preamble 内任何 \pagestyle，必然生效。
            head = document_text[:index] + _BEGIN_DOCUMENT + '\n\\pagestyle{empty}\n'
    if head is None:
        head = _MINIMAL_HEAD
    return head + math_raw.strip() + '\n\\end{document}\n'


def build_figure_document(figure_raw, root_text=None):
    '''把图形片段（tikzpicture 环境，含可选参数）包装成完整可编译文档。

    与公式包装的两点差异：
    - \\noindent：图默认从版面左上角起排，避免首行段落缩进把墨迹推右；
    - root preamble 是 beamer 时把图放进 frame：beamer body 里裸放
      tikzpicture 时 ``\\node[draw,right=of A]`` 这类依赖自动命名的写法
      实测直接 ``No shape named 'A' is known`` 且不产出 PDF，包进 frame
      后干净通过；overlay 语义（<+->/\\only）也只在 frame 内成立。
    '''
    raw = figure_raw.strip()
    head = None
    is_beamer = False
    if root_text:
        index = root_text.find(_BEGIN_DOCUMENT)
        if index != -1:
            preamble = root_text[:index]
            is_beamer = _is_beamer_preamble(preamble)
            head = (preamble + _BEGIN_DOCUMENT
                    + '\n\\pagestyle{empty}\n\\thispagestyle{empty}\n')
    if head is None:
        head = _MINIMAL_HEAD_FIGURE
    if is_beamer:
        body = '\\begin{frame}\n' + raw + '\n\\end{frame}\n'
    else:
        body = '\\noindent\n' + raw + '\n'
    return head + body + '\\end{document}\n'


def cache_key(wrapped_text, engine, cwd=None):
    '''编译结果缓存键：完整包装文档 + 引擎 + 编译工作目录的 sha256 前 16 hex。

    引擎必须参与键——同一公式在 xelatex（fontspec/系统字体）与
    pdflatex 下字形不同。cwd 也必须参与：多文件项目里片段内的相对路径
    （\\includegraphics{figs/x.png}）是相对 cwd 解析的，两个项目 preamble
    与片段字节完全相同而仅图不同时会互相串味。16 hex 在单用户缓存目录
    规模下碰撞概率可忽略，且与 synctex_folder 的哈希命名惯例一致。
    '''
    payload = engine.encode('utf-8') + b'\x00' + wrapped_text.encode('utf-8')
    if cwd:
        payload += b'\x00' + cwd.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:16]

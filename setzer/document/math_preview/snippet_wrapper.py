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

'''公式 snippet 包装（纯逻辑，无 gi 依赖，headless 可测）。

把数学区域源文本（含定界符）包装成可独立编译的完整 LaTeX 文档。

核心决策：复用原文档 preamble（``\\begin{document}`` 之前的全部内容），
让用户自定义宏（\\newcommand）与宏包（bm、siunitx、tikz...）在预览中
同样生效——错误渲染比慢渲染更糟。无 ``\\begin{document}`` 的文档
（snippet 文件、纯片段）回退到最小 preamble（article + amsmath/amssymb），
保证任何输入至少有可编译的兜底。
'''

import hashlib

# 最小兜底 preamble：只加预览公式最常用的两个数学宏包，不猜用户的
# 完整环境（猜错的宏包反而制造编译失败）。
_MINIMAL_HEAD = (
    '\\documentclass{article}\n'
    '\\usepackage{amsmath}\n'
    '\\usepackage{amssymb}\n'
    '\\begin{document}\n'
    '\\pagestyle{empty}\n'
)

_BEGIN_DOCUMENT = '\\begin{document}'


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


def cache_key(wrapped_text, engine):
    '''编译结果缓存键：完整包装文档 + 引擎的 sha256 前 16 hex。

    引擎必须参与键——同一公式在 xelatex（fontspec/系统字体）与
    pdflatex 下字形不同。16 hex 在单用户缓存目录规模下碰撞概率可忽略，
    且与 synctex_folder 的哈希命名惯例一致。
    '''
    digest = hashlib.sha256(
        engine.encode('utf-8') + b'\x00' + wrapped_text.encode('utf-8'))
    return digest.hexdigest()[:16]

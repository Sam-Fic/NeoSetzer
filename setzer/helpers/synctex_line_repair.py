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

'''按点击处的 PDF 文本纠正 synctex 给出的源码行号。

PhiTeX 1.0.2 的 `--synctex` 行号取自最后一次引擎运行的输入行：同一页里
大段标题、列表项会被记成后面某个章节命令的行号。实测同一份文档，xelatex
`-synctex=1` 给出 71/74/75/77 四行，phitex 全给 82——反向同步于是跳到
"2.1 试件与传感器布置"，正向同步也会滚到错误位置。h/v 坐标是准的，坏的
只有行号，所以靠点击坐标救不回来，只能拿点击处那一行的文字回源码里匹配。

纯逻辑、不依赖 GTK，便于单测（见 tests/python/test_backward_sync_match.py）。
'''

import difflib
import re
import unicodedata

# 少于这个字数（去空白后）的 PDF 行不参与纠正：页码、图号、单个符号
# 在整篇源码里到处都是匹配，改错的概率高于改对。
_MIN_PDF_CHARS = 4

# synctex 行与 PDF 文本的相似度到这么高就认为行号没问题，不再二次猜测。
_TRUST_SYNCTEX = 0.35

# 纠正后的行必须足够像，并且明显优于原行、明显优于第二名，才替换。
# 三个条件同时成立很难靠巧合满足，避免把其它引擎正确的 synctex 改坏。
_MIN_BEST = 0.5
_MIN_GAIN = 0.25
_MIN_LEAD = 0.1

# 正向同步（源码 → PDF）的阈值。实测正确匹配的相似度在 0.78-0.97，
# 0.55 足够宽松，同时靠"领先第二名 0.1"挡住重复的短行。
_FORWARD_MIN_BEST = 0.55
_FORWARD_MIN_LEAD = 0.1


# PDF 排版会把源码里的 ASCII 引号、连字符换成弯引号与长短破折号，
# `--` 也会连成 `–`。不统一就凭空多出一个差异字符，长行因此匹配不上。
_PUNCTUATION = str.maketrans({
    '’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': '-',
})


def normalize(text):
    '''去掉全部空白：PDF 与源码的断词、缩进、换行都不该影响比较。'''
    text = unicodedata.normalize('NFKC', text or '')
    return re.sub(r'\s+', '', text.translate(_PUNCTUATION))


def _score_line(pdf_text, source_text):
    '''两行**已 normalize** 的文本的相似度（0.0 - 1.0，-1.0 表示预筛淘汰）。

    两种排版差异要覆盖：
      - 源码是 `\\section{实验目的}` / `\\item 实现…`，PDF 里只剩可见文字；
      - 源码硬换行时，PDF 的一行只对应源码行的一段。
    逐字符相似度对第一种偏低，最长公共片段占比能补上。
    '''
    if not pdf_text or not source_text:
        return -1.0
    pdf_chars = set(pdf_text)
    if len(pdf_chars & set(source_text)) < max(2, len(pdf_chars) * 0.5):
        return -1.0
    matcher = difflib.SequenceMatcher(None, pdf_text, source_text, autojunk=False)
    result = matcher.ratio()
    size = matcher.find_longest_match(0, len(pdf_text), 0, len(source_text)).size
    if size >= _MIN_PDF_CHARS:
        result = max(result, 0.6 + 0.35 * min(1.0, size / len(pdf_text)))
    return result


def strip_latex(text):
    '''把源码行里的 LaTeX 标记去掉，尽量留下 PDF 里可见的文字。

    只服务于匹配，不追求严格解析：`\\section{实验目的}` → `实验目的`，
    `\\item 实现…` → `实现…`，数学/注释等噪声一并丢掉。
    '''
    text = re.sub(r'(?<!\\)%.*$', '', text or '')
    text = re.sub(r'\\begin\{[^}]*\}|\\end\{[^}]*\}', ' ', text)
    text = re.sub(r'\\[a-zA-Z@]+\*?(\[[^\]]*\])?', ' ', text)
    text = text.replace('\\\\', ' ')
    return re.sub(r'[{}~$&_^]', '', text)


def has_matchable_text(source_text):
    '''源码行去掉 LaTeX 标记后是否有足够文字用于匹配。

    预览层靠它跳过 `\\begin{itemize}` 这类没有可见文字的行，免得把整份
    PDF 的文本布局都翻一遍才发现无从匹配。
    '''
    return len(normalize(strip_latex(source_text))) >= _MIN_PDF_CHARS


def _forward_score(pdf_text, source_text):
    '''正向同步的相似度：源码行是查询，PDF 行是候选（两者已 normalize）。

    与 _score_line 的偏向相反：这里先要求源码行几乎被 PDF 行完整包含
    （coverage），再按逐字符相似度打分。否则 "A simple bullet list:" 和
    "A Appendix Structure Sample" 会因为共用 "mple" 被最长公共片段抬到
    0.65；目录里 "6.1 Lists ....." 也会和正文标题互相干扰。
    '''
    if not pdf_text or not source_text:
        return -1.0
    matcher = difflib.SequenceMatcher(None, source_text, pdf_text, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    if matched < _MIN_PDF_CHARS or matched / len(source_text) < 0.7:
        return -1.0
    return matcher.ratio()


def match_pdf_line(source_text, pdf_line_texts):
    '''在 PDF 文本行里找与源码行最匹配的一条，返回索引；匹配不到返回 None。

    正向同步时 synctex 给的行号可能来自 phitex 的错误归属，但页面上的文字
    是对的，于是拿源码行文字反过来找 PDF 行。要求最高分够高、且明显领先
    第二名，遇到重复内容（同名列表项、章节标题反复出现）宁可放弃。
    '''
    source = normalize(strip_latex(source_text))
    if not has_matchable_text(source_text):
        return None

    best_score, best_index = -1.0, -1
    second_score = -1.0
    for index, pdf_text in enumerate(pdf_line_texts):
        value = _forward_score(normalize(pdf_text), source)
        if value > best_score:
            second_score = best_score
            best_score, best_index = value, index
        elif value > second_score:
            second_score = value

    if best_index < 0 or best_score < _FORWARD_MIN_BEST:
        return None
    if second_score >= 0 and best_score < second_score + _FORWARD_MIN_LEAD:
        return None
    return best_index


def repair_line(current_line, pdf_line_text, source_lines):
    '''返回纠正后的 0-based 行号；无需或无法纠正时原样返回 current_line。

    current_line 是 synctex 给的行号（0-based），pdf_line_text 是点击处
    PDF 文本行的完整内容，source_lines 是目标文件的全部行。
    '''
    pdf_text = normalize(pdf_line_text)
    if len(pdf_text) < _MIN_PDF_CHARS:
        return current_line
    if current_line < 0 or current_line >= len(source_lines):
        return current_line

    current_score = -1.0
    best_score, best_line = -1.0, -1
    second_score = -1.0
    for index, source_text in enumerate(source_lines):
        value = _score_line(pdf_text, normalize(source_text))
        if index == current_line:
            current_score = value
        if value > best_score:
            second_score = best_score
            best_score, best_line = value, index
        elif value > second_score:
            second_score = value

    if current_score >= _TRUST_SYNCTEX:
        return current_line
    if best_line in (-1, current_line):
        return current_line
    if best_score < _MIN_BEST:
        return current_line
    if best_score < current_score + _MIN_GAIN:
        return current_line
    if best_score < second_score + _MIN_LEAD:
        return current_line
    return best_line

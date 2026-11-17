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

from setzer.helpers.synctex_line_repair import normalize, strip_latex

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

# 纯边界行：\begin{env}[opts]{args} / \end{env} / 显示数学定界符独占一行。
# synctex 常把这类行的记录归到周围段落盒（实测：\begin{figure}
# 返回的是浮动体上方正文行的整段宽盒）：正向查询/块矩形并集要跳过，
# 双向跳转落点要重定向到块内内容行。行内混有正文内容时不算边界行。
BOUNDARY_LINE_RE = re.compile(
    r'^\\(?:begin\{[^}]+\}(?:\[[^\]]*\])?(?:\{[^}]*\})*|end\{[^}]+\}|\[|\]|\$\$)\s*$')

# 纯无输出行：整行只由无可见输出的命令（连同参数）组成。\label{eq:x}
# 经 strip_latex 会剩下 label key，但它不产生可见输出、synctex 记录是
# 周围段落盒（实测返回公式上方正文行的整段宽盒），不能当内容行。
# 用白名单而非「strip 后判空」：\vdots/\cdots 等符号命令 strip 后也为空，
# 但它们有可见输出与自己的 synctex 记录（实测 \vdots 行有点阵行盒）。
# \tag 不在此列：它产生可见的自定义公式编号。
_NO_OUTPUT_LINE_RE = re.compile(
    r'^(?:\\(?:label|index|glossary|nonumber|notag|phantom|vspace\*?|hspace\*?)'
    r'(?:\s*\{[^{}]*\})?\s*)+$')


def is_no_output_line(stripped):
    '''整行是否只含无可见输出的命令（输入为已 strip 的行）。'''
    return bool(_NO_OUTPUT_LINE_RE.match(stripped))


# 不产生可见字形的常见命令（排版/间距/引用类）。strip_latex 后为空的
# 行若还含白名单之外的命令（\vdots、\alpha 等符号命令），则视为有
# 可见输出：它们有自己的 synctex 记录，可以作为重定向目标。
_CMD_NAME_RE = re.compile(r'\\([a-zA-Z@]+)')
_NO_GLYPH_COMMANDS = frozenset({
    'hline', 'cline', 'centering', 'raggedright', 'raggedleft',
    'noindent', 'indent', 'par', 'newline', 'linebreak', 'nolinebreak',
    'pagebreak', 'nopagebreak', 'newpage', 'clearpage', 'cleardoublepage',
    'allowbreak', 'hspace', 'vspace', 'hskip', 'vskip', 'kern', 'mkern',
    'hfill', 'vfill', 'hfil', 'vfil', 'hss', 'vss', 'quad', 'qquad',
    'medskip', 'smallskip', 'bigskip', 'strut', 'mathstrut',
    'tiny', 'scriptsize', 'footnotesize', 'small', 'normalsize',
    'large', 'Large', 'LARGE', 'huge', 'Huge',
    'label', 'index', 'glossary', 'nonumber', 'notag', 'protect', 'relax',
    'phantom', 'hphantom', 'vphantom',
    'displaystyle', 'textstyle', 'scriptstyle', 'scriptscriptstyle',
    'limits', 'nolimits', 'setlength', 'addtolength', 'setcounter',
    'stepcounter', 'refstepcounter',
})


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


def nearest_content_line(source_lines, line):
    r'''line（1-based）为块内无可见内容的行时，返回块内最近的内容行（仍 1-based）。

    双向跳转落点修正用：公式内点击 synctex 常返回 \end{equation}、光标停在
    \begin{align} 或 \label{...} 上正向查询拿到的是周围段落盒——纯边界行与
    纯无输出行（\label/\nonumber 等）本身没有可见内容，重定向到块内最近
    的内容行（跳过空行/注释/边界行/无输出行）。其它行、块外行或块内无
    内容行时原样返回。
    '''
    if not source_lines or not 1 <= line <= len(source_lines):
        return line
    stripped = source_lines[line - 1].strip()
    if not BOUNDARY_LINE_RE.match(stripped) and not is_no_output_line(stripped):
        return line
    start, end, kind, _env = expand_to_block(source_lines, line)
    if kind == 'line':
        return line

    def is_content(ln):
        s = source_lines[ln - 1].strip()
        if not s or s.startswith('%'):
            return False
        if BOUNDARY_LINE_RE.match(s) or is_no_output_line(s):
            return False
        # 去掉 LaTeX 标记后无可见文字的行（\hline、\centering 等）没有
        # synctex 记录，重定向过去跳转依旧落空。但符号命令行（\vdots、
        # \alpha 等）strip 后虽为空，却有可见输出与自己的记录。
        if normalize(strip_latex(s)):
            return True
        return any(name not in _NO_GLYPH_COMMANDS
                   for name in _CMD_NAME_RE.findall(s))

    for dist in range(1, end - start + 1):
        for ln in (line - dist, line + dist):
            if start <= ln <= end and is_content(ln):
                return ln
    return line
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

"""PDF 选区 → 源码映射（SyncTeX 反查 + 整行/整块扩展）。

预览的块吸附与「PDF 选区 → 批注/直发」共用本模块。所有 synctex subprocess
查询在后台线程跑；poppler 与 Gtk buffer 的读取一律在主线程预提取后传入
（线程里只做 subprocess + 纯逻辑 + 磁盘读）。

契约：
  - 文字拖选 → 首末两点反查取行范围（整行）；任一端点落在块环境/显示数学
    内 → 升级整块。focus 优先用 synctex Column/Offset 截源码子串，退化用
    PDF 提取文本。
  - 块/图片点选 → center 反查 → expand_to_block 整块；focus 为块内 PDF
    渲染文本（图片无文本时 '(image block)'）。
  - 行号经 PhiTeX 修复（correct_synctex_line，传入主线程快照避免跨线程读
    buffer）。
"""

import os
import threading

import gi
gi.require_version('Poppler', '0.18')
from gi.repository import GLib, Poppler

from setzer.helpers import synctex_query
from setzer.ai_fix import source_blocks
from setzer.ai_fix.prompt_builder import _read_file_lines

# 块内正向查询行数上限（跳过空行/注释行后计数），防极端长块卡线程。
_MAX_BLOCK_LINES = 60

# 纯边界行正则与块扩展共用 source_blocks 的定义（双向跳转落点重定向也用它）。
_BOUNDARY_LINE_RE = source_blocks.BOUNDARY_LINE_RE

# 浮动体环境：块矩形需并入 synctex 不记录的整图（\includegraphics 行无
# synctex 记录，图片区域只能从 poppler image mapping 拿）。
_FLOAT_ENVS = {'figure', 'figure*', 'table', 'table*'}

# 图片并入块矩形的垂直间隙容差（points，约一行高）：caption 与图片之间
# 的 \abovecaptionskip 等 glue 实测 ~9pt；取 12pt 覆盖常见排版。
_IMAGE_MERGE_GAP = 12.0

# 点击点命中块矩形的容差（points）：synctex 盒与字形框有少量出入。
_CLICK_TOL = 3.0

# 块吸附接受容差（points）：synctex 盒常比视觉块小（分数上下缘、
# 表格单元格间隙、行距 glue 都不在盒内，实测公式字形 y 166.9..189.3
# 而盒仅 169.5..187.3）。水平放宽到 12；垂直只放 4：块间空隙实测
# ~10pt，垂直容差太大会把间隙里的点击误吸到邻块。
_BLOCK_ACCEPT_PAD_X = 12.0
_BLOCK_ACCEPT_PAD_Y = 4.0

# 表格类环境：synctex 常只记录部分列（实测两列 tabular 只有左列有
# 记录，右列点击反查到块外行），块矩形因此只盖左列。这类环境只要
# 点击点垂直在块内就接受，水平不限（表格通常居中，两侧是页边空白）。
_TABULAR_ENVS = {'tabular', 'tabular*', 'array'}

# 显示数学环境：同表格一样居中排版、两侧是页边空白，且 synctex 盒
# 常比视觉块窄（大括号/编号不在内容行盒内），水平放宽到整行。
_DISPLAY_MATH_ENVS = {
    'equation', 'equation*', 'align', 'align*', 'alignat', 'alignat*',
    'gather', 'gather*', 'multline', 'multline*', 'eqnarray', 'eqnarray*',
    'flalign', 'flalign*', 'displaymath',
}

# 带编号的显示数学环境：编号 (1)/(2) 排在右缘页边处，不在内容行
# synctex 盒内（实测内容盒 x2=372 而编号 x~460-477），但 \end{行} 的
# 记录里有含编号的全宽盒——块矩形需并入它，否则点击编号无法吸附。
_NUMBERED_MATH_ENVS = {
    'equation', 'align', 'alignat', 'gather', 'multline', 'eqnarray', 'flalign',
}

# 空间降级搜索：反查行附近最多考虑的块数与源码行距上限。窗口要足够
# 大：公式间隙点击 synctex 可能返回很远的行（实测返回 \end{document}）。
_FALLBACK_MAX_BLOCKS = 6
_FALLBACK_LINE_WINDOW = 120

# 表格/显示数学视觉矩形：这两类环境的 synctex 正向盒不可靠（实测
# tabular 行盒整体错位、末行记录混入后续段落/标题盒；数学内容无记录，
# 逐行反回前面段落盒）。改用页面字形盒聚行后从点击行按「垂直间隙≤
# 阈值且水平重叠」连通扩展出视觉块；源块归属用「可靠锚行往下源码/
# 页面顺序步进对应」（_visual_block_source）校验，防 edit 反查落邻块。
_GLYPH_ROW_GAP = 10.0
_VISUAL_PAD = 2.0


def _rect_contains(rect, x, y, tol=_CLICK_TOL):
    x1, y1, x2, y2 = rect
    return x1 - tol <= x <= x2 + tol and y1 - tol <= y <= y2 + tol


def _block_accepts(rect, x, y, env):
    r'''块吸附接受判定：水平 12pt / 垂直 4pt 容差；表格与显示数学环境
    垂直在块内即接受（synctex 盒盖不全整个视觉块，两侧是页边空白）。
    env=None 来自 displaymath 候选（\[...\]/$$...$$，无环境名）。'''
    x1, y1, x2, y2 = rect
    if env in _TABULAR_ENVS or env in _DISPLAY_MATH_ENVS or env is None:
        return y1 - _BLOCK_ACCEPT_PAD_Y <= y <= y2 + _BLOCK_ACCEPT_PAD_Y
    return (x1 - _BLOCK_ACCEPT_PAD_X <= x <= x2 + _BLOCK_ACCEPT_PAD_X
            and y1 - _BLOCK_ACCEPT_PAD_Y <= y <= y2 + _BLOCK_ACCEPT_PAD_Y)


def _lines_for(document, filename, active_lines):
    '''活动文档用主线程快照；其它文件磁盘读（线程安全）。'''
    active_path = document.get_filename() if document is not None else None
    if (active_lines is not None and filename and active_path
            and os.path.realpath(filename) == os.path.realpath(active_path)):
        return active_lines
    return _read_file_lines(filename)


def _corrected_line(document, line1, pdf_line_text, lines):
    '''1-based 行号经 PhiTeX 修复后返回（仍 1-based）。'''
    build_system = getattr(document, 'build_system', None)
    if build_system is None:
        return line1
    plain = [l.rstrip('\n').rstrip('\r') for l in lines]
    return build_system.correct_synctex_line(line1 - 1, pdf_line_text,
                                             source_lines=plain) + 1


def block_pdf_rect(root_tex, pdf_filename, block_tex, page, start, end,
                   lines, directory, env=None, image_rects=()):
    r'''块内逐行 synctex view，取 page（0-based）上矩形并集 bbox。

    返回未旋转 top-down points (x1, y1, x2, y2)；无命中返回 None。
    synctex view 的 v 为 y-up 底边：top-down y1 = v - height。

    纯边界行（\begin/\end/显示数学定界符独占一行）与无可见输出行
    （\label{...} 等）跳过：它们的 synctex 记录常是周围段落盒（实测
    \label 行返回公式上方正文行的整段宽盒），混入并集会让框偏离
    实际内容。带编号数学环境（env ∈ _NUMBERED_MATH_ENVS）额外并入
    \end{行} 记录中与内容区垂直重叠的全宽盒（含右缘公式编号）。
    浮动体（env ∈ _FLOAT_ENVS）额外并入与内容区垂直相邻的整图矩形
    （image_rects 为主线程预提取的 poppler image mapping，top-down points）。
    '''
    x1 = y1 = x2 = y2 = None
    used = 0
    for ln in range(start, end + 1):
        if ln - 1 >= len(lines):
            break
        stripped = lines[ln - 1].strip()
        if not stripped or stripped.startswith('%'):
            continue
        if (_BOUNDARY_LINE_RE.match(stripped)
                or source_blocks.is_no_output_line(stripped)):
            continue
        used += 1
        if used > _MAX_BLOCK_LINES:
            break
        for r in synctex_query.query_rectangles(block_tex, pdf_filename, ln, directory):
            if r['page'] != page + 1:
                continue
            rx1, ry1 = r['h'], r['v'] - r['height']
            rx2, ry2 = r['h'] + r['width'], r['v']
            if x1 is None:
                x1, y1, x2, y2 = rx1, ry1, rx2, ry2
            else:
                x1, y1 = min(x1, rx1), min(y1, ry1)
                x2, y2 = max(x2, rx2), max(y2, ry2)
    if x1 is None:
        return None
    if env in _NUMBERED_MATH_ENVS and end <= len(lines):
        # 公式编号在右缘页边处，内容行盒盖不到；\end{行} 的全宽盒
        # 与公式同高，并入后块矩形覆盖编号区。只取与内容区垂直重叠
        # 的盒，防止 end 行记录混入的上下段落盒污染块矩形。
        for r in synctex_query.query_rectangles(block_tex, pdf_filename, end, directory):
            if r['page'] != page + 1:
                continue
            rx1, ry1 = r['h'], r['v'] - r['height']
            rx2, ry2 = r['h'] + r['width'], r['v']
            if ry2 < y1 - 2.0 or ry1 > y2 + 2.0:
                continue
            x1, y1 = min(x1, rx1), min(y1, ry1)
            x2, y2 = max(x2, rx2), max(y2, ry2)
    if env in _FLOAT_ENVS:
        for ix1, iy1, ix2, iy2 in image_rects:
            # 垂直相邻（含重叠）且水平有交集的整图并入块矩形。
            if iy2 < y1 - _IMAGE_MERGE_GAP or iy1 > y2 + _IMAGE_MERGE_GAP:
                continue
            if ix2 < x1 or ix1 > x2:
                continue
            x1, y1 = min(x1, ix1), min(y1, iy1)
            x2, y2 = max(x2, ix2), max(y2, iy2)
    return (x1, y1, x2, y2)


def _glyph_rows(glyph_rects):
    '''字形盒聚行：垂直重叠（含 0.5pt 容差，吸收零高标点盒）合并为
    行簇 (x1, y1, x2, y2)。返回按 y 排序、垂直不相交的行列表。'''
    rows = []
    for x1, y1, x2, y2 in sorted(glyph_rects, key=lambda r: (r[1], r[0])):
        if x2 <= x1 or y2 < y1:
            continue
        if rows and y1 <= rows[-1][3] + 0.5:
            r = rows[-1]
            rows[-1] = (min(r[0], x1), min(r[1], y1),
                        max(r[2], x2), max(r[3], y2))
        else:
            rows.append((x1, y1, x2, y2))
    return rows


def _glyph_band(glyph_rects, x, y):
    '''点击点种子行 + 连通扩展的视觉块 bbox（含 _VISUAL_PAD 外沿）。

    种子：包含点击点的行（2pt 容差）；否则垂直≤4pt 且水平≤12pt 的
    最近行（公式间隙/居中块两侧小空白）。扩展：垂直间隙≤
    _GLYPH_ROW_GAP 且水平重叠的行并入，直到稳定。块间空隙实测≥10.3pt，
    块内行隙≤9.3pt，阈值 10 分隔两者。无字形/无种子返回 None。'''
    rows = _glyph_rows(glyph_rects)
    seed = None
    for r in rows:
        if r[1] - 2 <= y <= r[3] + 2 and r[0] - 2 <= x <= r[2] + 2:
            seed = r
            break
    if seed is None:
        best = None
        for r in rows:
            dy = max(r[1] - y, y - r[3], 0.0)
            dx = max(r[0] - x, x - r[2], 0.0)
            if dy <= 4 and dx <= 12 and (best is None or dy < best[0]):
                best = (dy, r)
        if best is None:
            return None
        seed = best[1]
    band = list(seed)
    changed = True
    while changed:
        changed = False
        for r in rows:
            gap = max(band[1] - r[3], r[1] - band[3])
            if gap <= _GLYPH_ROW_GAP and r[0] < band[2] and r[2] > band[0]:
                nb = [min(band[0], r[0]), min(band[1], r[1]),
                      max(band[2], r[2]), max(band[3], r[3])]
                # 仅当 band 实际扩大才继续迭代（种子行每轮都满足
                # 合并条件，不比较会死循环）。
                if nb != band:
                    band = nb
                    changed = True
    return (band[0] - _VISUAL_PAD, band[1] - _VISUAL_PAD,
            band[2] + _VISUAL_PAD, band[3] + _VISUAL_PAD)


def _is_visual_env(env):
    '''表格/显示数学环境：synctex 盒不可靠，走字形视觉矩形。'''
    return env in _TABULAR_ENVS or env in _DISPLAY_MATH_ENVS or env is None


def _row_anchor_line(row, lines, page, block_tex, pdf_filename, directory):
    '''字形行 → 源码行（edit 反查 + view 盒几何校验）。

    synctex edit 对「自身盒不在自己 view 记录里」的行会落到邻块
    （实测 "A matrix:" 行反查到定理 label 行），故反查后要求该行
    view 记录里有与字形行几何吻合的盒才认锚。不吻合返回 None。'''
    cx, cy = (row[0] + row[2]) / 2, (row[1] + row[3]) / 2
    pos = synctex_query.query_source_position(pdf_filename, page + 1, cx, cy,
                                              directory)
    if pos is None:
        return None
    ln = pos['line']
    if source_blocks.expand_to_block(lines, ln)[2] != 'line':
        return None
    for r in synctex_query.query_rectangles(block_tex, pdf_filename, ln, directory):
        if r['page'] != page + 1:
            continue
        if (abs(r['h'] - row[0]) <= 2.0 and abs(r['v'] - r['height'] - row[1]) <= 2.0
                and abs(r['h'] + r['width'] - row[2]) <= 2.0
                and abs(r['v'] - row[3]) <= 2.0):
            return ln
    return None


def _visual_block_source(band, glyph_rects, lines, page, block_tex,
                         pdf_filename, directory):
    '''band 的源码块归属：从 band 上方最近可靠锚行顺序步进对应。

    synctex edit 对数学内容常返回邻块（实测 cases 公式反查到 matrix
    块、align 首行反查到上一个 equation），仅靠反查行会拿错源块。
    源码与页面同序：从锚行（_row_anchor_line 校验过）往下，源码文本
    行↔页面字形行、源码视觉块↔页面 band 逐一对应消费；遇到 band 即
    返回当前源块。对应失配（段落换行等）返回 None 交回反查行兜底。'''
    rows = _glyph_rows(glyph_rects)
    first = None
    for i, r in enumerate(rows):
        if r[3] > band[1] and r[1] < band[3]:
            first = i
            break
    if first is None or first == 0:
        return None
    anchor = None
    pi = first
    for i in range(first - 1, max(-1, first - 11), -1):
        ln = _row_anchor_line(rows[i], lines, page, block_tex,
                              pdf_filename, directory)
        if ln is not None:
            anchor = ln
            pi = i + 1
            break
    if anchor is None:
        return None
    ln = anchor + 1
    while ln <= len(lines) and ln <= anchor + 80 and pi < len(rows):
        st = lines[ln - 1].strip()
        if (not st or st.startswith('%') or _BOUNDARY_LINE_RE.match(st)
                or source_blocks.is_no_output_line(st)):
            ln += 1
            continue
        start, end, kind, env = source_blocks.expand_to_block(lines, ln)
        if kind != 'line':
            if not _is_visual_env(env) or pi > first:
                return None
            r = rows[pi]
            other = _glyph_band(glyph_rects, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
            if other is None:
                return None
            if other == band:
                return (start, end, kind, env)
            while pi < len(rows) and rows[pi][3] > other[1] and rows[pi][1] < other[3]:
                pi += 1
            ln = end + 1
            continue
        if pi >= first:
            return None
        pi += 1
        ln += 1
    return None


def _fallback_block_at(lines, pos_line, page, x, y, block_tex, pdf_filename,
                       directory, image_rects, glyph_rects=()):
    '''空间降级：反查行不在块内（或块矩形不含点击点）时，在附近源码块中
    找 PDF 矩形真正覆盖点击点的最小块。synctex 对无记录区域（图片内部、
    公式间隙）会返回最近的**块外**行，仅靠 expand_to_block 会静默失败。

    block_tex 为反查命中的文件（块所在文件，可能是 include 子文件）。
    返回 (start, end, kind, env, rect) 或 None。后台线程调用。
    '''
    ranges = source_blocks.compute_block_ranges(lines)
    band = _glyph_band(glyph_rects, x, y) if glyph_rects else None
    if band is not None:
        src = _visual_block_source(band, glyph_rects, lines, page,
                                   block_tex, pdf_filename, directory)
        if src is not None:
            start, end, kind, env = src
            rect = band
            if rect is not None and _block_accepts(rect, x, y, env):
                return (start, end, kind, env, rect)
    candidates = []
    for start, end, kind, env in ranges:
        dist = min(abs(start - pos_line), abs(end - pos_line))
        if dist <= _FALLBACK_LINE_WINDOW:
            candidates.append((dist, start, end, kind, env))
    candidates.sort(key=lambda c: (c[0], c[2] - c[1]))
    best = None
    for _dist, start, end, kind, env in candidates[:_FALLBACK_MAX_BLOCKS]:
        if band is not None and _is_visual_env(env):
            # 视觉环境矩形即 band（归属已由上方步进对应优先判定；
            # 走到这里说明步进失配，按源码距离猜最近块）。
            rect = band
        else:
            rect = block_pdf_rect(block_tex, pdf_filename, block_tex,
                                  page, start, end, lines, directory,
                                  env=env, image_rects=image_rects)
        if rect is None or not _block_accepts(rect, x, y, env):
            continue
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if best is None or area < best[0]:
            best = (area, start, end, kind, env, rect)
    if best is None:
        return None
    _area, start, end, kind, env, rect = best
    return (start, end, kind, env, rect)


def resolve_block(document, pdf_filename, page, x, y, active_lines=None,
                  image_rects=(), glyph_rects=()):
    '''单击块吸附：反查点击点 → 源码块 → 正向并集矩形（本页）。

    page 0-based，x/y 未旋转 PDF points。返回
    {'filename', 'start_line', 'end_line', 'kind', 'env', 'rect'}；
    点击不在块内（kind='line'）或查不到返回 None。后台线程调用。

    image_rects 为主线程预提取的本页 poppler image mapping 矩形列表
    （线程里不读 poppler）；glyph_rects 同为本页字形盒列表（表格/
    显示数学视觉矩形用）。反查行不在块内或块矩形不含点击点时，
    走空间降级搜索（图片内部等 synctex 无记录区域的典型情形）。
    '''
    tex_filename = document.get_filename()
    if not tex_filename or not pdf_filename:
        return None
    directory = synctex_query.synctex_directory_for_source(tex_filename, pdf_filename)
    pos = synctex_query.query_source_position(pdf_filename, page + 1, x, y, directory)
    if pos is None:
        return None
    lines = _lines_for(document, pos['filename'], active_lines)
    if not lines:
        return None
    start, end, kind, env = source_blocks.expand_to_block(lines, pos['line'])
    rect = None
    band = _glyph_band(glyph_rects, x, y) if glyph_rects else None
    if kind != 'line':
        if band is not None and _is_visual_env(env):
            src = _visual_block_source(band, glyph_rects, lines, page,
                                       pos['filename'], pdf_filename, directory)
            if src is not None:
                start, end, kind, env = src
            rect = band
        else:
            rect = block_pdf_rect(tex_filename, pdf_filename, pos['filename'],
                                  page, start, end, lines, directory,
                                  env=env, image_rects=image_rects)
        # 反查行已在块内：synctex 盒比视觉块小是常态，盒外沿宽容差内
        # 直接接受（源块映射是对的，高亮盒尽力而为）；只有点击点远离
        # 块矩形时才怀疑反查失真，走空间降级。
        if rect is not None and _block_accepts(rect, x, y, env):
            return {'filename': pos['filename'], 'start_line': start, 'end_line': end,
                    'kind': kind, 'env': env, 'rect': rect}
    fallback = _fallback_block_at(lines, pos['line'], page, x, y,
                                  pos['filename'], pdf_filename,
                                  directory, image_rects, glyph_rects=glyph_rects)
    if fallback is None:
        return None
    start, end, kind, env, rect = fallback
    return {'filename': pos['filename'], 'start_line': start, 'end_line': end,
            'kind': kind, 'env': env, 'rect': rect}


def _line_text_at(poppler_document, page, x, y):
    '''点击点所在 PDF 行文本（PhiTeX 行修复用）。'''
    try:
        poppler_page = poppler_document.get_page(page)
        rect = Poppler.Rectangle()
        rect.x1, rect.y1 = x - 1, y - 1
        rect.x2, rect.y2 = x + 1, y + 1
        return poppler_page.get_selected_text(Poppler.SelectionStyle.LINE, rect) or ''
    except Exception:
        return ''


def map_selection(preview, document, payload, on_done):
    '''PDF 选区 → 源码映射入口。主线程预提取 poppler/buffer，后台线程查询，
    idle 回调 on_done(mapping | None)。

    mapping: {'filename', 'start_line', 'end_line', 'focus_text', 'focus_kind'}。
    '''
    if payload is None or preview.pdf_filename is None or preview.poppler_document is None:
        GLib.idle_add(on_done, None)
        return
    pdf_filename = preview.pdf_filename
    tex_filename = document.get_filename()
    active_lines = None
    if tex_filename is not None:
        buffer = document.source_buffer
        active_lines = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), True).splitlines(keepends=True)

    extra = {}
    poppler_document = preview.poppler_document
    if payload['kind'] in ('block', 'image'):
        poppler_page = poppler_document.get_page(payload['page'])
        x1, y1, x2, y2 = payload['rect']
        rect = Poppler.Rectangle()
        rect.x1, rect.y1, rect.x2, rect.y2 = x1, y1, x2, y2
        text = (poppler_page.get_selected_text(Poppler.SelectionStyle.GLYPH, rect) or '').strip()
        if not text and payload['kind'] == 'image':
            text = '(image block)'
        extra['focus_text'] = text
        extra['pdf_line_text'] = poppler_page.get_selected_text(Poppler.SelectionStyle.LINE, rect) or ''
        # 主线程预提取本页整图矩形，供后台线程的空间降级搜索用。
        try:
            extra['image_rects'] = [
                (m.area.x1, m.area.y1, m.area.x2, m.area.y2)
                for m in (poppler_page.get_image_mapping() or [])]
        except Exception:
            extra['image_rects'] = []
        try:
            ok, glyphs = poppler_page.get_text_layout()
            extra['glyph_rects'] = [(g.x1, g.y1, g.x2, g.y2) for g in glyphs] if ok else []
        except Exception:
            extra['glyph_rects'] = []
    else:
        rects = payload['rects']
        first, last = min(rects), max(rects)
        ra, rb = rects[first], rects[last]
        extra['point_a'] = (first, ra.x1, ra.y1)
        extra['point_b'] = (last, rb.x2, rb.y2)
        extra['line_text_a'] = _line_text_at(poppler_document, first, ra.x1, ra.y1)
        extra['line_text_b'] = _line_text_at(poppler_document, last, rb.x2, rb.y2)

    def _run():
        mapping = _map_sync(document, payload, extra, pdf_filename,
                            tex_filename, active_lines)
        GLib.idle_add(on_done, mapping)

    threading.Thread(target=_run, daemon=True).start()


def _map_sync(document, payload, extra, pdf_filename, tex_filename, active_lines):
    directory = (synctex_query.synctex_directory_for_source(tex_filename, pdf_filename)
                 if tex_filename else synctex_query.synctex_directory_for(pdf_filename))

    if payload['kind'] in ('block', 'image'):
        # 块吸附已解析出源码块时直接复用（避免二次反查，且与高亮框
        # 对应的块严格一致）；整图同步命中路径无源码信息，走反查。
        if (payload.get('filename') and payload.get('start_line')
                and payload.get('end_line')):
            return {'filename': payload['filename'],
                    'start_line': payload['start_line'],
                    'end_line': payload['end_line'],
                    'focus_text': extra.get('focus_text') or '',
                    'focus_kind': 'block'}
        cx, cy = payload['center']
        pos = synctex_query.query_source_position(
            pdf_filename, payload['page'] + 1, cx, cy, directory)
        if pos is None:
            return None
        lines = _lines_for(document, pos['filename'], active_lines)
        if not lines:
            return None
        start, end, kind, _env = source_blocks.expand_to_block(lines, pos['line'])
        if kind == 'line':
            # 反查落在块外行（图片内部等无 synctex 记录区域）：空间降级。
            fallback = _fallback_block_at(
                lines, pos['line'], payload['page'], cx, cy,
                pos['filename'], pdf_filename, directory,
                extra.get('image_rects', ()),
                glyph_rects=extra.get('glyph_rects', ()))
            if fallback is None:
                return None
            start, end, kind, _env, _rect = fallback
        return {'filename': pos['filename'], 'start_line': start, 'end_line': end,
                'focus_text': extra.get('focus_text') or '', 'focus_kind': 'block'}

    # 文字拖选：首末两点反查。
    page_a, xa, ya = extra['point_a']
    page_b, xb, yb = extra['point_b']
    pos_a = synctex_query.query_source_position(pdf_filename, page_a + 1, xa, ya, directory)
    pos_b = synctex_query.query_source_position(pdf_filename, page_b + 1, xb, yb, directory)
    if pos_a is None and pos_b is None:
        return None
    if pos_a is None:
        pos_a = pos_b
    if pos_b is None:
        pos_b = pos_a
    same_file = pos_a['filename'] == pos_b['filename']
    filename = pos_a['filename']
    lines = _lines_for(document, filename, active_lines)
    if not lines:
        return None
    la = _corrected_line(document, pos_a['line'], extra.get('line_text_a'), lines)
    lb = (_corrected_line(document, pos_b['line'], extra.get('line_text_b'), lines)
          if same_file else la)
    start, end = min(la, lb), max(la, lb)
    # 任一端点落在块内 → 升级整块（拖选公式/表格一部分 → 整块上下文）。
    for ln in (la, lb):
        s, e, kind, _env = source_blocks.expand_to_block(lines, ln)
        if kind != 'line':
            start, end = s, e
            break

    focus_text = None
    focus_kind = 'pdf-text'
    if (same_file and pos_a.get('offset') is not None
            and pos_b.get('offset') is not None):
        oa, ob = pos_a['offset'], pos_b['offset']
        if la == lb:
            focus_text = lines[la - 1].rstrip('\n')[oa:ob + 1]
        else:
            parts = [lines[la - 1].rstrip('\n')[oa:]]
            parts.extend(l.rstrip('\n') for l in lines[la:lb - 1])
            parts.append(lines[lb - 1].rstrip('\n')[:ob + 1])
            focus_text = '\n'.join(parts)
        if focus_text and focus_text.strip():
            focus_kind = 'source'
        else:
            focus_text = None
    if focus_text is None:
        focus_text = payload.get('text') or ''
        focus_kind = 'pdf-text'
    return {'filename': filename, 'start_line': start, 'end_line': end,
            'focus_text': focus_text, 'focus_kind': focus_kind}
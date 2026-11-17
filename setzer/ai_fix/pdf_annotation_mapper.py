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
                   lines, directory):
    '''块内逐行 synctex view，取 page（0-based）上矩形并集 bbox。

    返回未旋转 top-down points (x1, y1, x2, y2)；无命中返回 None。
    synctex view 的 v 为 y-up 底边：top-down y1 = v - height。
    '''
    x1 = y1 = x2 = y2 = None
    used = 0
    for ln in range(start, end + 1):
        if ln - 1 >= len(lines):
            break
        stripped = lines[ln - 1].strip()
        if not stripped or stripped.startswith('%'):
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
    return (x1, y1, x2, y2)


def resolve_block(document, pdf_filename, page, x, y, active_lines=None):
    '''单击块吸附：反查点击点 → 源码块 → 正向并集矩形（本页）。

    page 0-based，x/y 未旋转 PDF points。返回
    {'filename', 'start_line', 'end_line', 'kind', 'env', 'rect'}；
    点击不在块内（kind='line'）或查不到返回 None。后台线程调用。
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
    if kind == 'line':
        return None
    rect = block_pdf_rect(tex_filename, pdf_filename, pos['filename'],
                          page, start, end, lines, directory)
    if rect is None:
        return None
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
        cx, cy = payload['center']
        pos = synctex_query.query_source_position(
            pdf_filename, payload['page'] + 1, cx, cy, directory)
        if pos is None:
            return None
        lines = _lines_for(document, pos['filename'], active_lines)
        if not lines:
            return None
        start, end, _kind, _env = source_blocks.expand_to_block(lines, pos['line'])
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
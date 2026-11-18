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

'''`phitex session compare --json` 的解析与 AI 编辑基线文件的管理。

compare 的输出是单个 JSON 对象（phitex 1.0.3+，crates/cli/src/compare.rs 的
changes_for）：changes[].{n, kind, page, bbox, file, line, old_text, new_text,
section}。**page/bbox 是 diff PDF（JOB-diff 旁路构建的 latexdiff 标记文档）
上的坐标**，不是正式 PDF 的——所以矩形只画在 diff 视图里；file/line 指当前
版本文件（c.new），留作以后做正式 PDF 上的近似高亮。

bbox 是 [x0, y0, x1, y1]，points、页左上原点、y 向下；而 NeoSetzer 预览的
synctex 矩形惯例是 v = 底边距页顶的距离（y 轴向上）。两套坐标的"页顶"是同
一个参照物，差别只在命名方向：底边在 y 向下系里就是 y1，故 v = y1。

本模块刻意不 import gi：解析、坐标换算、基线读写都是纯逻辑，CI 无显示直跑。
'''

import json
import os

# AI 编辑前的正文快照目录。放在文档目录的 .neosetzer/ 下（与项目构建配置
# build.json 同一惯例）：随文档走、应用重启后仍在——agent 跑几分钟期间
# NeoSetzer 若重启，基线还在，下一次构建仍能出 diff。
_BASELINE_SUBDIR = os.path.join('.neosetzer', 'ai-baseline')


def parse_compare_json(stdout):
    '''compare --json 的 stdout → payload dict；解析不出返回 None。

    phitex 把 JSON 打在 stdout（错误路径也是：{"error": ..., "status": N}），
    前后可能混有非 JSON 行，故逐行找第一个以 { 开头且能整体 loads 的行——
    与 PhitexSession._first_json_object 同一语义，这里独立实现是为了让本
    模块不依赖 build_system 的导入链。
    '''
    for line in (stdout or '').splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def compare_failed(payload):
    '''payload 是否是请求级失败（REV 不存在、无 watch 之类）。

    compare 的退出码语义：0 成功、1 diff 构建有 TeX 错误（payload 仍带
    changes，只是 page/bbox 可能全 null）、3/4 是请求被拒，那种情况 JSON
    形如 {"error": ..., "status": N}。TeX 错误不算失败——错误信息交给
    调用方决定要不要提示，changes 该展示还是展示。非 dict（None / 解析
    垃圾）一律按失败处理，调用方无需再单独判空。
    '''
    if not isinstance(payload, dict):
        return True
    return 'error' in payload and 'changes' not in payload


def changes_to_rectangles(changes):
    '''compare 的 changes → 预览矩形列表（synctex 矩形惯例 + kind）。

    输出元素：{page, h, v, width, height, kind, n}。page/bbox 为 null 的
    change 是"这次构建里没显示出来"（compare.rs 对未显示的 change 给
    null），跳过；kind/n 原样带上，presenter 按 kind 着色、弹窗按 n 反查。
    '''
    rectangles = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        bbox = change.get('bbox')
        page = change.get('page')
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            # 空盒（理论不该出现）对画高亮毫无意义，还可能在合并时传染邻盒。
            continue
        rectangles.append({'page': page,
                           'h': x0,
                           'v': y1,
                           'width': x1 - x0,
                           'height': y1 - y0,
                           'kind': change.get('kind') or 'change',
                           'n': change.get('n')})
    return rectangles


def baseline_path_for(doc_dir, basename):
    '''AI 基线快照的落盘路径：{doc_dir}/.neosetzer/ai-baseline/{basename}。

    basename 带原扩展名（main.tex → main.tex）：compare 只认得 LaTeX 文本，
    保留扩展名也让用户在文件管理器里认得出这份快照是什么。
    '''
    return os.path.join(doc_dir, _BASELINE_SUBDIR, basename)


def write_baseline(text, doc_dir, basename):
    '''写入快照，返回路径；写不了（目录只读等）返回 None。

    覆盖式写：基线语义就是"最近一次 AI 发送时的正文"，旧的没保留价值。
    '''
    path = baseline_path_for(doc_dir, basename)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    except OSError:
        return None
    return path


def read_baseline(path):
    '''读回快照文本；文件没了/读不了返回 None（照常清掉过期状态）。'''
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return None


def baseline_matches(text, path):
    '''当前正文与基线是否一致（逐字节比较，百 KB 级文档也就毫秒级）。'''
    return text == read_baseline(path)


def clear_baseline(doc_dir, basename):
    '''删掉快照文件（容错：不存在/删不掉都无所谓）。'''
    try:
        os.remove(baseline_path_for(doc_dir, basename))
    except OSError:
        pass

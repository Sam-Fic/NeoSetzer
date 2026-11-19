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

'''PhiTeX viewer WebSocket 协议的纯逻辑（不经网络、不 import gi）。

watch 的 viewer 端点（`session start` 输出的 `viewer http://127.0.0.1:PORT/
TOKEN/`，`session status --json` 里也有同一 URL）上挂着 WS 服务：页 hash 与
逐字形源码映射（origins）这两样 `status --json` 给不出的元数据只在这里。
协议钉死在 PhiTeX 源码（crates/editor/live/src/view.rs 的 Viewer::answer、
docs/note-origins.md、crates/cli/viewer/viewer.js 的 glyphs2/nearest），本模
块把其中的纯计算抽出来——URL 换算、hash 比对、origins 的 base64 小端数组解
码、反查取最近字形、字节偏移换行号——让传输壳
（build_system/phitex_session/viewer_socket.py）薄到只剩收发与分发，也让
CI 无显示环境能直接测协议。

要点（相对 CLI 的 `status --json`）：
* 页 hash：`{"op":"pages"}` → json.pages = ["d3ab...", ...]，16 位小写十六
  进制（u64 hash 的 {:016x}），按页序。`settled` 事件不带 hash，要收到后重
  新拉取比对。
* origins：`{"op":"origins","page":K,"files_from":N}`。该页字形映射还没做
  好时回 json={"pending":true}（watch 会去做，客户端轮询）；从来不做（页
  号越界）回 ok=false。做好时回并排的定长数组，每个是 base64 的小端字：
  file/start/end u32、synth 位域（32 字形一个字）、x/y f32。file 索引指
  files 表，0xFFFFFFFF 表示该字形没有来源（页眉页脚、自动编号这类合成
  物）。start/end 是**文件当前文本的 UTF-8 字节偏移**（viewer.js 的
  charOffset 按 UTF-8 解码——F3 条：偏移在 Build 持有的文本版本里，编辑
  后 watch 重算）。files 表只增不减：请求带 files_from 拿增量，客户端自己
  把增量拼到旧表上。
* 反查取最近字形（viewer.js nearest）：线性扫全页，代价
  dx² + 9·dy² + (synth ? 400 : 0)，dx = g.x + 2.5 − x、dy = g.y − 3 − y
  （字形原点相对点击点的经验偏移：基线在字底、点击稍偏右下）；无来源的字
  形直接跳过。origins 坐标与 diff bbox 同一系：**页左上原点、y 向下**
  （PDF pt，未旋转页），而 NeoSetzer 反查链路上的 y 是 synctex 惯例（页底
  起向上），换算在调用方：y_top = 页高 − y_bottom。

AGPL 边界：PhiTeX 只作为子进程/协议对端存在，本模块只是它的客户端解析器。
'''

import base64
import json
import os
import re
import struct

# origins 的 file 数组里"这个字形没有来源文件"的哨兵值（u32 全 1）。
NO_SOURCE_FILE = 0xFFFFFFFF

# session start / status 里 viewer URL 的形态：http://127.0.0.1:PORT/TOKEN/
# （TOKEN 是 32 位十六进制；WS 端点 = 该 URL 的 http→ws 再加 "ws"）。
_VIEWER_URL_RE = re.compile(r'http://[^\s"\'<>]+/')


def viewer_endpoint(viewer_url):
    '''viewer HTTP URL → WS 端点；认不出来返回 None。

    http://127.0.0.1:21170/aac5.../ → ws://127.0.0.1:21170/aac5.../ws
    token 全在路径前缀里，没有 header 认证；https 留作远程 watch 的余地。
    '''
    if not viewer_url or not isinstance(viewer_url, str):
        return None
    if viewer_url.startswith('http://'):
        return 'ws://' + viewer_url[len('http://'):] + 'ws'
    if viewer_url.startswith('https://'):
        return 'wss://' + viewer_url[len('https://'):] + 'ws'
    return None


def parse_viewer_url(text):
    '''从 `session start` 的输出或任意文本里抓 viewer URL；没有返回 None。

    start 的行形如 "started pid 665701 · viewer http://127.0.0.1:21170/
    <token>/ · log …"。只认 http:// 开头的绝对 URL。
    '''
    if not text:
        return None
    match = _VIEWER_URL_RE.search(text)
    return match.group(0) if match else None


def page_hashes_from_response(payload):
    '''`{"op":"pages"}` 的 json → hash 列表（按页序）；形状不对返回 None。'''
    if not isinstance(payload, dict):
        return None
    pages = payload.get('pages')
    if not isinstance(pages, list):
        return None
    if not all(isinstance(h, str) for h in pages):
        return None
    return pages


def changed_pages(old_hashes, new_hashes):
    '''两列页 hash → 内容变了的 0-based 页号集合。

    None（hash 不可用）→ None：调用方按"全页都可能变"处理（退化成现状）。
    页数变了时，多出的/消失的页也算变化。列表内元素相等才算没变。
    '''
    if old_hashes is None or new_hashes is None:
        return None
    changed = set()
    count = max(len(old_hashes), len(new_hashes))
    for index in range(count):
        old = old_hashes[index] if index < len(old_hashes) else None
        new = new_hashes[index] if index < len(new_hashes) else None
        if old != new:
            changed.add(index)
    return changed


def decode_origins(payload, files):
    '''origins op 的 json + 现有文件表 → (files, glyphs)；解不出返回 (files, None)。

    glyphs 每项 {file, start, end, synth, x, y}：file 是 files 表下标（哨兵
    值折成 None）、start/end UTF-8 字节偏移、synth 布尔、x/y f32（页左上、
    y 向下、PDF pt）。files_from 语义：payload['files'] 是从 payload
    ['files_from'] 起的增量表，就地拼接到传入的 files 上（watch 的文件表
    只增不减，索引才始终对得上）。pending/错误形态的 payload 返回
    (files, None)——pending 由传输层轮询，错误直接放弃该页。
    '''
    if not isinstance(payload, dict) or payload.get('pending'):
        return files, None
    try:
        count = int(payload['n'])
        files_from = int(payload.get('files_from', 0))
        new_files = payload['files']
        # file/start/end/synth 的字数按各自的打包宽度算：u32 数组各 n 个，
        # synth 是位域（⌈n/32⌉ 个字）。
        words = {key: base64.b64decode(payload[key])
                 for key in ('file', 'start', 'end', 'synth', 'x', 'y')}
    except (KeyError, TypeError, ValueError):
        return files, None
    if not isinstance(new_files, list):
        return files, None
    if count <= 0:
        # 空页（n=0）：合法答案就是空字形表（页上没东西可映射）。
        return files, []
    expected = {key: count * 4 for key in ('file', 'start', 'end', 'x', 'y')}
    expected['synth'] = ((count + 31) // 32) * 4
    if any(len(words[key]) < size for key, size in expected.items()):
        return files, None

    # 增量拼接（表只增不减，前缀恒定）：正常应答的 files_from 恰是当前表
    # 长；客户端/服务端对不齐时（比如连接重置后拿旧表请求）以应答为准，
    # 在 files_from 处接上新表。
    files = files[:files_from] + list(new_files)

    file_idx = struct.unpack('<{}I'.format(count), words['file'][:count * 4])
    start = struct.unpack('<{}I'.format(count), words['start'][:count * 4])
    end = struct.unpack('<{}I'.format(count), words['end'][:count * 4])
    xs = struct.unpack('<{}f'.format(count), words['x'][:count * 4])
    ys = struct.unpack('<{}f'.format(count), words['y'][:count * 4])
    synth_words = struct.unpack(
        '<{}I'.format((count + 31) // 32), words['synth'][:(count + 31) // 32 * 4])
    glyphs = []
    for index in range(count):
        glyph_file = file_idx[index]
        glyphs.append({
            'file': None if glyph_file == NO_SOURCE_FILE else glyph_file,
            'start': start[index],
            'end': end[index],
            'synth': bool((synth_words[index >> 5] >> (index & 31)) & 1),
            'x': xs[index],
            'y': ys[index],
        })
    return files, glyphs


def nearest_glyph(glyphs, x, y):
    '''页内 (x, y)（origins 坐标：左上原点 y 向下）→ 最近字形或 None。

    viewer.js nearest 的公式：dx = g.x + 2.5 − x、dy = g.y − 3 − y、代价
    dx² + 9·dy² + (synth ? 400 : 0)。9 倍权重让"同一行相邻字"的横向误差远
    轻于"隔一行"的纵向误差（行距 ~12pt 时 dy²·9 已经压过同行几 pt 的偏
    移）；合成字形（页眉、自动编号）罚 400 而不是剔除——图上只有页眉时
    点空白也要有个落点。无来源字形跳过（点了也没处可去）。
    '''
    best = None
    best_cost = float('inf')
    for glyph in glyphs or []:
        if glyph['file'] is None:
            continue
        dx = glyph['x'] + 2.5 - x
        dy = glyph['y'] - 3 - y
        cost = dx * dx + 9 * dy * dy + (400 if glyph['synth'] else 0)
        if cost < best_cost:
            best_cost = cost
            best = glyph
    return best


def origin_to_position(glyph, files, project_root, texts):
    '''字形 → 源码位置 {filename, line}（1-based 行）或 None。

    files[glyph['file']] 是相对项目根的路径（实测 'main.tex'，也可能带
    './' 前缀——viewer.js 的 path() 会去前缀补 .tex，我们对前缀同样处理，
    但不猜扩展名：watch 报什么算什么）。行号 = 文件文本前缀里 \\n 的个数
    + 1（start 是 UTF-8 字节偏移，\\n 在两种编码下同字节，计数安全）。
    texts 是 {去前缀路径: 文本} 或同签名的 callable（主文档取 buffer
    快照、子文件读盘由调用方决定，本函数不认识线程边界）；取不到文本
    返回 None。
    '''
    if glyph is None or glyph['file'] is None:
        return None
    name = files[glyph['file']] if glyph['file'] < len(files) else None
    if not name:
        return None
    name = name[2:] if name.startswith('./') else name
    filename = name if os.path.isabs(name) else os.path.join(project_root or '', name)
    text = texts(name) if callable(texts) else (texts.get(name) if isinstance(texts, dict) else None)
    if text is None:
        return None
    try:
        byte_offset = int(glyph['start'])
        prefix = text.encode('utf-8')[:byte_offset]
    except (UnicodeEncodeError, ValueError):
        return None
    line = prefix.count(b'\n') + 1
    return {'filename': os.path.normpath(filename), 'name': name, 'line': line}


def parse_where_json(stdout):
    '''`session where LABEL --json` 的 stdout → payload dict 或 None。

    实测输出（1.0.4）：{"approximate":false,"bbox":[131.05,315.95,480.2,
    362.53],"build":2,"page":1,"pdf":"/…/.phitex/png/builds/build-2.pdf",
    "printed_page":null,"target":"sec:test"}——bbox 是页左上 y 向下的 pt
    （与 compare 同一惯例）。label 不存在/没构建过时输出错误行，返回
    None 由调用方降级。
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


def where_to_rectangle(payload):
    '''where --json 的 payload → synctex 矩形惯例 {page,h,v,width,height}。

    与 phitex_compare.changes_to_rectangles 同一换算（v = y1 = 底边距页顶），
    这里单字形/单框够用。page 越界或 bbox 缺失返回 None。
    '''
    if not isinstance(payload, dict):
        return None
    bbox = payload.get('bbox')
    page = payload.get('page')
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return {'page': page, 'h': x0, 'v': y1, 'width': x1 - x0, 'height': y1 - y0}

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

'''子进程输出解码（gi-free 纯逻辑，供 builder 各处复用）。

背景：synctex / texcount 等 TeX 工具链的正常输出是 UTF-8，但在
Windows 上出错信息走系统代码页——中文 Windows（ANSI 代码页 cp936）
下含 0xD0 这类字节，硬按 UTF-8 解码抛 UnicodeDecodeError，使后台
同步/统计线程崩溃并弹出「An unexpected error occurred」对话框。

解码顺序（逐级回退，保证永不抛异常）：

1. UTF-8 —— 工具正常输出，绝大多数情况在此命中；
2. 系统 preferred encoding —— Windows 上即 ANSI 代码页（如 cp936），
   兜住本地化的错误提示文本；UTF-8 环境下与第 1 步重复，直接跳过；
3. UTF-8 + errors='replace' —— 编码探测全部失败的最终兜底。

下游用正则解析关心的是 Output:/Input:/Line: 等英文标签与数字，
均在 ASCII 区间，不受回退路径影响；唯一代价是非 UTF-8 场景下
错误文本可能部分显示为替换符。
'''

import locale


def decode_process_output(raw_bytes):
    '''将子进程 stdout/stderr 字节流解码为 str，永不抛 UnicodeDecodeError。

    传入 str 时原样返回（容错上游已解码的场景）。
    '''
    if isinstance(raw_bytes, str):
        return raw_bytes
    try:
        return raw_bytes.decode('utf-8')
    except UnicodeDecodeError:
        pass
    preferred = locale.getpreferredencoding(False)
    if preferred and preferred.lower().replace('-', '') not in ('utf8', ''):
        try:
            return raw_bytes.decode(preferred)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw_bytes.decode('utf-8', errors='replace')

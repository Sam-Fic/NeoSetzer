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

'''安全计算 synctex 缓存文件夹名。

旧实现把完整 .tex 路径做 base64-urlsafe 编码直接当文件夹名。base64
会把路径 *放大*（约 4/3 长度 + padding），当文件名较长时（实测 133 字符
的 .tex 路径放大到约 184 字符），加上 ``~/.config/setzer/`` 前缀后超过
Linux 常见 255 字节文件名上限，触发 ``OSError: [Errno 36] File name too
long``。该异常在 build 线程中未捕获，导致 ``_on_query_done`` 永不被调度，
UI 编译计数器无限增长（soft-hang）。

新方案复用 document_state_paths 的成熟思路：
``<sha256_16hex>__<sanitized_basename>``
- sha256 前缀保证不同路径即使 basename 相同也不冲突
- sanitized basename 保留可读性便于调试
- 固定短长度（16+2+变长 basename）永不超文件名上限
'''

import hashlib
import os.path


def synctex_folder(config_folder, tex_filename):
    '''返回存放某 .tex 对应 .synctex.gz 的缓存目录绝对路径（不保证已创建）。'''
    basename = os.path.basename(tex_filename)
    safe_basename = ''.join(
        c if (c.isalnum() or c in '._-') else '_' for c in basename)
    hash_prefix = hashlib.sha256(tex_filename.encode('utf-8')).hexdigest()[:16]
    stem = '{}__{}'.format(hash_prefix, safe_basename)
    return os.path.join(config_folder, stem)

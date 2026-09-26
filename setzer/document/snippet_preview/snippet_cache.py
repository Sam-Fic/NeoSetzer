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

'''snippet 预览缓存：内存 LRU + 磁盘 PDF 缓存（纯逻辑，无 gi 依赖）。

公式 hover 预览与 TikZ 侧栏预览共用同一份缓存：键是包装后的完整文档
+ 引擎 + 编译工作目录的哈希，两侧天然不会串味。

- 内存层：存放渲染完成的贴图对象（Gdk.Texture 等任意不可变对象，
  本模块不感知类型），标准 LRU 逐出，容量可注入。所有读写都发生在
  主线程（控制器经 idle 回主线程后调用），无需加锁。
- 磁盘层：按内容哈希存放编译出的单页 PDF（``<cache_dir>/<hash>.pdf``），
  命名沿用 helpers/synctex_folder.py 的 sha256_16hex 惯例。哈希键天然
  去重，同内容公式跨会话/跨文档共享缓存。
- 磁盘清理：每次写入后检查，文件数超上限时按 mtime 从旧到新删除
  （最新公式大概率还会再 hover）。
'''

import os
import shutil
from collections import OrderedDict


class SnippetCache(object):

    def __init__(self, cache_dir, max_items=128, max_disk_files=500):
        self.cache_dir = cache_dir
        self.max_items = max_items
        self.max_disk_files = max_disk_files
        self._memory = OrderedDict()  # key -> 贴图对象，尾部最近使用

    # ---------- 内存 LRU ----------

    def get(self, key):
        '''命中则移到队尾并返回值；未命中返回 None。'''
        value = self._memory.pop(key, None)
        if value is not None:
            self._memory[key] = value
        return value

    def put(self, key, value):
        self._memory[key] = value
        self._memory.move_to_end(key)
        while len(self._memory) > self.max_items:
            self._memory.popitem(last=False)

    def clear_memory(self):
        self._memory.clear()

    # ---------- 磁盘 PDF 缓存 ----------

    def pdf_path(self, key):
        return os.path.join(self.cache_dir, key + '.pdf')

    def has_pdf(self, key):
        return os.path.isfile(self.pdf_path(key))

    def store_pdf(self, src_path, key):
        '''把编译产物移入缓存目录并返回最终路径。

        编译在系统临时目录进行（常见为 tmpfs），缓存目录在用户配置目录，
        跨文件系统时 os.replace 抛 EXDEV，故用 shutil.move。覆盖同名
        旧缓存是安全的：键即内容哈希，同名即同内容。
        '''
        os.makedirs(self.cache_dir, exist_ok=True)
        target = self.pdf_path(key)
        shutil.move(src_path, target)
        self.prune_disk()
        return target

    def prune_disk(self):
        '''文件数超上限时按 mtime 从旧到新删除，直到回到上限以内。'''
        try:
            entries = [name for name in os.listdir(self.cache_dir)
                       if name.endswith('.pdf')]
        except OSError:
            return
        if len(entries) <= self.max_disk_files:
            return
        def mtime_or_zero(name):
            try:
                return os.path.getmtime(os.path.join(self.cache_dir, name))
            except OSError:
                return 0.0
        entries.sort(key=mtime_or_zero)
        for name in entries[:len(entries) - self.max_disk_files]:
            try:
                os.remove(os.path.join(self.cache_dir, name))
            except OSError:
                pass

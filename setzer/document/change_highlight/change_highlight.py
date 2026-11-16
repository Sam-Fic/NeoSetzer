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

'''增量编译后，把改动的位置在 PDF 预览里闪一下荧光。

只在 PhiTeX 常驻 session 这条路上启用（build_system.is_phitex_live_build），
其他引擎的构建节奏没有"改动位置"这个概念可对应——它们一次构建覆盖整份文档，
闪哪都一样。

数据链是：Document 记录自上次构建以来被改过的行 → `synctex view` 把行换成
PDF 上的矩形 → Preview.flash_changed_rectangles 画成会淡出的高亮。中间那段
是子进程查询（每个区间一次，约 5-10ms），必须离开主线程；而增量构建之间只隔
十几毫秒，所以还要防住线程堆积。
'''

import threading

from gi.repository import GLib

from setzer.helpers import synctex_query


class ChangeHighlight(object):
    '''每文档一个；由 BuildSystem 在构建成功后调用。'''

    # 一次高亮最多查询的改动区间数。用户长时间不构建会攒下很多段，
    # 而每段一次子进程调用；超过就只报最近的几段（丢的是最旧的改动）。
    MAX_QUERIES = 6

    def __init__(self, document):
        self.document = document
        self._lock = threading.Lock()
        self._running = False
        # 构建比查询快时，不在跑的查询结束后补一次，用当时最新的改动区间。
        self._stale = False
        self._shutdown = False

    def shutdown(self):
        '''文档关闭时调用：丢弃未处理的改动，并让在途查询不再回主线程。

        查询线程结束时用 GLib.idle_add 投递高亮，而文档可能已经关闭、preview
        已经拆掉；投递本身不会崩，但回调里碰已销毁的 widget 就是段错误级别的
        事故，所以在这里立牌子让回调自己放弃。
        '''
        self._shutdown = True
        self.document._dirty_line_ranges = []

    def highlight_after_build(self, pdf_filename, tex_filename):
        if self._shutdown or not pdf_filename or not tex_filename:
            return
        with self._lock:
            if self._running:
                self._stale = True
                return
            self._running = True
        # 区间在主线程取（buffer 只能在主线程读），查询在后台线程做。
        ranges = self.document.take_dirty_line_ranges()
        if not ranges:
            with self._lock:
                self._running = False
            return
        ranges = ranges[-self.MAX_QUERIES:]
        threading.Thread(target=self._query_and_show,
                         args=(list(ranges), pdf_filename, tex_filename),
                         daemon=True).start()

    def _query_and_show(self, ranges, pdf_filename, tex_filename):
        rectangles = []
        try:
            directory = synctex_query.synctex_directory_for(pdf_filename)
            for start_line, end_line in ranges:
                # synctex 行号 1-based，TextIter 是 0-based。
                for line in (start_line, end_line):
                    for rectangle in synctex_query.query_rectangles(
                            tex_filename, pdf_filename, line + 1, directory):
                        rectangles.append(rectangle)
                    if end_line != start_line:
                        # 只查两端：中间行同样在改动段里，但每次查询都是一个
                        # 子进程，逐行查会在长段落改动时放大成几十次调用。
                        continue
        finally:
            with self._lock:
                self._running = False
                retry = self._stale
                self._stale = False
        if rectangles:
            GLib.idle_add(self._show, rectangles)
        if retry:
            # 期间又完成了一次构建：按最新改动重查一次，否则最后一闪会丢。
            self.highlight_after_build(pdf_filename, tex_filename)

    def _show(self, rectangles):
        if self._shutdown:
            return False
        preview = getattr(self.document, 'preview', None)
        if preview is None:
            return False
        preview.flash_changed_rectangles(rectangles)
        return False

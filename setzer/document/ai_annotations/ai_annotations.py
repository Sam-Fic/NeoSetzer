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

"""每文档的 AI 批注模型（选区 → 批注 → 发送给 Agent）。

仿 bookmarks 的 Observable 模式：批注用一对 Gtk.TextMark 锚定选区
（start 左重力 / end 右重力），编辑时自动跟随；高亮用匿名 TextTag
（半透明琥珀色背景），随缓冲区编辑自动伸缩。

仅内存态：文档关闭即清除，不做磁盘持久化（本期边界）。选区被删空
（start == end）或 mark 失效（如外部重载整段替换文本）时自动移除批注。
"""

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib

from setzer.helpers.observable import Observable


# 批注高亮背景：半透明琥珀色，深浅主题下均可辨且不遮挡语法高亮。
_HIGHLIGHT_RGBA = 'rgba(245, 194, 17, 0.25)'

# update() 的"未传入"哨兵：preset=None 是合法值（自定义，无预制指令），
# 不能用 None 区分"不改"与"改成自定义"。
_UNSET = object()


class AiAnnotations(Observable):
    """Manages AI annotations (commented selections) for a single document."""

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.source_buffer = document.source_buffer

        # 批注 dict 列表（按添加顺序）：
        # {'id', 'start_mark', 'end_mark', 'comment', 'preset', 'snippet'}
        self._annotations = list()
        self._next_id = 1
        self._sweep_idle_id = None

        rgba = Gdk.RGBA()
        rgba.parse(_HIGHLIGHT_RGBA)
        # 匿名 tag：避免与 tag table 中既有命名 tag 冲突。
        self._tag = self.source_buffer.create_tag(None, background_rgba=rgba)

        # delete-range 在删除前触发，iter 随即失效；延迟到 idle 统一清扫
        # 被删空 / mark 失效的批注（一次删除可能波及多条）。
        self.source_buffer.connect('delete-range', self.on_text_delete)

    def shutdown(self):
        """Disconnect signals and drop marks/tag to prevent leaks."""
        try:
            self.source_buffer.disconnect_by_func(self.on_text_delete)
        except (TypeError, AttributeError):
            pass
        if self._sweep_idle_id is not None:
            GLib.source_remove(self._sweep_idle_id)
            self._sweep_idle_id = None
        for annotation in list(self._annotations):
            self._detach(annotation)
        self._annotations.clear()

    # ---- 查询 ----

    def get_all(self):
        '''返回批注 dict 列表（按添加顺序，调用方不应修改）。'''
        return self._annotations

    def get_by_id(self, annotation_id):
        return next((a for a in self._annotations if a['id'] == annotation_id), None)

    def get_iters(self, annotation):
        '''取批注当前 (start_iter, end_iter)；mark 已失效返回 None。'''
        start_mark = annotation['start_mark']
        end_mark = annotation['end_mark']
        if start_mark.get_deleted() or end_mark.get_deleted():
            return None
        return (self.source_buffer.get_iter_at_mark(start_mark),
                self.source_buffer.get_iter_at_mark(end_mark))

    def get_line_range(self, annotation):
        '''1-based 闭区间行号 (start_line, end_line)；失效返回 None。

        选区末尾恰在行首（含换行符的整行选择）时不把下一行计入。
        '''
        iters = self.get_iters(annotation)
        if iters is None:
            return None
        start, end = iters
        start_line = start.get_line()
        end_line = end.get_line()
        if end.get_line_offset() == 0 and end_line > start_line:
            end_line -= 1
        return (start_line + 1, end_line + 1)

    def get_text(self, annotation):
        '''批注锚定的当前原文（随编辑更新）；失效返回空串。'''
        iters = self.get_iters(annotation)
        if iters is None:
            return ''
        start, end = iters
        return self.source_buffer.get_text(start, end, False)

    # ---- 增删改 ----

    def add_from_selection(self, comment='', preset=None, focus=None):
        '''把当前选区登记为批注并高亮。无选区返回 None。

        focus: 可选 {'text', 'kind'}——PDF 派生批注单独强调用户实际选中
        部分（整行/整块范围仅作上下文）。
        '''
        bounds = self.source_buffer.get_selection_bounds()
        if not bounds:
            return None
        start, end = bounds
        snippet = self.source_buffer.get_text(start, end, False)
        # start 左重力 / end 右重力：在两端边界处输入会扩展批注范围。
        start_mark = self.source_buffer.create_mark(None, start, True)
        end_mark = self.source_buffer.create_mark(None, end, False)
        self.source_buffer.apply_tag(self._tag, start, end)
        annotation = {
            'id': self._next_id,
            'start_mark': start_mark,
            'end_mark': end_mark,
            'comment': comment,
            'preset': preset,
            'snippet': snippet,
            'focus': focus,
        }
        self._next_id += 1
        self._annotations.append(annotation)
        self.add_change_code('annotations_changed')
        return annotation

    def add_from_range(self, start_offset, end_offset, comment='', preset=None, focus=None):
        '''把指定 offset 区间登记为批注（PDF 反向映射入口：先 select_range
        再复用 add_from_selection 的 marks/高亮主体）。'''
        start = self.source_buffer.get_iter_at_offset(start_offset)
        end = self.source_buffer.get_iter_at_offset(end_offset)
        self.source_buffer.select_range(start, end)
        return self.add_from_selection(comment, preset, focus=focus)

    def remove(self, annotation_id):
        annotation = self.get_by_id(annotation_id)
        if annotation is None:
            return
        self._detach(annotation)
        self._annotations.remove(annotation)
        self.add_change_code('annotations_changed')

    def update(self, annotation_id, comment=None, preset=_UNSET):
        annotation = self.get_by_id(annotation_id)
        if annotation is None:
            return
        if comment is not None:
            annotation['comment'] = comment
        if preset is not _UNSET:
            annotation['preset'] = preset
        self.add_change_code('annotations_changed')

    def clear(self):
        for annotation in list(self._annotations):
            self._detach(annotation)
        self._annotations.clear()
        self.add_change_code('annotations_changed')

    # ---- 内部 ----

    def _detach(self, annotation):
        '''移除高亮与 marks（不动列表，调用方负责）。'''
        iters = self.get_iters(annotation)
        if iters is not None:
            start, end = iters
            self.source_buffer.remove_tag(self._tag, start, end)
        for key in ('start_mark', 'end_mark'):
            mark = annotation[key]
            if not mark.get_deleted():
                self.source_buffer.delete_mark(mark)

    def on_text_delete(self, buffer, start, end):
        if not self._annotations:
            return
        if self._sweep_idle_id is None:
            self._sweep_idle_id = GLib.idle_add(self._sweep_collapsed)

    def _sweep_collapsed(self):
        '''清除被删空（start==end）或 mark 失效的批注。'''
        self._sweep_idle_id = None
        removed = False
        for annotation in list(self._annotations):
            iters = self.get_iters(annotation)
            if iters is None or iters[0].equal(iters[1]):
                self._detach(annotation)
                self._annotations.remove(annotation)
                removed = True
        if removed:
            self.add_change_code('annotations_changed')
        return False
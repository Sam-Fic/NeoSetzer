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

'''AiAnnotations 批注模型测试（真实 GtkSource.Buffer）。

覆盖：选区登记 / 1-based 行号 / marks 随编辑跟随 / 选区删空自动清除 /
update 的 preset=None 哨兵语义 / remove 清理 marks 与高亮。
无显示环境（Gtk.init 失败）时整体跳过。
'''

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, GtkSource, GLib

gtk_ready = False
try:
    Gtk.init()
    gtk_ready = True
except Exception:
    gtk_ready = False

if gtk_ready:
    from setzer.document.ai_annotations.ai_annotations import AiAnnotations


class FakeDocument:
    def __init__(self, buffer):
        self.source_buffer = buffer


def pump():
    '''跑空主循环挂起事件（delete-range 的 idle 清扫等）。'''
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass


@unittest.skipUnless(gtk_ready, 'GTK display not available')
class AiAnnotationsTest(unittest.TestCase):

    def setUp(self):
        self.buffer = GtkSource.Buffer()
        self.buffer.set_text('one\ntwo\nthree\nfour\nfive\n')
        self.document = FakeDocument(self.buffer)
        self.model = AiAnnotations(self.document)
        self.events = []
        self.model.connect('annotations_changed',
                           lambda model: self.events.append('changed'))

    def tearDown(self):
        self.model.shutdown()

    def iter_at(self, line, offset):
        # 部分 PyGObject 版本返回 (bool, iter) 结果元组，兼容两种形态。
        result = self.buffer.get_iter_at_line_offset(line, offset)
        return result[1] if isinstance(result, tuple) else result

    def select(self, start_line, start_offset, end_line, end_offset):
        start = self.iter_at(start_line, start_offset)
        end = self.iter_at(end_line, end_offset)
        self.buffer.select_range(start, end)

    def test_add_from_selection(self):
        self.select(1, 0, 2, 5)  # "two\nthree"
        annotation = self.model.add_from_selection('fix this', 'Explain')
        self.assertIsNotNone(annotation)
        self.assertEqual(self.model.get_line_range(annotation), (2, 3))
        self.assertEqual(self.model.get_text(annotation), 'two\nthree')
        self.assertEqual(annotation['comment'], 'fix this')
        self.assertEqual(annotation['preset'], 'Explain')
        self.assertEqual(len(self.model.get_all()), 1)
        self.assertTrue(self.events)

    def test_no_selection_returns_none(self):
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.assertIsNone(self.model.add_from_selection('x'))
        self.assertEqual(self.model.get_all(), [])

    def test_whole_line_selection_excludes_next_line(self):
        # 选中第 2 行整行（含换行符）：end 落在第 3 行行首，不应计入第 3 行
        self.select(1, 0, 2, 0)
        annotation = self.model.add_from_selection()
        self.assertEqual(self.model.get_line_range(annotation), (2, 2))

    def test_marks_follow_insertion_above(self):
        self.select(2, 0, 2, 5)  # "three"
        annotation = self.model.add_from_selection()
        self.assertEqual(self.model.get_line_range(annotation), (3, 3))
        # 在批注上方插入两行 → 行号下移，文本不变
        self.buffer.insert(self.buffer.get_start_iter(), 'new\nlines\n')
        self.assertEqual(self.model.get_line_range(annotation), (5, 5))
        self.assertEqual(self.model.get_text(annotation), 'three')

    def test_typing_at_end_extends_annotation(self):
        self.select(1, 0, 1, 3)  # "two"
        annotation = self.model.add_from_selection()
        # end mark 右重力：在选区末尾输入会扩展批注
        end_iter = self.iter_at(1, 3)
        self.buffer.insert(end_iter, '!!')
        self.assertEqual(self.model.get_text(annotation), 'two!!')

    def test_deleting_selection_removes_annotation(self):
        self.select(1, 0, 2, 5)
        annotation = self.model.add_from_selection()
        start, end = self.model.get_iters(annotation)
        self.buffer.delete(start, end)
        pump()  # delete-range → idle 清扫
        self.assertEqual(self.model.get_all(), [])
        self.assertIsNone(self.model.get_by_id(annotation['id']))

    def test_partial_delete_keeps_annotation(self):
        self.select(1, 0, 1, 3)  # "two"
        annotation = self.model.add_from_selection()
        start = self.iter_at(1, 0)
        end = self.iter_at(1, 1)
        self.buffer.delete(start, end)
        pump()
        self.assertEqual(len(self.model.get_all()), 1)
        self.assertEqual(self.model.get_text(annotation), 'wo')

    def test_update_preset_none_clears_preset(self):
        self.select(0, 0, 0, 3)
        annotation = self.model.add_from_selection('c', 'Explain')
        # preset=None 是合法值（自定义），必须能清掉原 preset
        self.model.update(annotation['id'], comment='new', preset=None)
        self.assertEqual(annotation['comment'], 'new')
        self.assertIsNone(annotation['preset'])
        # 不传 preset 则保持不变
        self.model.update(annotation['id'], preset='Refactor')
        self.model.update(annotation['id'], comment='again')
        self.assertEqual(annotation['preset'], 'Refactor')

    def test_remove_cleans_marks_and_tag(self):
        self.select(1, 0, 1, 3)
        annotation = self.model.add_from_selection()
        start_mark = annotation['start_mark']
        self.model.remove(annotation['id'])
        self.assertEqual(self.model.get_all(), [])
        self.assertTrue(start_mark.get_deleted())

    def test_shutdown_clears_everything(self):
        self.select(0, 0, 0, 3)
        self.model.add_from_selection()
        self.model.shutdown()
        self.assertEqual(self.model.get_all(), [])
        # shutdown 后编辑不再触发清扫回调
        self.buffer.set_text('')
        pump()


if __name__ == '__main__':
    unittest.main()
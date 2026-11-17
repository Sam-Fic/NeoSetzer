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

'''PDF「添加 AI 批注」统一走源码侧入口的 wiring 测试。

契约：PDF 选区反查映射后，先把映射区间同步选中到源码（滚动到选区
末尾保证 popover 锚点可见），等视图滚动/布局稳定后调用源码侧已有的
ai_add_annotation（popover 锚定/选区捕获/落模型全部复用），不再维护
PDF 专用弹窗路径。直发分支（send=True）仍走统一发送控制器 send_mapped。

方法体经 AST 提取 exec（与 test_file_template_actions 同模式），
map_selection 补丁为同步回调，buffer 用真实 Gtk.TextBuffer。
'''

import ast
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from setzer.ai_fix import pdf_annotation_mapper

SRC = os.path.join(REPO, 'setzer', 'workspace', 'actions', 'actions.py')


def _load_method(name):
    tree = ast.parse(open(SRC, encoding='utf-8').read())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == 'Actions')
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {'_': lambda s: s, 'GLib': GLib}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, 'exec'), ns)
    return ns[name]


_AI_FROM_PDF = _load_method('_ai_from_pdf')
_SETTLE = _load_method('_ai_annotate_when_view_settled')


def _mapping(start=2, end=3):
    return {'filename': '/p/chapter.tex', 'start_line': start, 'end_line': end,
            'focus_text': 'x=1', 'focus_kind': 'source'}


class AiFromPdfTest(unittest.TestCase):

    def setUp(self):
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text('line1\nline2\nline3\nline4\n', -1)
        self.target = SimpleNamespace(
            source_buffer=self.buffer,
            scroll_cursor_to_center=mock.Mock())
        self.sender = mock.Mock()
        self.workspace = mock.Mock()
        self.workspace.get_active_document.return_value = self.target
        self.workspace.get_annotation_sender.return_value = self.sender
        self.preview = SimpleNamespace(
            get_selection_payload=mock.Mock(return_value={'kind': 'text'}))
        # ai_add_annotation 被调用时记录当时的选区（验证先选中再委托）。
        self.seen_bounds = []

        def record_annotation():
            bounds = self.buffer.get_selection_bounds()
            self.seen_bounds.append(
                tuple(b.get_offset() for b in bounds) if bounds else None)

        self.fake = SimpleNamespace(
            workspace=self.workspace,
            _get_preview=mock.Mock(return_value=self.preview),
            _document_for_mapping=mock.Mock(return_value=self.target),
            _show_file_template_toast=mock.Mock(),
            ai_add_annotation=mock.Mock(side_effect=record_annotation))
        self.fake._ai_from_pdf = _AI_FROM_PDF.__get__(self.fake)
        self.fake._ai_annotate_when_view_settled = _SETTLE.__get__(self.fake)

    def _run(self, send, mapping):
        with mock.patch.object(pdf_annotation_mapper, 'map_selection',
                               side_effect=lambda p, d, payload, cb: cb(mapping)):
            self.fake._ai_from_pdf(send=send)

    def test_annotate_selects_mapped_range_then_delegates(self):
        self._run(False, _mapping(2, 3))
        # line2 起点 offset 6，line3 行尾 offset 17。
        self.assertEqual(self.seen_bounds, [(6, 17)])
        self.fake.ai_add_annotation.assert_called_once()
        self.target.scroll_cursor_to_center.assert_called_once()
        bounds = self.buffer.get_selection_bounds()
        self.assertEqual(tuple(b.get_offset() for b in bounds), (6, 17))
        self.sender.send_mapped.assert_not_called()

    def test_annotate_single_line_range(self):
        self._run(False, _mapping(1, 1))
        self.assertEqual(self.seen_bounds, [(0, 5)])

    def test_annotate_activates_target_when_not_active(self):
        other = SimpleNamespace()
        self.workspace.get_active_document.return_value = other
        self._run(False, _mapping())
        self.workspace.set_active_document.assert_called_once_with(self.target)
        self.fake.ai_add_annotation.assert_called_once()

    def test_annotate_no_reactivation_when_already_active(self):
        self._run(False, _mapping())
        self.workspace.set_active_document.assert_not_called()

    def test_send_branch_uses_sender_without_source_selection(self):
        mapping = _mapping()
        self._run(True, mapping)
        self.sender.send_mapped.assert_called_once_with(self.target, mapping)
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(self.buffer.get_selection_bounds(), ())

    def test_mapping_none_shows_toast(self):
        self._run(False, None)
        self.fake._show_file_template_toast.assert_called_once()
        self.fake.ai_add_annotation.assert_not_called()

    def test_out_of_range_lines_show_toast(self):
        self._run(False, _mapping(99, 100))
        self.fake._show_file_template_toast.assert_called_once()
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(self.buffer.get_selection_bounds(), ())

    def test_no_payload_shows_toast_without_mapping(self):
        self.preview.get_selection_payload.return_value = None
        with mock.patch.object(pdf_annotation_mapper, 'map_selection') as ms:
            self.fake._ai_from_pdf(send=False)
        ms.assert_not_called()
        self.fake._show_file_template_toast.assert_called_once()


class ViewSettleTest(unittest.TestCase):
    '''_ai_annotate_when_view_settled：视图未分配/滚动未稳定时推迟弹窗。'''

    def setUp(self):
        self.target = SimpleNamespace(
            view=SimpleNamespace(
                source_view=mock.Mock(),
                scrolled_window=mock.Mock()))
        self.adjustment = self.target.view.scrolled_window.get_vadjustment.return_value
        self.fake = SimpleNamespace(ai_add_annotation=mock.Mock())
        self.fake._ai_annotate_when_view_settled = _SETTLE.__get__(self.fake)
        self.idle_calls = []

    def _patch_idle(self):
        # 捕获低优先级 idle 排程（不真正进主循环），返回补丁器。
        def capture(fn, *args, **kwargs):
            self.idle_calls.append((fn, args, kwargs))
            return len(self.idle_calls)
        return mock.patch('gi.repository.GLib.idle_add', side_effect=capture)

    def _settle(self, attempt=0, deadline=None):
        return self.fake._ai_annotate_when_view_settled(
            self.target, attempt, deadline)

    def test_unallocated_view_defers_popup(self):
        self.target.view.source_view.get_allocated_height.return_value = 0
        self.adjustment.get_value.return_value = 100.0
        with self._patch_idle():
            self._settle()
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(len(self.idle_calls), 1)
        self.assertEqual(self.idle_calls[0][2].get('priority'),
                         GLib.PRIORITY_LOW)

    def test_moving_scroll_defers_popup(self):
        self.target.view.source_view.get_allocated_height.return_value = 500
        self.adjustment.get_value.side_effect = [100.0, 200.0]
        with self._patch_idle():
            self._settle()          # 首帧：记录基准，无 prev 必推迟
            self._settle(attempt=1)  # 次帧：值变化 → 继续等
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(len(self.idle_calls), 2)

    def test_stable_view_pops_after_two_frames(self):
        self.target.view.source_view.get_allocated_height.return_value = 500
        self.adjustment.get_value.return_value = 100.0
        with self._patch_idle():
            self._settle()          # 首帧记录基准
            self._settle(attempt=1)  # 次帧值不变 → 弹出
        self.fake.ai_add_annotation.assert_called_once()
        self.assertEqual(len(self.idle_calls), 1)

    def test_deadline_fallback_pops(self):
        self.target.view.source_view.get_allocated_height.return_value = 0
        self.adjustment.get_value.return_value = 100.0
        with self._patch_idle():
            self._settle(deadline=0.0)  # monotonic() >= 0 恒真 → 兜底弹出
        self.fake.ai_add_annotation.assert_called_once()
        self.assertEqual(self.idle_calls, [])

    def test_missing_view_pops_immediately(self):
        # 无 view 信息（桩文档）：value=None 视为稳定，不阻塞弹窗。
        target = SimpleNamespace()
        with self._patch_idle():
            self.fake._ai_annotate_when_view_settled(target)
        self.fake.ai_add_annotation.assert_called_once()
        self.assertEqual(self.idle_calls, [])


if __name__ == '__main__':
    unittest.main()
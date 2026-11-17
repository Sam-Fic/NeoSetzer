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
import inspect
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

from setzer.ai_fix import pdf_annotation_mapper

SRC = os.path.join(REPO, 'setzer', 'workspace', 'actions', 'actions.py')


def _load_method(name):
    tree = ast.parse(open(SRC, encoding='utf-8').read())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == 'Actions')
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    # is_ai_agent_enabled 是 actions.py 的模块级 import，AST 提取方法体时
    # 不会带过来：这里按「AI agent 已启用」喂桩，专注测映射/选区接线。
    ns = {'_': lambda s: s, 'GLib': GLib, 'os': os, 'Gtk': Gtk, 'Gdk': Gdk,
          'is_ai_agent_enabled': lambda settings: True}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), SRC, 'exec'), ns)
    return ns[name]


_AI_FROM_PDF = _load_method('_ai_from_pdf')
_SETTLE = _load_method('_ai_annotate_when_view_settled')
_DOC_FOR_MAPPING = _load_method('_document_for_mapping')
_ANCHOR_RECT = _load_method('_annotation_anchor_rect')
_REANCHOR = _load_method('_ai_reanchor_annotation_popover')


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
        # set_active_document 同步反映到 get_active_document（settle 的
        # 活动文档守卫依赖一致状态）。
        self.workspace.set_active_document.side_effect = (
            lambda d: setattr(self.workspace.get_active_document, 'return_value', d))
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
            settings=SimpleNamespace(),
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

    def _setup_cross_file(self):
        # 多 TeX 文件：活动文档是 main.tex，映射落被 include 的子文件。
        child_buffer = Gtk.TextBuffer()
        child_buffer.set_text('c1\nc2\nc3\n', -1)
        child = SimpleNamespace(source_buffer=child_buffer,
                                scroll_cursor_to_center=mock.Mock(),
                                get_filename=lambda: '/p/chapter.tex')
        self.target.get_filename = lambda: '/p/main.tex'
        self.workspace.open_document_by_filename.return_value = child
        self.fake._document_for_mapping = _DOC_FOR_MAPPING.__get__(self.fake)
        return child, child_buffer

    def test_annotate_cross_file_selects_in_child_document(self):
        child, child_buffer = self._setup_cross_file()
        with mock.patch('os.path.isfile', return_value=True):
            self._run(False, _mapping(2, 2))
        self.workspace.open_document_by_filename.assert_called_once_with('/p/chapter.tex')
        self.workspace.set_active_document.assert_called_once_with(child)
        bounds = child_buffer.get_selection_bounds()
        self.assertEqual(tuple(b.get_offset() for b in bounds), (3, 5))
        # 主文件 buffer 不受影响；滚动在子文件视图上。
        self.assertEqual(self.buffer.get_selection_bounds(), ())
        child.scroll_cursor_to_center.assert_called_once()
        self.fake.ai_add_annotation.assert_called_once()

    def test_send_cross_file_uses_child_document(self):
        child, _buffer = self._setup_cross_file()
        mapping = _mapping(2, 3)
        with mock.patch('os.path.isfile', return_value=True):
            self._run(True, mapping)
        self.sender.send_mapped.assert_called_once_with(child, mapping)
        self.fake.ai_add_annotation.assert_not_called()


class DocumentForMappingTest(unittest.TestCase):
    '''多 TeX 文件：同文件直接用；include 子文件经 workspace 打开。'''

    def setUp(self):
        self.document = SimpleNamespace(get_filename=lambda: '/p/main.tex')
        self.workspace = mock.Mock()
        self.fake = SimpleNamespace(workspace=self.workspace)
        self.fake._document_for_mapping = _DOC_FOR_MAPPING.__get__(self.fake)

    def test_same_file_returns_document(self):
        result = self.fake._document_for_mapping(
            self.document, {'filename': '/p/main.tex'})
        self.assertIs(result, self.document)
        self.workspace.open_document_by_filename.assert_not_called()

    def test_path_normalization_matches(self):
        result = self.fake._document_for_mapping(
            self.document, {'filename': '/p/./main.tex'})
        self.assertIs(result, self.document)

    def test_child_file_opened_via_workspace(self):
        child = SimpleNamespace()
        self.workspace.open_document_by_filename.return_value = child
        with mock.patch('os.path.isfile', return_value=True):
            result = self.fake._document_for_mapping(
                self.document, {'filename': '/p/chapter.tex'})
        self.assertIs(result, child)
        self.workspace.open_document_by_filename.assert_called_once_with('/p/chapter.tex')

    def test_missing_file_returns_none(self):
        with mock.patch('os.path.isfile', return_value=False):
            result = self.fake._document_for_mapping(
                self.document, {'filename': '/p/gone.tex'})
        self.assertIsNone(result)

    def test_no_filename_returns_none(self):
        self.assertIsNone(self.fake._document_for_mapping(self.document, {}))


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
        # 基准值随链传参：续帧从上一次 idle 排程里取 _state。
        state = self.idle_calls[-1][1][3] if self.idle_calls else None
        return self.fake._ai_annotate_when_view_settled(
            self.target, attempt, deadline, state)

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

    def test_growing_allocation_defers_until_stable(self):
        # 新激活视图先拿小分配再长到最终尺寸：分配尺寸变化期间
        # 滚动会被再夹紧，不得提前弹。
        # 每帧调两次（allocated 判定 + alloc 记录）：100→500→500。
        self.target.view.source_view.get_allocated_height.side_effect = [
            100, 100, 500, 500, 500, 500]
        self.adjustment.get_value.return_value = 100.0
        with self._patch_idle():
            self._settle()          # 记录基准
            self._settle(attempt=1)  # 分配 100→500 变化 → 推迟
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(len(self.idle_calls), 2)
        with self._patch_idle():
            self._settle(attempt=2)  # 分配稳定 → 弹出
        self.fake.ai_add_annotation.assert_called_once()

    def test_missing_view_pops_immediately(self):
        # 无 view 信息（桩文档）：value=None 视为稳定，不阻塞弹窗。
        target = SimpleNamespace()
        with self._patch_idle():
            self.fake._ai_annotate_when_view_settled(target)
        self.fake.ai_add_annotation.assert_called_once()
        self.assertEqual(self.idle_calls, [])

    def test_switched_active_document_aborts(self):
        # 等待期间用户切换/关闭了目标文档：静默放弃，不得把批注
        # 落到别的文档的选区上。
        self.fake.workspace = mock.Mock()
        self.fake.workspace.get_active_document.return_value = SimpleNamespace()
        with self._patch_idle():
            self._settle()
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(self.idle_calls, [])

    def test_parallel_chains_do_not_share_baseline(self):
        # 连续两次批注的等待链并行：基准值随链传参，不互相污染。
        def mk_target(value):
            adj = mock.Mock()
            adj.get_value.return_value = value
            sv = mock.Mock()
            sv.get_allocated_height.return_value = 500
            return SimpleNamespace(view=SimpleNamespace(
                source_view=sv,
                scrolled_window=mock.Mock(
                    get_vadjustment=mock.Mock(return_value=adj))))
        t1, t2 = mk_target(100.0), mk_target(200.0)
        with self._patch_idle():
            self.fake._ai_annotate_when_view_settled(t1)             # A 首帧
            state_a = self.idle_calls[-1][1][3]
            self.fake._ai_annotate_when_view_settled(t2)             # B 首帧
            self.fake._ai_annotate_when_view_settled(t1, 1, None, state_a)  # A 次帧→弹
        self.fake.ai_add_annotation.assert_called_once()


class ScrollArrivalTest(unittest.TestCase):
    '''回归：滚动值假稳定（scroll_to_mark 尚未生效）不得提前弹窗。

    新激活文档的滚动被 GTK 推迟到布局后，此前滚动值恒为旧值；
    必须等选区末尾真正落入可见区（滚动完成）才弹 popover。
    '''

    def setUp(self):
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text('\n'.join('line%d' % i for i in range(40)), -1)
        # 选中 line10（offset 60..66）：模拟跳转目标在视口下方。
        self.buffer.select_range(self.buffer.get_iter_at_offset(60),
                                 self.buffer.get_iter_at_offset(66))
        self.source_view = mock.Mock()
        self.source_view.get_allocated_height.return_value = 500
        self.adjustment = mock.Mock()
        self.adjustment.get_value.return_value = 0.0
        self.target = SimpleNamespace(
            source_buffer=self.buffer,
            view=SimpleNamespace(
                source_view=self.source_view,
                scrolled_window=mock.Mock(
                    get_vadjustment=mock.Mock(return_value=self.adjustment))))
        self.fake = SimpleNamespace(ai_add_annotation=mock.Mock())
        self.fake._ai_annotate_when_view_settled = _SETTLE.__get__(self.fake)
        self.idle_calls = []

    def _patch_idle(self):
        def capture(fn, *args, **kwargs):
            self.idle_calls.append((fn, args, kwargs))
            return len(self.idle_calls)
        return mock.patch('gi.repository.GLib.idle_add', side_effect=capture)

    def _set_visible(self, y, height=500):
        rect = Gdk.Rectangle()
        rect.y, rect.height = y, height
        self.source_view.get_visible_rect.return_value = rect

    def _set_end_loc(self, y, height=20):
        rect = Gdk.Rectangle()
        rect.y, rect.height = y, height
        self.source_view.get_iter_location.return_value = rect

    def _frame(self, attempt=0):
        # 基准值随链传参：续帧从上一次 idle 排程里取 _state。
        state = self.idle_calls[-1][1][3] if self.idle_calls else None
        return self.fake._ai_annotate_when_view_settled(
            self.target, attempt, None, state)

    def test_stable_value_but_target_not_in_view_defers(self):
        # 滚动值连续不变但选区末尾仍在视口下方（滚动尚未开始）：
        # 不得误判稳定；滚入可见区后次帧才弹。
        self._set_visible(0)
        self._set_end_loc(600)
        with self._patch_idle():
            self._frame()
            self._frame(attempt=1)
        self.fake.ai_add_annotation.assert_not_called()
        self.assertEqual(len(self.idle_calls), 2)
        # 滚动完成：选区末尾入可见区，值仍稳定 → 弹出。
        self._set_visible(120)
        with self._patch_idle():
            self._frame(attempt=2)
        self.fake.ai_add_annotation.assert_called_once()

    def test_never_in_view_pops_at_deadline(self):
        self._set_visible(0)
        self._set_end_loc(5000)
        with self._patch_idle():
            self.fake._ai_annotate_when_view_settled(self.target, deadline=0.0)
        self.fake.ai_add_annotation.assert_called_once()
        self.assertEqual(self.idle_calls, [])

    def test_deadline_fallback_rescrolls_before_pop(self):
        # 兜底弹出前补一次居中滚动（尽力到位，重锚再修正残余）。
        self.target.scroll_cursor_to_center = mock.Mock()
        self._set_visible(0)
        self._set_end_loc(5000)
        with self._patch_idle():
            self.fake._ai_annotate_when_view_settled(self.target, deadline=0.0)
        self.target.scroll_cursor_to_center.assert_called_once()
        self.fake.ai_add_annotation.assert_called_once()

    def test_clamped_at_document_top_accepted_with_tolerance(self):
        # 文档首部 GTK 夹紧无法居中：选区末尾在可见区顶上方一行高
        # 容差内仍视为到位。
        self._set_visible(0)
        self._set_end_loc(-15)
        with self._patch_idle():
            self._frame()
            self._frame(attempt=1)
        self.fake.ai_add_annotation.assert_called_once()

    def test_no_selection_skips_arrival_gate(self):
        self.buffer.select_range(self.buffer.get_iter_at_offset(0),
                                 self.buffer.get_iter_at_offset(0))
        self._set_visible(0)
        self._set_end_loc(5000)
        with self._patch_idle():
            self._frame()
            self._frame(attempt=1)
        self.fake.ai_add_annotation.assert_called_once()


class ReanchorTest(unittest.TestCase):
    '''弹出后逐帧重锚：修正跨文件跳转残余布局位移导致的弹窗错位。'''

    def setUp(self):
        self.buffer = Gtk.TextBuffer()
        self.buffer.set_text('line1\nline2\n', -1)
        self.source_view = mock.Mock()
        loc = Gdk.Rectangle()
        loc.x, loc.y, loc.width, loc.height = 10, 40, 60, 20
        self.source_view.get_iter_location.return_value = loc
        self.source_view.buffer_to_widget_coords.return_value = (10, 60)
        self.frame_clock = mock.Mock()
        self.source_view.get_frame_clock.return_value = self.frame_clock
        self.document = SimpleNamespace(
            source_buffer=self.buffer,
            view=SimpleNamespace(source_view=self.source_view))
        self.popover = mock.Mock()
        self.popover.get_visible.return_value = True
        self.workspace = mock.Mock()
        self.workspace.get_active_document.return_value = self.document
        self.fake = SimpleNamespace(workspace=self.workspace)
        self.fake._annotation_anchor_rect = _ANCHOR_RECT.__get__(self.fake)
        self.fake._ai_reanchor_annotation_popover = _REANCHOR.__get__(self.fake)

    def _reanchor(self, ticks=0):
        self.fake._ai_reanchor_annotation_popover(self.document, self.popover,
                                                  5, ticks)

    def _tick_handlers(self):
        return [c.args[0] for c in
                self.source_view.add_tick_callback.call_args_list]

    def test_first_call_reanchors_and_schedules_next_frame(self):
        # 首帧不查 visible（popup 的 map 异步）；重算锚点并挂下一帧。
        self._reanchor()
        self.popover.set_pointing_to.assert_called_once()
        rect = self.popover.set_pointing_to.call_args.args[0]
        self.assertEqual((rect.x, rect.y), (10, 60))
        self.assertEqual(len(self._tick_handlers()), 1)

    def test_tick_recomputes_shifted_anchor(self):
        self._reanchor()
        # 残余位移：坐标换算变化 → 下一帧 pointing 跟随修正。
        self.source_view.buffer_to_widget_coords.return_value = (10, 200)
        # tick 回调签名 (widget, frame_clock)，返回 False 即自动注销 ——
        # 曾误用 frame_clock.connect('after-paint') 并多带一个 data
        # 形参，运行期抛 TypeError 弹「unexpected error」对话框。
        self.assertEqual(self._tick_handlers()[0](self.source_view,
                                                 self.frame_clock), False)
        rect = self.popover.set_pointing_to.call_args.args[0]
        self.assertEqual((rect.x, rect.y), (10, 200))
        self.assertEqual(len(self._tick_handlers()), 2)

    def test_tick_callback_matches_gtk_tick_signature(self):
        # Gtk.Widget.add_tick_callback 的回调固定收 (widget, frame_clock)：
        # 参数个数不符会在主循环里抛 TypeError 而不是在这里。
        self._reanchor()
        handler = self._tick_handlers()[0]
        params = list(inspect.signature(handler).parameters.values())
        self.assertEqual([p.name for p in params], ['widget', 'frame_clock'])

    def test_closed_popover_stops(self):
        self.popover.get_visible.return_value = False
        self._reanchor(ticks=1)
        self.popover.set_pointing_to.assert_not_called()
        self.source_view.add_tick_callback.assert_not_called()

    def test_switched_document_stops(self):
        self.workspace.get_active_document.return_value = SimpleNamespace()
        self._reanchor(ticks=1)
        self.popover.set_pointing_to.assert_not_called()

    def test_tick_cap_stops(self):
        self._reanchor(ticks=4)
        self.popover.set_pointing_to.assert_not_called()
        self.source_view.add_tick_callback.assert_not_called()

    def test_superseded_by_new_popup_stops(self):
        # 连续批注重用同一 popover：旧重锚链（seq 落后）不得用旧
        # end_offset 覆盖新弹窗的 pointing。
        self.fake._ai_popover_seq = 2
        self._reanchor(ticks=1)   # seq 默认 0 ≠ 2
        self.popover.set_pointing_to.assert_not_called()
        self.source_view.add_tick_callback.assert_not_called()

    def test_anchor_rect_fallback_api(self):
        # 旧 PyGObject 命名：buffer_to_window_coords 兜底。
        del self.source_view.buffer_to_widget_coords
        self.source_view.buffer_to_window_coords.return_value = (10, 60)
        rect = self.fake._annotation_anchor_rect(self.document, 5)
        self.assertEqual((rect.x, rect.y, rect.width, rect.height), (10, 60, 60, 20))
        self.source_view.buffer_to_window_coords.assert_called_once_with(
            Gtk.TextWindowType.WIDGET, 10, 60)


if __name__ == '__main__':
    unittest.main()
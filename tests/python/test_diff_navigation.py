#!/usr/bin/env python3
# coding: utf-8
#
# diff 改动导航（上一处 / 下一处）的核心状态与接线。
#
# 三条约定：
#   - 下标是 diff_rectangles 的下标（0-based），与矩形里的 n 分开——点击
#     高亮时按 n 反查下标同步，两套编号不混用；
#   - 两端不循环：审阅有终点，最后一处再点"下一处"跳回第一处会分不清
#     "看完了"还是"又开始了"；
#   - 进入 diff 视图时布局可能还没建好（diff PDF 页尺寸与正式 PDF 不同会
#     作废 layout）：这次定位挂起，等布局建好补跳，不能静默丢失。
#
# 方法用 ast 抽取到假对象上测（同 test_preview_no_flash_on_rebuild 范式），
# 不构造真 GTK 控件。

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = 'setzer/document/preview/preview.py'
CONTROLLER = 'setzer/document/preview/preview_controller.py'

METHODS = ['jump_to_diff_change', 'step_diff_change',
           'sync_diff_change_from_click', '_apply_pending_diff_jump']


def load_methods(relative_path, class_name, method_names):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    namespace = {}
    found = {}
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in method_names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'),
                 namespace)
            found[node.name] = namespace[node.name]
    missing = set(method_names) - set(found)
    if missing:
        raise AssertionError('missing methods: {}'.format(sorted(missing)))
    return found


def rect(n, page=1):
    return {'page': page, 'h': 10, 'v': 20, 'width': 30, 'height': 5,
            'kind': 'add', 'n': n}


class FakePreview:
    def __init__(self, rects=None, layout=object()):
        self.diff_rectangles = list(rects or [])
        self.diff_change_index = None
        self._pending_diff_jump = False
        self.layout = layout
        self.codes = []
        self.scrolled = []

    def add_change_code(self, code):
        self.codes.append(code)

    def _scroll_to_rectangle(self, rectangle):
        if self.layout is None:
            return False
        self.scrolled.append(rectangle)
        return True


for _name, _fn in load_methods(PREVIEW, 'Preview', METHODS).items():
    setattr(FakePreview, _name, _fn)


class JumpTest(unittest.TestCase):

    def test_jump_clamps_and_scrolls(self):
        preview = FakePreview([rect(1), rect(2, page=2)])
        self.assertTrue(preview.jump_to_diff_change(5))
        self.assertEqual(preview.diff_change_index, 1)
        self.assertEqual(preview.scrolled[-1]['n'], 2)
        self.assertTrue(preview.jump_to_diff_change(-3))
        self.assertEqual(preview.diff_change_index, 0)

    def test_jump_without_rectangles_is_noop(self):
        preview = FakePreview([])
        self.assertFalse(preview.jump_to_diff_change(0))
        self.assertIsNone(preview.diff_change_index)

    def test_jump_emits_position_change_only_on_move(self):
        preview = FakePreview([rect(1), rect(2)])
        preview.jump_to_diff_change(0)
        preview.jump_to_diff_change(0)
        self.assertEqual(preview.codes.count('diff_position_changed'), 1)

    def test_missing_layout_defers_jump(self):
        # 布局未建好：定位挂起，等 setup_layout_and_zoom_levels 补跳。
        preview = FakePreview([rect(1)], layout=None)
        self.assertFalse(preview.jump_to_diff_change(0))
        self.assertTrue(preview._pending_diff_jump)
        preview.layout = object()
        preview._apply_pending_diff_jump()
        self.assertFalse(preview._pending_diff_jump)
        self.assertEqual(preview.scrolled[-1]['n'], 1)


class StepTest(unittest.TestCase):

    def test_step_does_not_wrap(self):
        preview = FakePreview([rect(1), rect(2)])
        preview.jump_to_diff_change(1)
        preview.step_diff_change(1)
        self.assertEqual(preview.diff_change_index, 1)
        preview.jump_to_diff_change(0)
        preview.step_diff_change(-1)
        self.assertEqual(preview.diff_change_index, 0)

    def test_step_from_unset_index_starts_at_first(self):
        preview = FakePreview([rect(1), rect(2)])
        preview.step_diff_change(1)
        self.assertEqual(preview.diff_change_index, 1)


class ClickSyncTest(unittest.TestCase):

    def test_click_sync_by_change_n(self):
        preview = FakePreview([rect(1), rect(5), rect(9)])
        self.assertEqual(preview.sync_diff_change_from_click(5), 1)
        self.assertEqual(preview.diff_change_index, 1)
        # 点击同步不滚动：用户已经在那儿。
        self.assertEqual(preview.scrolled, [])

    def test_click_sync_unknown_n_keeps_state(self):
        preview = FakePreview([rect(1)])
        self.assertIsNone(preview.sync_diff_change_from_click(42))
        self.assertIsNone(preview.diff_change_index)


class WiringGuardTest(unittest.TestCase):
    '''show_diff_pdf 落第一处 / 结束清下标 / 控制器点击先同步。'''

    def test_show_diff_pdf_lands_on_first_change(self):
        source = (ROOT / PREVIEW).read_text(encoding='utf-8')
        start = source.index('def show_diff_pdf')
        end = source.index('\n    def ', start + 1)
        block = source[start:end]
        self.assertIn('self.diff_change_index', block)
        self.assertIn('_scroll_to_rectangle', block)
        self.assertIn('_pending_diff_jump', block)

    def test_end_review_clears_navigation(self):
        source = (ROOT / PREVIEW).read_text(encoding='utf-8')
        start = source.index('def end_diff_review')
        end = source.index('\n    def ', start + 1)
        self.assertIn('self.diff_change_index = None', source[start:end])

    def test_controller_syncs_before_opening_dialog(self):
        source = (ROOT / CONTROLLER).read_text(encoding='utf-8')
        sync = source.index('sync_diff_change_from_click')
        dialog = source.index('_show_diff_change_dialog(change)', sync)
        self.assertLess(sync, dialog)


if __name__ == '__main__':
    unittest.main()

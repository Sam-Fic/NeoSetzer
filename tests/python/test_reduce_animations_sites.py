#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''「减弱动画」在三个逐帧动画消费点的落地（无界面 AST 测试）。

这三处都不是 Gtk.Stack/Revealer 那种「设个 transition 就好」的过渡，而是自己
驱动的逐帧循环，必须各自判断偏好后直接进末态：
  - helpers/scroll_animator.ScrollAnimatorMixin.scroll_view：侧栏列表滚动；
  - document.Document._start_highlight_fade：反向 SyncTeX 的编辑器高亮淡出；
  - preview/preview_presenter.PreviewPresenter.start_fade_loop：正向 SyncTeX
    的 PDF 高亮淡出。

被测方法用 AST 从类里摘出来单独 exec（项目既有约定），避免 import gi 与
构造真实控件。
'''

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]

SCROLL_ANIMATOR = 'setzer/helpers/scroll_animator.py'
DOCUMENT = 'setzer/document/document.py'
PREVIEW_PRESENTER = 'setzer/document/preview/preview_presenter.py'


def load_methods(relative_path, class_name, names, namespace):
    '''从类里摘出指定方法，exec 进给定的命名空间后返回该命名空间。'''
    source = (ROOT / relative_path).read_text(encoding='utf-8')
    tree = ast.parse(source)
    cls = next(node for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    wanted = [node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name in names]
    found = {node.name for node in wanted}
    missing = set(names) - found
    assert not missing, 'missing methods in {}: {}'.format(relative_path, missing)
    module = ast.Module(body=wanted, type_ignores=[])
    exec(compile(module, relative_path, 'exec'), namespace)
    return namespace


class FakePrefs:
    '''animation_prefs 替身：测试直接翻转 reduced 标志。'''

    def __init__(self, reduced=False):
        self.reduced = reduced

    def is_reduced(self):
        return self.reduced


class TestScrollAnimatorJumps(unittest.TestCase):
    '''侧栏（Document Structure / Symbols）点击目录后的列表滚动。'''

    def make(self, reduced):
        timeouts = []
        removals = []
        namespace = {
            'time': SimpleNamespace(time=lambda: 0.0),
            'GObject': SimpleNamespace(
                source_remove=removals.append,
                timeout_add=lambda interval, cb: timeouts.append((interval, cb)) or 1),
            'animation_prefs': FakePrefs(reduced),
        }
        load_methods(SCROLL_ANIMATOR, 'ScrollAnimatorMixin',
                     {'scroll_view', 'do_scroll', 'ease'}, namespace)

        class Animator:
            pass

        for name in ('scroll_view', 'do_scroll', 'ease'):
            setattr(Animator, name, namespace[name])

        animator = Animator()
        animator._scroll_timeout_id = None
        animator._adjustment_value = 0.0
        adjustment = SimpleNamespace(
            get_value=lambda: animator._adjustment_value,
            set_value=lambda v: setattr(animator, '_adjustment_value', v))
        scrolled_window = Mock()
        scrolled_window.get_vadjustment.return_value = adjustment
        animator._get_scrolled_window = lambda: scrolled_window
        return animator, scrolled_window, timeouts, removals

    def test_normal_mode_keeps_the_requested_duration(self):
        animator, _window, timeouts, _removals = self.make(reduced=False)
        animator.scroll_view(400, duration=0.2)
        self.assertEqual(animator.scroll_to['duration'], 0.2)
        self.assertEqual(timeouts[0][0], 15)

    def test_reduced_mode_lands_on_target_in_one_tick(self):
        animator, window, timeouts, _removals = self.make(reduced=True)
        animator.scroll_view(400, duration=0.2)
        self.assertEqual(animator.scroll_to['duration'], 0)
        self.assertEqual(timeouts[0][0], 15)
        # 第一个 tick 直接落到目标并归还 kinetic scrolling，不再有第二帧。
        self.assertFalse(animator.do_scroll())
        self.assertEqual(animator._adjustment_value, 400)
        window.set_kinetic_scrolling.assert_called_with(True)
        self.assertIsNone(animator.scroll_to)
        self.assertIsNone(animator._scroll_timeout_id)

    def test_reduced_mode_still_cancels_a_running_animation(self):
        '''连续点击时旧动画必须先被摘掉，否则两个循环一起写 adjustment。'''
        animator, _window, _timeouts, removals = self.make(reduced=True)
        animator._scroll_timeout_id = 55
        animator.scroll_view(900)
        self.assertEqual(removals, [55])
        self.assertEqual(animator.scroll_to['position_end'], 900)


class FakeTag:
    def __init__(self, name):
        self.name = name
        self.removed_from = []


class FakeBuffer:
    def __init__(self, tags):
        self._tags = list(tags)
        self._table = SimpleNamespace(remove=self._table_remove)
        self.removed = []

    def _table_remove(self, tag):
        self._tags.remove(tag)

    def get_tag_table(self):
        return self._table

    def get_start_iter(self):
        return 'start'

    def get_end_iter(self):
        return 'end'

    def remove_tag(self, tag, start, end):
        self.removed.append((tag.name, start, end))

    @property
    def remaining_tags(self):
        return list(self._tags)


class TestDocumentHighlightFade(unittest.TestCase):
    '''反向 SyncTeX：编辑器里 1.5s 后的 0.25s 逐帧淡出。'''

    def make(self, reduced):
        timeouts = []
        namespace = {
            'GObject': SimpleNamespace(
                timeout_add=lambda interval, cb: timeouts.append((interval, cb)) or 7),
            'animation_prefs': FakePrefs(reduced),
        }
        load_methods(DOCUMENT, 'Document',
                     {'_start_highlight_fade', '_clear_highlight_tags'}, namespace)

        class Doc:
            pass

        Doc._start_highlight_fade = namespace['_start_highlight_fade']
        Doc._clear_highlight_tags = namespace['_clear_highlight_tags']

        doc = Doc()
        doc.remove_or_color_highlight_tags = Mock()
        tag_a, tag_b = FakeTag('highlight-1'), FakeTag('highlight-2')
        doc.source_buffer = FakeBuffer([tag_a, tag_b])
        doc.highlight_tags = {1: {'tag': tag_a, 'time': 0.0},
                              2: {'tag': tag_b, 'time': 0.0}}
        doc._highlight_timeout_id = 1234
        return doc, timeouts

    def test_normal_mode_schedules_the_fade_loop(self):
        doc, timeouts = self.make(reduced=False)
        self.assertFalse(doc._start_highlight_fade())
        self.assertEqual(timeouts, [(33, doc.remove_or_color_highlight_tags)])
        self.assertEqual(doc._highlight_timeout_id, 7)
        self.assertEqual(len(doc.highlight_tags), 2)

    def test_reduced_mode_clears_tags_without_a_loop(self):
        doc, timeouts = self.make(reduced=True)
        self.assertFalse(doc._start_highlight_fade())
        self.assertEqual(timeouts, [])
        self.assertIsNone(doc._highlight_timeout_id)
        self.assertEqual(doc.highlight_tags, {})
        self.assertEqual(doc.source_buffer.remaining_tags, [])
        self.assertEqual([name for name, _s, _e in doc.source_buffer.removed],
                         ['highlight-1', 'highlight-2'])

    def test_reduced_mode_with_no_tags_does_not_touch_the_buffer(self):
        doc, timeouts = self.make(reduced=True)
        doc.highlight_tags = {}
        self.assertFalse(doc._start_highlight_fade())
        self.assertEqual(timeouts, [])
        self.assertIsNone(doc._highlight_timeout_id)
        self.assertEqual(doc.source_buffer.removed, [])

    def test_clear_highlight_tags_is_idempotent(self):
        doc, _timeouts = self.make(reduced=True)
        doc._clear_highlight_tags()
        calls = len(doc.source_buffer.removed)
        doc._clear_highlight_tags()
        self.assertEqual(len(doc.source_buffer.removed), calls)


class TestPreviewFadeLoop(unittest.TestCase):
    '''正向 SyncTeX：PDF 高亮的逐帧淡出。'''

    def make(self, reduced):
        timeouts = []
        namespace = {
            'GLib': SimpleNamespace(
                timeout_add=lambda interval, cb: timeouts.append((interval, cb)) or 42,
                Source=SimpleNamespace(remove=lambda tid: timeouts.append(('removed', tid)))),
            'animation_prefs': FakePrefs(reduced),
        }
        load_methods(PREVIEW_PRESENTER, 'PreviewPresenter',
                     {'start_fade_loop', 'cancel_fade_loop',
                      '_finish_fade_without_animation'}, namespace)

        class Presenter:
            pass

        for name in ('start_fade_loop', 'cancel_fade_loop',
                     '_finish_fade_without_animation'):
            setattr(Presenter, name, namespace[name])

        presenter = Presenter()
        presenter.highlight_duration = 1.5
        presenter._fade_loop_id = None
        presenter._fade_timeout_id = None
        presenter._current_time_factor = 0.0
        presenter.preview = SimpleNamespace(
            set_synctex_rectangles=Mock(),
            visible_synctex_rectangles_time=0.0)
        presenter.view = SimpleNamespace(
            drawing_area=SimpleNamespace(
                queue_draw=Mock(),
                add_tick_callback=lambda cb: 99,
                remove_tick_callback=lambda tid: None))
        return presenter, timeouts

    def test_normal_mode_uses_a_tick_callback(self):
        presenter, timeouts = self.make(reduced=False)
        presenter.start_fade_loop()
        self.assertEqual(presenter._fade_loop_id, 99)
        self.assertIsNone(presenter._fade_timeout_id)
        self.assertEqual(timeouts, [])

    def test_reduced_mode_uses_a_single_timeout(self):
        presenter, timeouts = self.make(reduced=True)
        presenter.start_fade_loop()
        self.assertIsNone(presenter._fade_loop_id)
        self.assertEqual(presenter._fade_timeout_id, 42)
        self.assertEqual([interval for interval, _cb in timeouts], [1500])
        self.assertEqual(presenter._current_time_factor, 1.0)

    def test_reduced_mode_timeout_clears_the_highlight(self):
        presenter, _timeouts = self.make(reduced=True)
        presenter.start_fade_loop()
        self.assertFalse(presenter._finish_fade_without_animation())
        presenter.preview.set_synctex_rectangles.assert_called_once_with([])
        self.assertEqual(presenter._current_time_factor, 0)
        self.assertIsNone(presenter._fade_timeout_id)

    def test_cancel_removes_the_reduced_mode_timeout(self):
        presenter, timeouts = self.make(reduced=True)
        presenter.start_fade_loop()
        presenter.cancel_fade_loop()
        self.assertIn(('removed', 42), timeouts)
        self.assertIsNone(presenter._fade_timeout_id)
        self.assertEqual(presenter._current_time_factor, 0)

    def test_restart_in_reduced_mode_drops_the_previous_timeout(self):
        presenter, timeouts = self.make(reduced=True)
        presenter.start_fade_loop()
        presenter.start_fade_loop()
        self.assertEqual(timeouts.count(('removed', 42)), 1)


if __name__ == '__main__':
    unittest.main()

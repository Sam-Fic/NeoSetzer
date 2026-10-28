#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Regression tests for full-page preview cache scheduling.'''

import ast
import math
from pathlib import Path
import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


RENDERER_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'document' / 'preview'
    / 'preview_page_renderer.py'
)
RENDERER_TREE = ast.parse(RENDERER_SOURCE.read_text(encoding='utf-8'))


def _renderer_method(name, **namespace):
    renderer = next(
        node for node in RENDERER_TREE.body
        if isinstance(node, ast.ClassDef) and node.name == 'PreviewPageRenderer'
    )
    method = next(
        node for node in renderer.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {'math': math, 'queue': queue, 'threading': threading, **namespace}
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(RENDERER_SOURCE), 'exec'),
         namespace)
    return namespace[name]


class _MainLoop:
    def __init__(self):
        self.sources = {}
        self.added = []
        self.removed = []
        self.Source = SimpleNamespace(remove=self.remove)

    def idle_add(self, callback):
        source_id = len(self.added) + 1
        self.added.append(source_id)
        self.sources[source_id] = callback
        return source_id

    def timeout_add(self, *args):
        raise AssertionError('Result delivery must not register a polling timer')

    def remove(self, source_id):
        self.removed.append(source_id)
        del self.sources[source_id]

    def dispatch(self):
        source_id = next(iter(self.sources))
        callback = self.sources[source_id]
        result = callback()
        if not result:
            self.sources.pop(source_id, None)
        return result


class _Layout:
    hidpi_factor = 1
    page_width_original = 100
    page_height_original = 200
    page_height = 200
    scale_factor = 1.0

    @staticmethod
    def get_page_by_offset(offset):
        return 1


class _Content:
    scrolling_offset_y = 0


class _View:
    content = _Content()

    @staticmethod
    def get_allocated_height():
        return 200


class _PopplerDocument:
    def __init__(self):
        self.page = SimpleNamespace(render=Mock())

    def get_page(self, page_number):
        return self.page

    @staticmethod
    def get_n_pages():
        return 1


class _Preview:
    def __init__(self, pdf_version):
        self.layout = _Layout()
        self.view = _View()
        self.poppler_document = _PopplerDocument()
        self.recolor_pdf = False
        # 渲染缓存版本号 = 内存文档版本（编译期间磁盘 mtime 变化不影响它）。
        self.pdf_version = pdf_version
        self.connect = Mock()
        self.document = SimpleNamespace(settings=Mock())


class _RendererHarness:
    _initialize = _renderer_method(
        '__init__', Observable=SimpleNamespace(__init__=lambda self: None))
    update_rendered_pages = _renderer_method('update_rendered_pages')
    rendered_pages_loop = _renderer_method('rendered_pages_loop')
    activate = _renderer_method('activate')
    deactivate = _renderer_method('deactivate')
    on_layout_or_position_changed = _renderer_method('on_layout_or_position_changed')
    on_recolor_pdf_changed = _renderer_method('on_recolor_pdf_changed')
    on_settings_changed = _renderer_method('on_settings_changed')
    invalidate_magnifier_requests = _renderer_method('invalidate_magnifier_requests')
    _queue_rendered_page = _renderer_method('_queue_rendered_page')
    _wake_rendered_pages_loop = _renderer_method('_wake_rendered_pages_loop')
    on_magnifier_result_produced = _renderer_method('on_magnifier_result_produced')

    def __init__(self, pdf_version, cached_page=None):
        self.main_loop = _MainLoop()
        self.thread_factory = Mock(side_effect=lambda **kwargs: Mock(
            join=Mock(side_effect=AssertionError('GTK must not join the render thread'))))
        cairo = SimpleNamespace(
            Format=SimpleNamespace(ARGB32=0),
            ImageSurface=lambda format, width, height: _FakeSurface(width, height),
            Context=Mock(),
        )
        for name in ('_request_rendered_pages_delivery', 'shutdown',
                     'render_page_loop', '_process_magnifier_todo'):
            method = _renderer_method(name, GObject=self.main_loop, GLib=self.main_loop,
                                      cairo=cairo, apply_magnifier_transform=Mock())
            setattr(self, name, method.__get__(self))
        self._ensure_loops_started = _renderer_method(
            '_ensure_loops_started', GObject=self.main_loop, GLib=self.main_loop,
            threading=SimpleNamespace(Thread=self.thread_factory)).__get__(self)
        self._initialize(_Preview(pdf_version))
        self.is_active = True
        self.rendered_pages = ({0: cached_page} if cached_page is not None else {})
        self.change_codes = []

    def add_change_code(self, code):
        self.change_codes.append(code)

    @property
    def queued_tasks(self):
        return self.render_queue.qsize() + self.render_queue_low_priority.qsize()


class _FakeSurface:
    '''桩 surface：生产条目用 cairo.ImageSurface，渲染器会读实际分辨率
    识别低分辨率 draft 占位（get_width < 页面设备像素宽）。'''

    def __init__(self, width, height):
        self._width = width
        self._height = height

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height


class TestPreviewPageCache(unittest.TestCase):

    @staticmethod
    def cached_page(pdf_version, width=100, height=200, surface_width=None):
        # Production tuple contract: surface, width, height, pdf version, colors.
        # surface_width 缺省 = 全分辨率（设备像素宽 = CSS 宽 × hidpi=1）。
        if surface_width is None:
            surface_width = width
        return [_FakeSurface(surface_width, height), width, height, pdf_version, None]

    def test_unchanged_cached_page_is_not_queued_again(self):
        renderer = _RendererHarness(3, self.cached_page(3))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 0)
        self.assertEqual(renderer.page_render_count, {})
        self.assertIn(0, renderer.rendered_pages)

    def test_missing_visible_page_queues_draft_then_full(self):
        # 视口内完全没有纹理的页：低分辨率 draft 走高优先级抢首个可见画面，
        # 全分辨率走低优先级在滚动停滞后精修；只递增一次渲染计数。
        renderer = _RendererHarness(1)

        renderer.update_rendered_pages()

        self.assertEqual(renderer.render_queue.qsize(), 1)
        self.assertEqual(renderer.render_queue_low_priority.qsize(), 1)
        draft_task = renderer.render_queue.get_nowait()
        full_task = renderer.render_queue_low_priority.get_nowait()
        self.assertTrue(draft_task.get('draft'))
        self.assertFalse(full_task.get('draft'))
        self.assertEqual(draft_task['render_count'], full_task['render_count'])
        self.assertEqual(renderer.page_render_count, {0: 1})

    def test_draft_placeholder_queues_full_resolution_refinement(self):
        # 低分辨率占位（版本/几何已匹配但 surface 偏小）保留显示，并排队
        # 全分辨率精修任务。
        renderer = _RendererHarness(1, self.cached_page(1, surface_width=25))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 1)
        self.assertFalse(renderer.render_queue.get_nowait().get('draft'))
        self.assertIn(0, renderer.rendered_pages)

    def test_pdf_version_change_queues_rerender_but_keeps_placeholder(self):
        # 版本不匹配不再驱逐缓存：旧纹理作占位继续显示，直到新版本渲染结果
        # 替换，避免每次构建成功后预览整屏白底。重绘任务仍须入队。
        renderer = _RendererHarness(2, self.cached_page(1))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 1)
        self.assertEqual(renderer.page_render_count, {0: 1})
        self.assertIn(0, renderer.rendered_pages)

    def test_size_change_keeps_placeholder_and_queues_refinement(self):
        for width, height in ((99, 200), (100, 199), (200, 400), (50, 100)):
            with self.subTest(width=width, height=height):
                old = self.cached_page(1, width=width, height=height)
                renderer = _RendererHarness(1, old)
                renderer.update_rendered_pages()
                self.assertEqual(renderer.queued_tasks, 1)
                self.assertIs(renderer.rendered_pages[0], old)

    def test_repeated_notifications_do_not_invalidate_pending_render(self):
        renderer = _RendererHarness(1, self.cached_page(1, width=99))
        for _ in range(20):
            renderer.update_rendered_pages()
        self.assertEqual(renderer.queued_tasks, 1)
        self.assertEqual(renderer.page_render_count, {0: 1})

    def test_resize_animation_keeps_content_until_final_result(self):
        old = self.cached_page(1)
        renderer = _RendererHarness(1, old)
        for width in (110, 120, 130, 120, 110):
            renderer.preview.layout.page_width_original = width
            renderer.preview.layout.page_height_original = width * 2
            renderer.update_rendered_pages()
            self.assertIs(renderer.rendered_pages[0], old)
        final_count = renderer.page_render_count[0]
        final = self.cached_page(1, width=110, height=220)
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=final_count, item=final))
        # 晚到的中间帧不得盖住最终图。
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=final_count - 1,
                                               item=self.cached_page(1, width=120, height=240)))
        renderer.rendered_pages_loop()
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer._pending_page_renders, {})

    def test_return_to_cached_size_invalidates_inflight_result(self):
        old = self.cached_page(1)
        renderer = _RendererHarness(1, old)
        renderer.preview.layout.page_width_original = 110
        renderer.update_rendered_pages()
        count = renderer.page_render_count[0]
        renderer.preview.layout.page_width_original = 100
        renderer.update_rendered_pages()
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=count,
                                               item=self.cached_page(1, width=110)))
        renderer.rendered_pages_loop()
        self.assertIs(renderer.rendered_pages[0], old)

    def test_old_pdf_result_is_discarded(self):
        renderer = _RendererHarness(2, self.cached_page(1))
        renderer.update_rendered_pages()
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=1,
                                               item=self.cached_page(1)))
        renderer.rendered_pages_loop()
        self.assertEqual(renderer.change_codes, [])

    def test_old_color_request_is_discarded(self):
        renderer = _RendererHarness(1, self.cached_page(1, width=99))
        renderer.update_rendered_pages()
        renderer.page_render_count[0] += 1  # 换色会生成新的请求编号。
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=1,
                                               item=self.cached_page(1)))
        renderer.rendered_pages_loop()
        self.assertEqual(renderer.change_codes, [])

    def test_failed_render_can_be_retried_without_losing_placeholder(self):
        old = self.cached_page(1, width=99)
        renderer = _RendererHarness(1, old)
        renderer.update_rendered_pages()
        renderer.rendered_pages_queue.put(dict(page_number=0, render_count=1, item=None))
        renderer.rendered_pages_loop()
        renderer.update_rendered_pages()
        self.assertEqual(renderer.page_render_count, {0: 2})
        self.assertIs(renderer.rendered_pages[0], old)

    def test_out_of_range_page_is_still_evicted(self):
        renderer = _RendererHarness(1, self.cached_page(1))
        renderer.rendered_pages[10] = self.cached_page(1)
        renderer.update_rendered_pages()
        self.assertNotIn(10, renderer.rendered_pages)

    @staticmethod
    def result(item, render_count=1):
        return dict(page_number=0, render_count=render_count, item=item)

    def produce_in_thread(self, callback):
        errors = []

        def produce():
            try:
                callback()
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=produce, daemon=True)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), 'Result producer blocked during delivery')
        if errors:
            raise errors[0]

    def test_activation_starts_worker_once_without_polling(self):
        renderer = _RendererHarness(1)
        self.assertFalse(renderer._render_thread_started)
        renderer.activate()
        renderer.activate()
        renderer.thread_factory.assert_called_once()
        renderer._render_thread.start.assert_called_once()
        self.assertEqual(renderer.main_loop.added, [])
        self.assertIsNone(renderer._rendered_pages_idle_id)
        self.assertIs(renderer.rendered_pages_loop(), False)

    def test_many_results_share_one_idle_and_one_notification(self):
        renderer = _RendererHarness(1)
        renderer.update_rendered_pages()
        final = self.cached_page(1)
        for _ in range(20):
            renderer._queue_rendered_page(self.result(self.cached_page(1, surface_width=25)))
        renderer._queue_rendered_page(self.result(final))
        self.assertEqual(renderer.main_loop.added, [1])
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer.change_codes, ['rendered_pages_changed'])
        self.assertEqual(renderer._pending_page_renders, {})
        self.assertTrue(renderer.rendered_pages_queue.empty())
        self.assertIsNone(renderer._rendered_pages_idle_id)
        self.assertEqual(renderer.main_loop.sources, {})

    def test_result_produced_while_installing_is_consumed_in_same_idle(self):
        renderer = _RendererHarness(1)
        renderer.update_rendered_pages()
        draft = self.cached_page(1, surface_width=25)
        final = self.cached_page(1)

        def get_width():
            self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(final)))
            self.assertEqual(renderer.main_loop.added, [1])
            return 25

        draft[0].get_width = get_width
        renderer._queue_rendered_page(self.result(draft))
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer.main_loop.added, [1])
        self.assertEqual(renderer.main_loop.sources, {})

    def test_result_produced_after_drain_gets_followup_idle(self):
        renderer = _RendererHarness(1)
        renderer.update_rendered_pages()
        draft = self.cached_page(1, surface_width=25)
        final = self.cached_page(1)
        notify = renderer.add_change_code

        def on_changed(code):
            notify(code)
            renderer.add_change_code = notify
            self.assertIsNotNone(renderer._rendered_pages_idle_id)
            self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(final)))
            self.assertEqual(renderer.main_loop.added, [1])

        renderer.add_change_code = on_changed
        renderer._queue_rendered_page(self.result(draft))
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], draft)
        self.assertEqual(renderer.main_loop.added, [1, 2])
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer.change_codes, ['rendered_pages_changed'] * 2)
        self.assertEqual(renderer.main_loop.sources, {})
        self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(final)))
        self.assertEqual(renderer.main_loop.added, [1, 2, 3])
        self.assertIs(renderer.main_loop.dispatch(), False)

    def test_inactive_results_wait_for_activation_without_idle(self):
        renderer = _RendererHarness(1)
        renderer.deactivate()
        final = self.cached_page(1)
        self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(final)))
        self.assertIs(renderer.rendered_pages_loop(), False)
        self.assertEqual(renderer.rendered_pages_queue.qsize(), 1)
        self.assertEqual(renderer.main_loop.added, [])
        renderer.activate()
        self.assertEqual(renderer.main_loop.added, [1])
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer.main_loop.sources, {})

    def test_pending_idle_stops_when_deactivated_and_resumes_on_activate(self):
        renderer = _RendererHarness(1)
        renderer.activate()
        renderer._queue_rendered_page(self.result(self.cached_page(1)))
        renderer.deactivate()
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertEqual(renderer.main_loop.sources, {})
        self.assertIsNone(renderer._rendered_pages_idle_id)
        self.assertEqual(renderer.rendered_pages_queue.qsize(), 1)
        renderer.activate()
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertTrue(renderer.rendered_pages_queue.empty())
        self.assertEqual(renderer.rendered_pages, {})
        self.assertEqual(renderer.main_loop.added, [1, 2])

    def test_layout_restoration_wakes_queue_and_rejects_old_pdf(self):
        renderer = _RendererHarness(1)
        renderer.activate()
        renderer._queue_rendered_page(self.result(self.cached_page(1)))
        renderer.preview.layout = None
        renderer.preview.pdf_version = 2
        renderer.on_layout_or_position_changed(renderer.preview)
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages_loop(), False)
        self.assertEqual(renderer.main_loop.sources, {})
        self.assertIsNone(renderer._rendered_pages_idle_id)
        final = self.cached_page(2)
        self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(final)))
        self.assertEqual(renderer.main_loop.added, [1])
        self.assertEqual(renderer.rendered_pages_queue.qsize(), 2)
        renderer.preview.layout = _Layout()
        renderer.on_layout_or_position_changed(renderer.preview)
        self.assertEqual(renderer.main_loop.added, [1, 2])
        self.assertIs(renderer.main_loop.dispatch(), False)
        self.assertIs(renderer.rendered_pages[0], final)
        self.assertEqual(renderer.change_codes, ['rendered_pages_changed'])
        self.assertEqual(renderer.main_loop.sources, {})

    def test_worker_success_and_failure_use_coalesced_delivery(self):
        class WorkerIdle(Exception):
            pass

        for fail in (False, True):
            with self.subTest(fail=fail):
                renderer = _RendererHarness(1)
                renderer.update_rendered_pages()
                render = renderer.preview.poppler_document.page.render
                if fail:
                    render.side_effect = RuntimeError('Render failed')
                get = renderer.render_queue.get

                def get_task(block=True, timeout=None):
                    if block and renderer.render_queue.empty():
                        raise WorkerIdle
                    return get(block=block, timeout=timeout)

                renderer.render_queue.get = get_task
                with self.assertRaises(WorkerIdle):
                    renderer.render_page_loop()
                self.assertEqual(render.call_count, 2)
                self.assertEqual(renderer.rendered_pages_queue.qsize(), 1 if fail else 2)
                self.assertEqual(renderer.main_loop.added, [1])
                self.assertIs(renderer.main_loop.dispatch(), False)
                self.assertEqual(renderer._pending_page_renders, {})
                self.assertEqual(bool(renderer.rendered_pages), not fail)

    @staticmethod
    def magnifier_task():
        return dict(request_id=0, page_number=0, out_css_size=100, hidpi_factor=1,
                    density=1, center_x_pt=0, center_y_pt=0, rotation=0,
                    matching_theme_colors=None)

    def test_shutdown_cancels_idles_and_rejects_late_results_without_join(self):
        renderer = _RendererHarness(1)
        renderer.activate()
        renderer._queue_rendered_page(self.result(self.cached_page(1)))
        renderer._process_magnifier_todo(self.magnifier_task())
        callbacks = list(renderer.main_loop.sources.values())
        renderer.shutdown()
        self.assertTrue(renderer._shutting_down)
        self.assertFalse(renderer.is_active)
        self.assertEqual(renderer.main_loop.removed, [1, 2])
        self.assertIsNone(renderer._rendered_pages_idle_id)
        self.assertIsNone(renderer._magnifier_result_idle_id)
        self.assertTrue(renderer.rendered_pages_queue.empty())
        self.assertTrue(renderer.magnified_pages_queue.empty())
        self.assertTrue(renderer.render_queue_low_priority.empty())
        self.assertIsNone(renderer.render_queue.get_nowait())
        renderer._render_thread.join.assert_not_called()
        renderer.preview.document.settings.disconnect.assert_called_once_with(
            'settings_changed', renderer._settings_callback)
        self.produce_in_thread(lambda: renderer._queue_rendered_page(self.result(None)))
        self.produce_in_thread(lambda: renderer._process_magnifier_todo(self.magnifier_task()))
        renderer.activate()
        renderer._ensure_loops_started()
        renderer.on_layout_or_position_changed(renderer.preview)
        for callback in callbacks:
            self.assertIs(callback(), False)
        renderer.shutdown()
        self.assertEqual(renderer.main_loop.added, [1, 2])
        self.assertEqual(renderer.main_loop.removed, [1, 2])
        self.assertEqual(renderer.main_loop.sources, {})
        self.assertTrue(renderer.rendered_pages_queue.empty())
        self.assertTrue(renderer.magnified_pages_queue.empty())
        self.assertEqual(renderer.change_codes, [])
        renderer.thread_factory.assert_called_once()

    def test_shutdown_between_enqueue_and_request_prevents_idle_registration(self):
        renderer = _RendererHarness(1)
        renderer.update_rendered_pages()
        request = renderer._request_rendered_pages_delivery
        enqueued = threading.Event()
        resume = threading.Event()

        def request_after_shutdown():
            enqueued.set()
            if not resume.wait(timeout=2):
                raise AssertionError('Shutdown did not release result producer')
            request()

        renderer._request_rendered_pages_delivery = request_after_shutdown
        worker = threading.Thread(
            target=renderer._queue_rendered_page,
            args=(self.result(self.cached_page(1)),), daemon=True)
        worker.start()
        try:
            self.assertTrue(enqueued.wait(timeout=2))
            renderer.shutdown()
        finally:
            resume.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(renderer.main_loop.added, [])
        self.assertTrue(renderer.rendered_pages_queue.empty())


if __name__ == '__main__':
    unittest.main()

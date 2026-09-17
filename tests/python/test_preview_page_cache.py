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
import unittest


RENDERER_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'document' / 'preview'
    / 'preview_page_renderer.py'
)


def _renderer_method(name):
    tree = ast.parse(RENDERER_SOURCE.read_text(encoding='utf-8'))
    renderer = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'PreviewPageRenderer'
    )
    method = next(
        node for node in renderer.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {'math': math}
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(RENDERER_SOURCE), 'exec'),
         namespace)
    return namespace[name]


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


class _RendererHarness:
    update_rendered_pages = _renderer_method('update_rendered_pages')
    rendered_pages_loop = _renderer_method('rendered_pages_loop')

    def __init__(self, pdf_version, cached_page=None):
        self.is_active_lock = threading.Lock()
        self.is_active = True
        self.preview = _Preview(pdf_version)
        self.visible_pages_lock = threading.Lock()
        self.visible_pages = []
        self.visible_pages_additional = []
        self.page_width = None
        self.pdf_version = None
        self.maximum_rendered_pixels = 20_000_000
        self.rendered_pages = ({0: cached_page} if cached_page is not None else {})
        self.render_queue = queue.Queue()
        self.render_queue_low_priority = queue.Queue()
        self.page_render_count_lock = threading.Lock()
        self.page_render_count = {}
        self._pending_page_renders = {}
        self.rendered_pages_queue = queue.Queue()
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


if __name__ == '__main__':
    unittest.main()

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


def _update_rendered_pages_method():
    tree = ast.parse(RENDERER_SOURCE.read_text(encoding='utf-8'))
    renderer = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'PreviewPageRenderer'
    )
    method = next(
        node for node in renderer.body
        if isinstance(node, ast.FunctionDef) and node.name == 'update_rendered_pages'
    )
    namespace = {'math': math}
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(RENDERER_SOURCE), 'exec'),
         namespace)
    return namespace['update_rendered_pages']


_UPDATE_RENDERED_PAGES = _update_rendered_pages_method()


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
    update_rendered_pages = _UPDATE_RENDERED_PAGES

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

    def test_page_width_change_evicts_and_queues_cached_page(self):
        renderer = _RendererHarness(1, self.cached_page(1, width=99))

        renderer.update_rendered_pages()

        # 驱逐后视口内无纹理 → draft + 全分辨率共两个任务。
        self.assertEqual(renderer.queued_tasks, 2)
        self.assertEqual(renderer.page_render_count, {0: 1})
        self.assertNotIn(0, renderer.rendered_pages)

    def test_page_height_change_evicts_and_queues_cached_page(self):
        renderer = _RendererHarness(1, self.cached_page(1, height=199))

        renderer.update_rendered_pages()

        # 驱逐后视口内无纹理 → draft + 全分辨率共两个任务。
        self.assertEqual(renderer.queued_tasks, 2)
        self.assertEqual(renderer.page_render_count, {0: 1})
        self.assertNotIn(0, renderer.rendered_pages)


if __name__ == '__main__':
    unittest.main()

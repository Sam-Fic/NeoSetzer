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
    def __init__(self, pdf_date):
        self.layout = _Layout()
        self.view = _View()
        self.poppler_document = _PopplerDocument()
        self.recolor_pdf = False
        self.pdf_date = pdf_date

    def get_pdf_date(self):
        return self.pdf_date


class _RendererHarness:
    update_rendered_pages = _UPDATE_RENDERED_PAGES

    def __init__(self, pdf_date, cached_page=None):
        self.is_active_lock = threading.Lock()
        self.is_active = True
        self.preview = _Preview(pdf_date)
        self.visible_pages_lock = threading.Lock()
        self.visible_pages = []
        self.visible_pages_additional = []
        self.page_width = None
        self.pdf_date = None
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


class TestPreviewPageCache(unittest.TestCase):

    @staticmethod
    def cached_page(pdf_date, width=100, height=200):
        # Production tuple contract: surface, width, height, PDF date, colors.
        return [object(), width, height, pdf_date, None]

    def test_unchanged_cached_page_is_not_queued_again(self):
        renderer = _RendererHarness(123456789, self.cached_page(123456789))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 0)
        self.assertEqual(renderer.page_render_count, {})

    def test_pdf_date_change_invalidates_and_queues_cached_page(self):
        renderer = _RendererHarness(2, self.cached_page(1))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 1)
        self.assertEqual(renderer.page_render_count, {0: 1})

    def test_page_width_change_invalidates_and_queues_cached_page(self):
        renderer = _RendererHarness(1, self.cached_page(1, width=99))

        renderer.update_rendered_pages()

        self.assertEqual(renderer.queued_tasks, 1)
        self.assertEqual(renderer.page_render_count, {0: 1})


if __name__ == '__main__':
    unittest.main()

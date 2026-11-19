#!/usr/bin/env python3
# coding: utf-8
#
# 增量重编译后预览不得闪白。
#
# 根因：load_pdf 每次成功加载都 self.layout = None，而 draw 见 layout is None
# 时"建完布局就直接 return"——那一帧什么都不画，用户看到闪白。构建慢时一两
# 秒一次看不出来，PhiTeX 常驻 session 下每次保存只要十几毫秒，就成了持续闪烁。
#
# 修法：几何（页数 + 每页尺寸）没变就沿用现有 layout。几何真变了（增删页、
# 纸张尺寸变化）仍照旧作废重建，因为那时旧布局确实是错的。

import ast
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = 'setzer/document/preview/preview.py'


def load_load_pdf(namespace):
    source = ROOT / PREVIEW
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'Preview')
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == 'load_pdf')
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['load_pdf']


class FakeLayout:
    pass


def make_namespace(document):
    return {
        'os': os,
        'Poppler': mock.Mock(),
        'GLib': mock.Mock(filename_to_uri=lambda p: 'uri:' + p),
        'ExternalPdfState': mock.Mock(CURRENT='current'),
    }


def make_preview(page_sizes, with_layout=True):
    preview = mock.Mock()
    preview.pdf_filename = '/p/main.pdf'
    preview.poppler_document = None
    preview.pdf_version = 0
    preview.page_sizes = list(page_sizes)
    preview.page_heights = [h for w, h in page_sizes]
    preview.layout = FakeLayout() if with_layout else None
    preview.document = mock.Mock()
    return preview


class LayoutRetentionTest(unittest.TestCase):

    def setUp(self):
        self.new_document = mock.Mock()
        # 62 页 A4，与重建后一致
        self.sizes = [(595.276, 841.89)] * 62

    def run_load(self, before_sizes, with_layout=True):
        '''用例先改 self.sizes 再调用：文档页数/尺寸必须按最新的 sizes 配置。'''
        def get_page(index):
            page = mock.Mock()
            width, height = self.sizes[index]
            page.get_size.return_value = mock.Mock(width=width, height=height)
            return page
        self.new_document.get_n_pages.return_value = len(self.sizes)
        self.new_document.get_page.side_effect = get_page

        ns = make_namespace(self.new_document)
        load_pdf = load_load_pdf(ns)
        preview = make_preview(before_sizes, with_layout=with_layout)
        ns['Poppler'].Document.new_from_file.return_value = self.new_document
        preview._external_pdf_tracker = mock.Mock()
        load_pdf(preview)
        return preview

    def test_layout_kept_when_geometry_unchanged(self):
        '''一次编辑后页数与尺寸都不变：必须沿用旧 layout，否则下一帧空白。'''
        preview = self.run_load(self.sizes)
        self.assertIsNotNone(preview.layout)
        self.assertIsInstance(preview.layout, FakeLayout)

    def test_layout_dropped_when_page_count_changes(self):
        '''增删一页后旧布局的偏移表全错，必须重建。'''
        self.sizes.append((595.276, 841.89))
        preview = self.run_load([(595.276, 841.89)] * 62)
        self.assertIsNone(preview.layout)

    def test_layout_dropped_when_a_page_size_changes(self):
        changed = list(self.sizes)
        changed[3] = (600.0, 841.89)
        self.sizes = changed
        preview = self.run_load([(595.276, 841.89)] * 62)
        self.assertIsNone(preview.layout)

    def test_first_load_does_not_invent_a_layout(self):
        '''首次打开 PDF 时还没有 layout 对象可沿用，也不该由 load_pdf 造一个。'''
        preview = self.run_load([], with_layout=False)
        self.assertIsNone(preview.layout)
        self.assertEqual(len(preview.page_sizes), 62)

    def test_version_still_bumped(self):
        '''沿用 layout 不等于沿用旧像素：内存文档版本必须递增，让改动页重渲染。'''
        preview = self.run_load(self.sizes)
        self.assertEqual(preview.pdf_version, 1)


if __name__ == '__main__':
    unittest.main()

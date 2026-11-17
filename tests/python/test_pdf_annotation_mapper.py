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

'''pdf_annotation_mapper 单测（mock synctex 查询，不跑 subprocess）。

契约：
  - 文字拖选 → 首末反查行范围（整行）；任一端点在块内 → 升级整块；
    focus 优先 Column/Offset 源码子串（kind='source'），退化 PDF 文本
    （kind='pdf-text'）。
  - 块/图片 → center 反查 → 整块；focus 为块内渲染文本。
  - 跨文件（被 include 的子文件）磁盘读；反查全失败 → None。
  - block_pdf_rect：synctex view 的 v 为 y-up 底边 → top-down y1=v-height；
    只取本页矩形并集，跳过空行/注释行。
'''

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from setzer.ai_fix import pdf_annotation_mapper as mapper


class FakeBuildSystem:
    def correct_synctex_line(self, line0, pdf_line_text, source_lines=None):
        return line0  # 非 PhiTeX：恒等


class FakeDocument:
    def __init__(self, filename='/p/main.tex'):
        self.filename = filename
        self.build_system = FakeBuildSystem()

    def get_filename(self):
        return self.filename


def pos(filename='/p/main.tex', line=1, column=None, offset=None):
    return {'filename': filename, 'line': line, 'column': column, 'offset': offset}


def text_extra(point_a=(0, 10, 20), point_b=(0, 50, 20),
               line_text_a='', line_text_b=''):
    return {'point_a': point_a, 'point_b': point_b,
            'line_text_a': line_text_a, 'line_text_b': line_text_b}


def run_map(document, payload, extra, positions, lines=None,
            pdf='/p/main.pdf', tex='/p/main.tex'):
    '''_map_sync + mock 反查。positions 为逐次 query_source_position 返回值。'''
    with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                           return_value='/cache'), \
            mock.patch.object(mapper.synctex_query, 'synctex_directory_for',
                              return_value='/cache'), \
            mock.patch.object(mapper.synctex_query, 'query_source_position',
                              side_effect=positions), \
            mock.patch.object(mapper, '_read_file_lines',
                              return_value=lines or []):
        return mapper._map_sync(document, payload, extra, pdf, tex, lines)


class TextSelectionTest(unittest.TestCase):

    LINES = ['alpha beta gamma\n', 'second line\n']

    def test_whole_line_with_source_focus_substring(self):
        payload = {'kind': 'text', 'text': 'beta'}
        result = run_map(FakeDocument(), payload, text_extra(),
                         [pos(line=1, offset=6), pos(line=1, offset=9)],
                         lines=self.LINES)
        self.assertEqual(result['start_line'], 1)
        self.assertEqual(result['end_line'], 1)
        self.assertEqual(result['focus_text'], 'beta')
        self.assertEqual(result['focus_kind'], 'source')
        self.assertEqual(result['filename'], '/p/main.tex')

    def test_multiline_focus_joins_source_lines(self):
        payload = {'kind': 'text', 'text': 'beta gamma second'}
        result = run_map(FakeDocument(), payload, text_extra(),
                         [pos(line=1, offset=6), pos(line=2, offset=5)],
                         lines=self.LINES)
        self.assertEqual((result['start_line'], result['end_line']), (1, 2))
        self.assertEqual(result['focus_text'], 'beta gamma\nsecond')
        self.assertEqual(result['focus_kind'], 'source')

    def test_endpoint_inside_block_upgrades_to_whole_block(self):
        lines = ['intro\n', '\\begin{equation}\n', 'E = mc^2\n',
                 '\\end{equation}\n', 'outro\n']
        payload = {'kind': 'text', 'text': 'mc'}
        result = run_map(FakeDocument(), payload, text_extra(),
                         [pos(line=3, offset=4), pos(line=3, offset=5)],
                         lines=lines)
        self.assertEqual((result['start_line'], result['end_line']), (2, 4))
        self.assertEqual(result['focus_text'], 'mc')

    def test_missing_offsets_fall_back_to_pdf_text(self):
        payload = {'kind': 'text', 'text': 'rendered words'}
        result = run_map(FakeDocument(), payload, text_extra(),
                         [pos(line=1), pos(line=1)], lines=self.LINES)
        self.assertEqual(result['focus_text'], 'rendered words')
        self.assertEqual(result['focus_kind'], 'pdf-text')

    def test_one_endpoint_missing_uses_the_other(self):
        payload = {'kind': 'text', 'text': 'x'}
        result = run_map(FakeDocument(), payload, text_extra(),
                         [None, pos(line=2, offset=0)], lines=self.LINES)
        self.assertEqual((result['start_line'], result['end_line']), (2, 2))

    def test_both_endpoints_missing_returns_none(self):
        payload = {'kind': 'text', 'text': 'x'}
        self.assertIsNone(run_map(FakeDocument(), payload, text_extra(),
                                  [None, None], lines=self.LINES))

    def test_cross_file_reads_from_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = os.path.join(tmp, 'child.tex')
            with open(child, 'w', encoding='utf-8') as f:
                f.write('child line one\nchild line two\n')
            payload = {'kind': 'text', 'text': 'one'}
            # 活动文档是 main.tex，反查落在子文件 → 磁盘读而非活动快照
            # （不 mock _read_file_lines，走真实磁盘读）。
            with mock.patch.object(mapper.synctex_query,
                                   'synctex_directory_for_source',
                                   return_value='/cache'), \
                    mock.patch.object(mapper.synctex_query,
                                      'query_source_position',
                                      side_effect=[pos(filename=child, line=1, offset=11),
                                                   pos(filename=child, line=1, offset=13)]):
                result = mapper._map_sync(FakeDocument('/p/main.tex'), payload,
                                          text_extra(), '/p/main.pdf',
                                          '/p/main.tex', ['main snapshot\n'])
        self.assertEqual(result['filename'], child)
        self.assertEqual(result['focus_text'], 'one')
        self.assertEqual(result['focus_kind'], 'source')

    def test_phitex_line_correction_is_applied(self):
        document = FakeDocument()
        document.build_system = mock.Mock()
        document.build_system.correct_synctex_line.return_value = 1  # 0-based
        payload = {'kind': 'text', 'text': 'x'}
        result = run_map(document, payload,
                         text_extra(line_text_a='second line',
                                    line_text_b='second line'),
                         [pos(line=5), pos(line=5)], lines=self.LINES)
        self.assertEqual((result['start_line'], result['end_line']), (2, 2))
        document.build_system.correct_synctex_line.assert_called()


class BlockSelectionTest(unittest.TestCase):

    LINES = ['\\begin{figure}\n', '\\includegraphics{a}\n', '\\end{figure}\n']

    def test_block_center_maps_to_whole_environment(self):
        payload = {'kind': 'block', 'page': 0, 'rect': (10, 20, 100, 80),
                   'center': (55, 50)}
        extra = {'focus_text': 'rendered block text', 'pdf_line_text': ''}
        result = run_map(FakeDocument(), payload, extra,
                         [pos(line=2)], lines=self.LINES)
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))
        self.assertEqual(result['focus_text'], 'rendered block text')
        self.assertEqual(result['focus_kind'], 'block')

    def test_image_without_text_uses_placeholder(self):
        payload = {'kind': 'image', 'page': 0, 'rect': (10, 20, 100, 80),
                   'center': (55, 50)}
        extra = {'focus_text': '(image block)', 'pdf_line_text': ''}
        result = run_map(FakeDocument(), payload, extra,
                         [pos(line=2)], lines=self.LINES)
        self.assertEqual(result['focus_text'], '(image block)')
        self.assertEqual(result['focus_kind'], 'block')

    def test_block_query_failure_returns_none(self):
        payload = {'kind': 'block', 'page': 0, 'rect': (0, 0, 1, 1),
                   'center': (0.5, 0.5)}
        self.assertIsNone(run_map(FakeDocument(), payload, {}, [None],
                                  lines=self.LINES))


class BlockPdfRectTest(unittest.TestCase):

    def test_union_bbox_top_down_and_page_filter(self):
        lines = ['a\n', 'b\n']
        rects = [
            {'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10},
            {'page': 1, 'h': 30, 'v': 90, 'width': 40, 'height': 10},
            {'page': 2, 'h': 0, 'v': 500, 'width': 10, 'height': 10},  # 它页忽略
        ]
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=rects):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 2, lines, '/cache')
        # v 为 y-up 底边：top-down y1 = v - height。
        self.assertEqual(rect, (10, 80, 70, 100))

    def test_skips_blank_and_comment_lines(self):
        lines = ['\n', '% comment\n', 'real\n']
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[]) as query:
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines, '/cache')
        self.assertIsNone(rect)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(query.call_args[0][2], 3)  # 只查了第 3 行

    def test_no_rectangles_returns_none(self):
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[]):
            self.assertIsNone(mapper.block_pdf_rect(
                '/p/main.tex', '/p/main.pdf', '/p/main.tex', 0, 1, 1,
                ['x\n'], '/cache'))


class ResolveBlockTest(unittest.TestCase):

    LINES = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']

    def resolve(self, positions, rects, document=None):
        document = document or FakeDocument()
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  side_effect=positions), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=rects):
            return mapper.resolve_block(document, '/p/main.pdf', 0, 50, 50,
                                        active_lines=self.LINES)

    def test_resolves_block_with_union_rect(self):
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects)
        self.assertEqual(result['filename'], '/p/main.tex')
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))
        self.assertEqual(result['kind'], 'environment')
        self.assertEqual(result['env'], 'equation')
        self.assertEqual(result['rect'], (10, 90, 50, 100))

    def test_plain_line_click_returns_none(self):
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=1)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[]):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          5, 5, active_lines=['plain text\n'])
        self.assertIsNone(result)

    def test_query_failure_returns_none(self):
        self.assertIsNone(self.resolve([None], []))


class MapSelectionEntryTest(unittest.TestCase):

    def test_none_payload_calls_back_none(self):
        preview = SimpleNamespace(pdf_filename='/p/main.pdf',
                                  poppler_document=mock.Mock())
        results = []
        with mock.patch.object(mapper.GLib, 'idle_add',
                               side_effect=lambda fn, *a: fn(*a)):
            mapper.map_selection(preview, FakeDocument(), None, results.append)
        self.assertEqual(results, [None])

    def test_missing_pdf_calls_back_none(self):
        preview = SimpleNamespace(pdf_filename=None, poppler_document=None)
        results = []
        with mock.patch.object(mapper.GLib, 'idle_add',
                               side_effect=lambda fn, *a: fn(*a)):
            mapper.map_selection(preview, FakeDocument(),
                                 {'kind': 'text', 'rects': {}, 'text': ''},
                                 results.append)
        self.assertEqual(results, [None])


if __name__ == '__main__':
    unittest.main()
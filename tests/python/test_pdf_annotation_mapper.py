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
    只取本页矩形并集，跳过空行/注释行/纯边界行；浮动体并入相邻整图。
  - resolve_block：块矩形须覆盖点击点，否则空间降级搜索附近块
    （图片内部等 synctex 无记录区域反查会落在块外行）。
  - 表格/显示数学视觉矩形：字形聚行 + 点击种子连通扩展出 band；
    源块归属用可靠锚行（edit 反查 + view 盒几何校验）往下源码/页面
    顺序步进对应，防 edit 反查落邻块（实测 cases 反查到 matrix）。
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

    def test_payload_with_source_block_reused_without_query(self):
        # 块吸附已解析出源码块 → 直接复用，不再二次反查（与高亮框
        # 对应的块严格一致）。
        payload = {'kind': 'block', 'page': 0, 'rect': (10, 20, 100, 80),
                   'center': (55, 50), 'filename': '/p/chapter.tex',
                   'start_line': 4, 'end_line': 9}
        extra = {'focus_text': 'block text'}
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position') as query:
            result = mapper._map_sync(FakeDocument(), payload, extra,
                                      '/p/main.pdf', '/p/main.tex', None)
        query.assert_not_called()
        self.assertEqual(result['filename'], '/p/chapter.tex')
        self.assertEqual((result['start_line'], result['end_line']), (4, 9))
        self.assertEqual(result['focus_kind'], 'block')

    def test_line_hit_falls_back_spatially(self):
        # 整图同步命中路径（payload 无源码信息）：反查落在块外行 →
        # 空间降级搜索覆盖点击点的附近块。
        lines = ['text\n', '\\begin{figure}\n', '\\includegraphics{a}\n',
                 '\\caption{cap}\n', '\\end{figure}\n', 'more\n']
        payload = {'kind': 'image', 'page': 0, 'rect': (90, 30, 190, 100),
                   'center': (140, 60)}
        extra = {'focus_text': '(image block)',
                 'image_rects': [(90, 30, 190, 88)]}
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=6)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[caption]):
            result = mapper._map_sync(FakeDocument(), payload, extra,
                                      '/p/main.pdf', '/p/main.tex', lines)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (2, 5))
        self.assertEqual(result['focus_kind'], 'block')


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

    def test_skips_pure_boundary_lines(self):
        # 纯边界行的 synctex 记录常是周围段落盒，不得混入并集。
        lines = ['\\begin{figure}[h]\n', '\\centering\n',
                 '\\caption{cap}\n', '\\end{figure}\n']
        paragraph = {'page': 1, 'h': 0, 'v': 500, 'width': 340, 'height': 20}
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}

        def fake_query(tex, pdf, ln, directory):
            return [paragraph] if ln in (1, 4) else [caption]

        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=fake_query) as query:
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 4, lines,
                                         '/cache', env='figure')
        self.assertEqual(rect, (100, 90, 180, 100))
        queried = [c[0][2] for c in query.call_args_list]
        self.assertNotIn(1, queried)
        self.assertNotIn(4, queried)

    def test_inline_boundary_content_not_skipped(self):
        # 行内混有正文内容（单行 tabular）不属纯边界行，照常查询。
        lines = ['\\begin{tabular}{ll} a & b \\end{tabular}\n']
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=rects) as query:
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 1, lines,
                                         '/cache', env='tabular')
        self.assertEqual(rect, (10, 90, 50, 100))
        self.assertEqual(query.call_count, 1)

    def test_float_env_merges_adjacent_image(self):
        lines = ['\\begin{figure}\n', '\\includegraphics{a}\n',
                 '\\caption{cap}\n', '\\end{figure}\n']
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        image = (90, 30, 190, 88)  # caption 上方 2pt，水平有交集
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[caption]):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 4, lines,
                                         '/cache', env='figure',
                                         image_rects=[image])
        self.assertEqual(rect, (90, 30, 190, 100))

    def test_float_env_ignores_distant_image(self):
        lines = ['\\begin{figure}\n', '\\caption{cap}\n', '\\end{figure}\n']
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        far_image = (90, 300, 190, 380)  # 垂直相距远超容差
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[caption]):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines,
                                         '/cache', env='figure',
                                         image_rects=[far_image])
        self.assertEqual(rect, (100, 90, 180, 100))

    def test_non_float_env_does_not_merge_image(self):
        lines = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']
        box = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[box]):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines,
                                         '/cache', env='equation',
                                         image_rects=[(90, 30, 190, 88)])
        self.assertEqual(rect, (100, 90, 180, 100))

    def test_skips_label_lines(self):
        # \label 行的 synctex 记录是公式上方正文行的整段宽盒（实测），
        # 混入并集会把块矩形拉到上方正文行，导致正文点击误吸到公式。
        lines = ['\\begin{equation}\n', '\\label{eq:x}\n', 'x=1\n',
                 '\\end{equation}\n']
        paragraph = {'page': 1, 'h': 0, 'v': 500, 'width': 340, 'height': 20}
        content = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}

        def fake_query(tex, pdf, ln, directory):
            return [paragraph] if ln == 2 else [content]

        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=fake_query) as query:
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 4, lines,
                                         '/cache', env='equation')
        self.assertEqual(rect, (100, 90, 180, 100))
        queried = [c[0][2] for c in query.call_args_list]
        self.assertNotIn(2, queried)

    def test_numbered_math_merges_end_line_number_box(self):
        # 公式编号在右缘页边处，内容行盒盖不到；\end{行} 的全宽盒
        # （含编号）与内容区同高，并入后块矩形覆盖编号区。
        lines = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']
        content = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        full_width = {'page': 1, 'h': 50, 'v': 100, 'width': 290, 'height': 10}
        paragraph = {'page': 1, 'h': 0, 'v': 500, 'width': 340, 'height': 20}

        def fake_query(tex, pdf, ln, directory):
            if ln == 3:
                return [full_width, paragraph]
            return [content]

        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=fake_query):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines,
                                         '/cache', env='equation')
        # 全宽盒并入（x 扩到 50..340）；远处段落盒不垂直重叠，排除。
        self.assertEqual(rect, (50, 90, 340, 100))

    def test_numbered_math_ignores_non_overlapping_end_boxes(self):
        lines = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']
        content = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        above = {'page': 1, 'h': 0, 'v': 500, 'width': 340, 'height': 20}

        def fake_query(tex, pdf, ln, directory):
            return [above] if ln == 3 else [content]

        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=fake_query):
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines,
                                         '/cache', env='equation')
        self.assertEqual(rect, (100, 90, 180, 100))

    def test_starred_math_does_not_merge_number_box(self):
        # equation* 无编号：不查 end 行全宽盒，避免无谓放宽块矩形。
        lines = ['\\begin{equation*}\n', 'x=1\n', '\\end{equation*}\n']
        content = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        full_width = {'page': 1, 'h': 50, 'v': 100, 'width': 290, 'height': 10}

        def fake_query(tex, pdf, ln, directory):
            return [full_width] if ln == 3 else [content]

        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=fake_query) as query:
            rect = mapper.block_pdf_rect('/p/main.tex', '/p/main.pdf',
                                         '/p/main.tex', 0, 1, 3, lines,
                                         '/cache', env='equation*')
        self.assertEqual(rect, (100, 90, 180, 100))
        queried = [c[0][2] for c in query.call_args_list]
        self.assertNotIn(3, queried)


class ResolveBlockTest(unittest.TestCase):

    LINES = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']

    def resolve(self, positions, rects, document=None, x=30, y=95,
                image_rects=()):
        document = document or FakeDocument()
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  side_effect=positions), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=rects):
            return mapper.resolve_block(document, '/p/main.pdf', 0, x, y,
                                        active_lines=self.LINES,
                                        image_rects=image_rects)

    def test_resolves_block_with_union_rect(self):
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects)
        self.assertEqual(result['filename'], '/p/main.tex')
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))
        self.assertEqual(result['kind'], 'environment')
        self.assertEqual(result['env'], 'equation')
        self.assertEqual(result['rect'], (10, 90, 50, 100))

    def test_click_below_box_within_vertical_pad_accepted(self):
        # synctex 盒比视觉块小（分数下缘/glue 不在盒内）：盒下沿 4pt 内接受。
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=30, y=103)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))

    def test_click_in_gap_between_blocks_not_snapped(self):
        # 块间空隙实测 ~10pt：垂直容差只放 4pt，间隙点击不误吸邻块。
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=30, y=108)
        self.assertIsNone(result)

    def test_click_horizontally_outside_within_pad_accepted(self):
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=60, y=95)
        self.assertIsNotNone(result)

    def test_tabular_right_column_click_accepted(self):
        # synctex 只记录部分列（实测两列 tabular 只有左列有记录）：
        # 右列点击反查到块外行，降级搜索的块矩形只盖左列，
        # 表格类环境垂直在块内即接受。
        lines = ['\\begin{tabular}{|c|c|}\n', '\\hline\n',
                 'alpha & beta \\\\\n', '\\hline\n', '\\end{tabular}\n']
        left_col = {'page': 1, 'h': 236, 'v': 281, 'width': 78, 'height': 24}
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=6)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[left_col]):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          400, 270, active_lines=lines)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (1, 5))
        self.assertEqual(result['env'], 'tabular')

    def test_tabular_click_below_block_not_accepted(self):
        # 表格特例只放宽水平：垂直远离块仍不吸附。
        lines = ['\\begin{tabular}{|c|c|}\n', 'alpha & beta \\\\\n',
                 '\\end{tabular}\n']
        left_col = {'page': 1, 'h': 236, 'v': 281, 'width': 78, 'height': 24}
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=4)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[left_col]):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          400, 320, active_lines=lines)
        self.assertIsNone(result)

    def test_equation_number_region_click_accepted(self):
        # 点击右缘公式编号：反查返回 \end{equation}（块内），但内容行
        # 盒盖不到编号区；end 行全宽盒并入后接受。
        content = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        full_width = {'page': 1, 'h': 50, 'v': 100, 'width': 290, 'height': 10}

        def fake_query(tex, pdf, ln, directory):
            return [full_width] if ln == 3 else [content]

        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=3)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=fake_query):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          300, 95, active_lines=self.LINES)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))
        self.assertEqual(result['rect'], (50, 90, 340, 100))

    def test_math_env_left_margin_click_accepted(self):
        # 显示数学居中排版、两侧是页边空白：水平放宽，左边距点击
        # （反查命中块内行）也吸附。
        rects = [{'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=20, y=95)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (1, 3))

    def test_math_env_vertical_outside_not_accepted(self):
        # 水平放宽不改变垂直判定：远离块的点击不吸附。
        rects = [{'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=20, y=108)
        self.assertIsNone(result)

    def test_displaymath_env_none_horizontally_relaxed(self):
        # displaymath（\[...\]/$$...$$）无环境名，env=None 同样放宽。
        self.assertTrue(mapper._block_accepts((10, 90, 50, 100), 300, 95, None))
        self.assertFalse(mapper._block_accepts((10, 90, 50, 100), 300, 108, None))

    def test_fallback_window_covers_distant_return_line(self):
        # 公式间隙点击 synctex 可能返回很远的行（实测 \end{document}）：
        # 降级窗口要能盖到近百行外的块。
        lines = self.LINES + ['text\n'] * 110
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=rects):
            fallback = mapper._fallback_block_at(
                lines, 100, 0, 30, 95, '/p/main.tex', '/p/main.pdf',
                '/cache', ())
        self.assertIsNotNone(fallback)
        self.assertEqual((fallback[0], fallback[1]), (1, 3))

    def test_click_outside_block_rect_falls_back_spatially(self):
        # 反查命中块 A 但 A 的 synctex 盒不含点击点（实测：公式间隙
        # 反查到的行，其盒是别处段落）：降级搜索真正覆盖点击点的块 B。
        lines = ['\\begin{equation}\n', 'x=1\n', '\\end{equation}\n',
                 'text\n', '\\begin{figure}\n', '\\caption{cap}\n',
                 '\\end{figure}\n']
        equation = {'page': 1, 'h': 10, 'v': 410, 'width': 40, 'height': 10}
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}

        def fake_query(tex, pdf, ln, directory):
            if ln == 2:
                return [equation]
            if ln == 6:
                return [caption]
            return []

        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=2)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=fake_query):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          140, 95, active_lines=lines)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (5, 7))
        self.assertEqual(result['env'], 'figure')
        self.assertEqual(result['rect'], (100, 90, 180, 100))

    def test_click_on_line_outside_block_falls_back_to_nearby_block(self):
        # 图片内部点击：synctex 返回块外行（expand 得 'line'），降级
        # 搜索附近块；figure 块矩形并入整图后覆盖点击点。
        lines = ['text\n', '\\begin{figure}\n', '\\includegraphics{a}\n',
                 '\\caption{cap}\n', '\\end{figure}\n', 'more\n']
        caption = {'page': 1, 'h': 100, 'v': 100, 'width': 80, 'height': 10}
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=6)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[caption]):
            result = mapper.resolve_block(
                FakeDocument(), '/p/main.pdf', 0, 140, 60,
                active_lines=lines, image_rects=[(90, 30, 190, 88)])
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (2, 5))
        self.assertEqual(result['env'], 'figure')
        self.assertEqual(result['rect'], (90, 30, 190, 100))

    def test_fallback_no_covering_block_returns_none(self):
        # 降级候选块矩形都不含点击点 → 不吸附（纯文本点击）。
        rects = [{'page': 1, 'h': 10, 'v': 100, 'width': 40, 'height': 10}]
        result = self.resolve([pos(line=2)], rects, x=500, y=500)
        self.assertIsNone(result)

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


class GlyphRowsTest(unittest.TestCase):

    def test_merge_overlapping_rows(self):
        rects = [(10, 10, 20, 20), (15, 12, 30, 22), (40, 30, 50, 40)]
        self.assertEqual(mapper._glyph_rows(rects),
                         [(10, 10, 30, 22), (40, 30, 50, 40)])

    def test_zero_height_and_inverted_boxes_skipped(self):
        # 倒置盒跳过；零高标点盒保留为独立行（聚行时靠 0.5 容差并入）。
        rects = [(5, 30, 8, 30), (0, 5, 1, 4), (10, 10, 20, 20)]
        self.assertEqual(mapper._glyph_rows(rects),
                         [(10, 10, 20, 20), (5, 30, 8, 30)])

    def test_unsorted_input_sorted_by_y(self):
        rects = [(40, 30, 50, 40), (10, 10, 20, 20)]
        self.assertEqual(mapper._glyph_rows(rects),
                         [(10, 10, 20, 20), (40, 30, 50, 40)])


class GlyphBandTest(unittest.TestCase):

    RECTS = [(10, 10, 100, 20),          # 上文文本行
             (50, 32, 150, 42), (60, 46, 140, 56),   # 公式两行（隙 4）
             (200, 58, 260, 68),         # 垂直隙 2 但水平不重叠的旁行
             (50, 80, 150, 90)]          # 隙 24 的下一块

    def test_seed_expands_connected_rows_only(self):
        band = mapper._glyph_band(self.RECTS, 100, 37)
        self.assertEqual(band, (48, 30, 152, 58))

    def test_large_gap_stops_expansion(self):
        band = mapper._glyph_band(self.RECTS, 100, 85)
        self.assertEqual(band, (48, 78, 152, 92))

    def test_nearest_seed_within_small_gap(self):
        # 公式行隙点击（dy=4）吸到最近行。
        band = mapper._glyph_band(self.RECTS, 100, 60)
        self.assertEqual(band, (48, 30, 152, 58))

    def test_far_click_returns_none(self):
        self.assertIsNone(mapper._glyph_band(self.RECTS, 100, 62))
        self.assertIsNone(mapper._glyph_band(self.RECTS, 500, 500))
        self.assertIsNone(mapper._glyph_band([], 10, 10))

    def test_single_row_terminates(self):
        # 回归：种子行每轮都满足合并条件，不比较 band 变化会死循环。
        band = mapper._glyph_band([(10, 10, 20, 20)], 15, 15)
        self.assertEqual(band, (8, 8, 22, 22))


def vbox(x1, y1, x2, y2, page=1):
    '''top-down 矩形 → synctex view 记录（v 为 y-up 底边）。'''
    return {'page': page, 'h': x1, 'v': y2, 'width': x2 - x1, 'height': y2 - y1}


class RowAnchorLineTest(unittest.TestCase):

    LINES = ['intro\n', '\\begin{equation}\n', 'x=1\n',
             '\\end{equation}\n', 'outro\n']
    ROW = (10, 10, 100, 20)

    def run_anchor(self, position, rects):
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               return_value=position), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=rects):
            return mapper._row_anchor_line(self.ROW, self.LINES, 0,
                                           '/p/main.tex', '/p/main.pdf', '/cache')

    def test_geometry_verified_anchor(self):
        self.assertEqual(self.run_anchor(pos(line=1), [vbox(10, 10, 100, 20)]), 1)

    def test_geometry_mismatch_rejected(self):
        # edit 落到邻块：view 盒与字形行不吻合 → 不认锚。
        self.assertIsNone(self.run_anchor(pos(line=1), [vbox(10, 60, 100, 70)]))

    def test_block_line_rejected(self):
        self.assertIsNone(self.run_anchor(pos(line=2), [vbox(10, 10, 100, 20)]))

    def test_query_failure_rejected(self):
        self.assertIsNone(self.run_anchor(None, []))

    def test_no_output_line_rejected_even_if_geometry_matches(self):
        # 回归：\label 行的 view 记录是周围内容盒集合（实测全页 mega
        # 盒含邻块公式盒），几何校验会误认锚导致步进对应脱轨。
        lines = ['intro\n', '\\label{eq:x}\n', 'outro\n']
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               return_value=pos(line=2)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[vbox(10, 10, 100, 20)]):
            result = mapper._row_anchor_line(self.ROW, lines, 0,
                                             '/p/main.tex', '/p/main.pdf',
                                             '/cache')
        self.assertIsNone(result)


class VisualBlockSourceTest(unittest.TestCase):
    '''页面：intro 行 / equation 两行 band / mid 行 / align 行 band。'''

    LINES = ['intro\n', '\\begin{equation}\n', 'x=1\n', '\\end{equation}\n',
             'mid\n', '\\begin{align}\n', 'y=2\n', '\\end{align}\n', 'outro\n']
    RECTS = [(10, 10, 100, 20), (50, 32, 150, 42), (60, 46, 140, 56),
             (10, 70, 60, 80), (50, 92, 150, 102), (10, 120, 100, 130)]
    EQ_BAND = (48, 30, 152, 58)
    ALIGN_BAND = (48, 90, 152, 104)

    def edit(self, pdf, page, x, y, directory):
        return pos(line=1 if y < 25 else (5 if y < 85 else 3))

    def view(self, tex, pdf, ln, directory):
        if ln == 1:
            return [vbox(10, 10, 100, 20)]
        if ln == 5:
            return [vbox(10, 70, 60, 80)]
        return []

    def run_source(self, band, view=None):
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               side_effect=self.edit), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=view or self.view):
            return mapper._visual_block_source(band, self.RECTS, self.LINES, 0,
                                               '/p/main.tex', '/p/main.pdf',
                                               '/cache')

    def test_band_maps_to_own_block(self):
        self.assertEqual(self.run_source(self.EQ_BAND),
                         (2, 4, 'environment', 'equation'))
        self.assertEqual(self.run_source(self.ALIGN_BAND),
                         (6, 8, 'environment', 'align'))

    def test_walk_skips_intermediate_band(self):
        # mid 行锚不可靠（view 无自身盒）：从 intro 锚步进，跳过
        # equation band 与 mid 行后对应到 align band。
        def view(tex, pdf, ln, directory):
            return [vbox(10, 10, 100, 20)] if ln == 1 else []
        self.assertEqual(self.run_source(self.ALIGN_BAND, view=view),
                         (6, 8, 'environment', 'align'))

    def test_source_page_mismatch_returns_none(self):
        # 源码多出一行文本（页面换行等失配）→ 交回反查行兜底。
        lines = self.LINES[:1] + ['extra\n'] + self.LINES[1:]
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               side_effect=self.edit), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=self.view):
            result = mapper._visual_block_source(self.EQ_BAND, self.RECTS,
                                                 lines, 0, '/p/main.tex',
                                                 '/p/main.pdf', '/cache')
        self.assertIsNone(result)

    def test_no_verified_anchor_returns_none(self):
        self.assertIsNone(self.run_source(self.EQ_BAND, view=lambda *a: []))


class ResolveBlockVisualTest(unittest.TestCase):

    LINES = VisualBlockSourceTest.LINES
    RECTS = VisualBlockSourceTest.RECTS
    EQ_BAND = VisualBlockSourceTest.EQ_BAND

    def resolve(self, positions, x=100, y=37):
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  side_effect=positions), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=VisualBlockSourceTest().view):
            return mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0, x, y,
                                        active_lines=self.LINES,
                                        glyph_rects=self.RECTS)

    def test_wrong_pos_block_corrected_by_order(self):
        # 实测：公式内容 edit 反查落邻块（align 行）；顺序步进对应
        # 纠正为点击 band 真正所属的 equation 块。
        result = self.resolve([pos(line=7), pos(line=1), pos(line=3), pos(line=3)])
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (2, 4))
        self.assertEqual(result['rect'], self.EQ_BAND)

    def test_pos_line_fallback_uses_order(self):
        # 反查落块外文本行：降级搜索优先用步进对应的块（第三次
        # 反查是降级路径里锚行校验；后两次是编号拆分的正文行校验）。
        result = self.resolve([pos(line=1), pos(line=1), pos(line=1),
                               pos(line=3), pos(line=3)])
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (2, 4))
        self.assertEqual(result['rect'], self.EQ_BAND)

    def test_without_glyphs_keeps_synctex_path(self):
        # 无字形预提取（旧调用方）：仍走 synctex 并集路径。
        content = vbox(100, 90, 180, 100)
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  return_value=pos(line=3)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[content]):
            result = mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0,
                                          140, 95, active_lines=self.LINES)
        self.assertIsNotNone(result)
        self.assertEqual(result['rect'], (100, 90, 180, 100))


class ParagraphRangeTest(unittest.TestCase):

    LINES = ['para one\n', 'para two\n', '\n', '% comment\n', 'other\n',
             '\\label{eq:x}\n', 'after\n', '\\begin{equation}\n', 'x=1\n',
             '\\end{equation}\n']

    def test_consecutive_text_lines(self):
        self.assertEqual(mapper._paragraph_range(self.LINES, 2), (1, 2))

    def test_blank_comment_and_label_break_paragraph(self):
        # 空行/注释/\label（无输出行）都断开段落：label 若计入会把
        # edit 失真落它的公式/标题行误并入段落矩形。
        self.assertEqual(mapper._paragraph_range(self.LINES, 5), (5, 5))
        self.assertEqual(mapper._paragraph_range(self.LINES, 7), (7, 7))

    def test_non_paragraph_line_returns_none(self):
        for ln in (3, 4, 6, 8, 10):
            self.assertIsNone(mapper._paragraph_range(self.LINES, ln))


class RowSourceByGeometryTest(unittest.TestCase):
    '''edit 失真且自身盒缺失时：扫描附近行 view 记录找几何吻合盒。'''

    LINES = ['text one\n', '\\label{eq:x}\n', 'text two\n',
             '\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']
    ROW = (10, 10, 100, 20)

    def run_scan(self, pos_line, view, lines=None):
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               side_effect=view):
            return mapper._row_source_by_geometry(
                self.ROW, lines or self.LINES, pos_line, 0,
                '/p/main.tex', '/p/main.pdf', '/cache')

    def test_finds_line_whose_view_record_matches(self):
        # edit 失真落 label 行（2）：行盒在邻近文本行 3 的记录里。
        def view(tex, pdf, ln, directory):
            return [vbox(10, 10, 100, 20)] if ln == 3 else []
        self.assertEqual(self.run_scan(2, view), 3)

    def test_no_output_and_block_lines_skipped(self):
        # label 行（2）与公式内容行（5）即使盒吻合也不认。
        def view(tex, pdf, ln, directory):
            return [vbox(10, 10, 100, 20)] if ln in (2, 5) else []
        self.assertIsNone(self.run_scan(2, view))

    def test_no_match_returns_none(self):
        self.assertIsNone(self.run_scan(2, lambda *a: []))

    def test_outside_window_not_scanned(self):
        lines = ['t%d\n' % i for i in range(200)]

        def view(tex, pdf, ln, directory):
            return [vbox(10, 10, 100, 20)] if ln == 150 else []
        self.assertIsNone(self.run_scan(10, view, lines=lines))


class ParagraphPdfRectTest(unittest.TestCase):

    RECTS = [(10, 10, 100, 20), (10, 24, 120, 34), (10, 38, 90, 48),
             (50, 80, 150, 90)]   # 末行为段外公式行

    def edit(self, pdf, page, x, y, directory):
        if y < 22:
            return pos(line=1)
        if y < 36:
            return pos(line=99)   # 中间行反查失真（落邻块）
        if y < 60:
            return pos(line=3)
        return pos(line=6)

    def run_rect(self, x=50, y=15, para=(1, 3)):
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               side_effect=self.edit):
            return mapper._paragraph_pdf_rect(self.RECTS, x, y, para, 0,
                                              '/p/main.tex', '/p/main.pdf',
                                              '/cache')

    def test_hole_filled_by_matched_span(self):
        # 中间行反查失真留洞：匹配行垂直跨度夹逼补洞，x 取跨度内行并集。
        self.assertEqual(self.run_rect(), (8, 8, 122, 50))

    def test_no_seed_returns_none(self):
        self.assertIsNone(self.run_rect(x=500, y=500))

    def test_no_matched_rows_returns_none(self):
        self.assertIsNone(self.run_rect(para=(20, 30)))


class ResolveParagraphTest(unittest.TestCase):
    '''普通正文点击：band 不归属视觉块 → 吸附源码段落而非邻块。'''

    LINES = ['intro one\n', 'intro two\n', '\\begin{equation}\n', 'x=1\n',
             '\\end{equation}\n', 'tail\n']
    RECTS = [(10, 10, 100, 20), (10, 24, 120, 34),
             (50, 60, 150, 70), (60, 74, 140, 84), (10, 120, 100, 130)]

    def edit(self, pdf, page, x, y, directory):
        if y < 22:
            return pos(line=1)
        if y < 40:
            return pos(line=2)
        if y < 100:
            return pos(line=4)
        return pos(line=6)

    def resolve(self, x, y):
        with mock.patch.object(mapper.synctex_query, 'synctex_directory_for_source',
                               return_value='/cache'), \
                mock.patch.object(mapper.synctex_query, 'query_source_position',
                                  side_effect=self.edit), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[]):
            return mapper.resolve_block(FakeDocument(), '/p/main.pdf', 0, x, y,
                                        active_lines=self.LINES,
                                        glyph_rects=self.RECTS)

    def test_body_click_selects_paragraph(self):
        result = self.resolve(50, 29)
        self.assertIsNotNone(result)
        self.assertEqual(result['kind'], 'paragraph')
        self.assertEqual((result['start_line'], result['end_line']), (1, 2))
        self.assertEqual(result['rect'], (8, 8, 122, 36))

    def test_block_click_still_wins_over_paragraph(self):
        # 块归属优先：公式 band 点击不得被段落路径抢占。
        result = self.resolve(100, 65)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (3, 5))
        self.assertNotEqual(result['kind'], 'paragraph')


class FallbackNoGuessTest(unittest.TestCase):
    '''band 不归属视觉块时不空间猜邻块（实测文本行 band 会被猜错）。'''

    LINES = ['text\n', '\\begin{equation}\n', 'x=1\n', '\\end{equation}\n']
    RECTS = [(10, 10, 100, 20), (50, 34, 150, 44), (60, 48, 140, 58)]

    def test_text_band_not_guessed_to_visual_block(self):
        with mock.patch.object(mapper.synctex_query, 'query_rectangles',
                               return_value=[]), \
                mock.patch.object(mapper, 'block_pdf_rect',
                                  return_value=(8, 8, 102, 22)) as rect_fn:
            result = mapper._fallback_block_at(self.LINES, 1, 0, 50, 15,
                                               '/p/main.tex', '/p/main.pdf',
                                               '/cache', (),
                                               glyph_rects=self.RECTS)
        self.assertIsNone(result)
        rect_fn.assert_not_called()

    def test_without_band_spatial_search_still_works(self):
        # 无字形预提取（band=None）：空间降级搜索保持原行为。
        with mock.patch.object(mapper, 'block_pdf_rect',
                               return_value=(8, 8, 102, 22)):
            result = mapper._fallback_block_at(self.LINES, 1, 0, 50, 15,
                                               '/p/main.tex', '/p/main.pdf',
                                               '/cache', ())
        self.assertEqual(result[:4], (2, 4, 'environment', 'equation'))


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


class ProseRowLineTest(unittest.TestCase):
    '''宽松几何匹配的正文行认定（全宽行右缘差数 pt 仍认）。'''

    LINES = ['intro\n', '\\begin{equation}\n', 'x=1\n',
             '\\end{equation}\n', 'outro\n']
    ROW = (10, 10, 100, 20)

    def run_prose(self, position, rects, lines=None):
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               return_value=position), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=rects):
            return mapper._prose_row_line(self.ROW, lines or self.LINES, 0,
                                          '/p/main.tex', '/p/main.pdf', '/cache')

    def test_loose_geometry_accepts_wider_view_box(self):
        # view 盒右缘比字形并集宽 10pt（2pt 全边匹配会漏认）→ 半重叠仍认。
        self.assertEqual(self.run_prose(pos(line=1), [vbox(10, 10, 110, 20)]), 1)

    def test_vertical_mismatch_rejected(self):
        self.assertIsNone(self.run_prose(pos(line=1), [vbox(10, 60, 100, 70)]))

    def test_block_line_rejected(self):
        self.assertIsNone(self.run_prose(pos(line=2), [vbox(10, 10, 100, 20)]))

    def test_no_output_line_rejected(self):
        lines = ['intro\n', '\\label{eq:x}\n', 'outro\n']
        self.assertIsNone(self.run_prose(pos(line=2), [vbox(10, 10, 100, 20)],
                                         lines=lines))


class NumberedEquationRectTest(unittest.TestCase):
    '''相邻 equation 连通 band 的编号盒逐式拆分。'''

    LINES = ['\\begin{equation}\n', 'a=1\n', '\\end{equation}\n',
             '\\begin{equation}\n', 'b=2\n', '\\end{equation}\n', 'tail\n']
    # 两式 Tall 盒垂直重叠聚成一行；编号盒右缘隔离；正文行隙 8 连通。
    RECTS = [(60, 30, 70, 44), (50, 32, 150, 42),
             (200, 34, 204, 44), (204, 34, 208, 44), (208, 34, 212, 44),
             (60, 43, 70, 57), (50, 46, 150, 56),
             (200, 48, 204, 58), (204, 48, 208, 58), (208, 48, 212, 58),
             (10, 66, 212, 76)]

    def edit(self, pdf, page, x, y, directory):
        return pos(line=2 if y < 60 else 7)

    def view(self, tex, pdf, ln, directory):
        return [vbox(10, 66, 212, 76)] if ln == 7 else []

    def run_rect(self, x, y, view=None):
        band = mapper._glyph_band(self.RECTS, x, y)
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               side_effect=self.edit), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=view or self.view):
            return mapper._numbered_equation_rect(
                band, self.RECTS, x, y, self.LINES, 0,
                '/p/main.tex', '/p/main.pdf', '/cache')

    def test_click_second_equation_tight(self):
        self.assertEqual(self.run_rect(100, 50), (48, 41, 214, 60))

    def test_click_first_equation_tight(self):
        self.assertEqual(self.run_rect(100, 36), (48, 28, 214, 46))

    def test_click_tall_box_uses_glyph_group(self):
        # 根号 Tall 盒点击：按命中字形归属（盒底离本式编号近）。
        self.assertEqual(self.run_rect(65, 47), (48, 41, 214, 60))

    def test_prose_row_excluded_from_group(self):
        # 正文行虽连通进 band，但不得混入公式组（y2 止于 58+2）。
        self.assertEqual(self.run_rect(100, 50)[3], 60)

    def test_single_number_returns_none(self):
        rects = self.RECTS[:5]
        band = mapper._glyph_band(rects, 100, 36)
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               side_effect=self.edit), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  side_effect=self.view):
            self.assertIsNone(mapper._numbered_equation_rect(
                band, rects, 100, 36, self.LINES, 0,
                '/p/main.tex', '/p/main.pdf', '/cache'))


class ResolveNumberedSplitTest(unittest.TestCase):
    '''resolve_block 端到端：连通 band 的 equation 点击返回逐式紧框。'''

    LINES = NumberedEquationRectTest.LINES
    RECTS = NumberedEquationRectTest.RECTS[:10]

    def test_equation_click_rect_tight_and_source_own_block(self):
        with mock.patch.object(mapper.synctex_query, 'query_source_position',
                               return_value=pos(line=5)), \
                mock.patch.object(mapper.synctex_query, 'query_rectangles',
                                  return_value=[]):
            result = mapper.resolve_block(
                FakeDocument(), '/p/main.pdf', 0, 100, 50,
                active_lines=self.LINES, glyph_rects=self.RECTS)
        self.assertIsNotNone(result)
        self.assertEqual((result['start_line'], result['end_line']), (4, 6))
        self.assertEqual(result['env'], 'equation')
        self.assertEqual(result['rect'], (48, 41, 214, 60))


if __name__ == '__main__':
    unittest.main()
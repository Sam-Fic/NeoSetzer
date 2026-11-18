#!/usr/bin/env python3
# coding: utf-8
#
# phitex session compare --json 的解析、bbox → 预览矩形坐标换算、AI 基线文件。
#
# 坐标换算的硬约束：compare 的 bbox 是 [x0,y0,x1,y1]（points、页左上原点、
# y 向下），预览的 synctex 惯例是 v = 底边距页顶的距离——两条坐标系的页顶
# 是同一参照物，所以 v 恰好等于 y1，高 top = v - height = y0。换算错了，
# diff 高亮会上下颠倒或整段偏移。

import json
import os
import tempfile
import unittest

from setzer.helpers import phitex_compare


def make_payload(changes=None, **overrides):
    '''贴近 compare.rs changes_for 真实 schema 的样例。'''
    payload = {
        'rev': 'the files as they are',
        'label': 'baseline → the files as they are',
        'pdf': '/tmp/proj/.phitex/compare/main-diff.pdf',
        'tex': '/tmp/proj/.phitex/compare/tex',
        'pages': 2,
        'changes': changes if changes is not None else [],
        'errors': [],
        'ms': 12.5,
    }
    payload.update(overrides)
    return payload


class ParseCompareJsonTest(unittest.TestCase):

    def test_single_object_with_noise_lines(self):
        payload = make_payload()
        stdout = 'some preamble line\n{}\ntrailing text'.format(json.dumps(payload))
        self.assertEqual(phitex_compare.parse_compare_json(stdout), payload)

    def test_error_object_parsed(self):
        stdout = '{"error": "no version to compare with: give a revision", "status": 4}'
        payload = phitex_compare.parse_compare_json(stdout)
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get('status'), 4)

    def test_empty_and_garbage(self):
        self.assertIsNone(phitex_compare.parse_compare_json(''))
        self.assertIsNone(phitex_compare.parse_compare_json(None))
        self.assertIsNone(phitex_compare.parse_compare_json('no json here'))
        # 第一行是坏 JSON、第二行是好的：跳过坏行。
        stdout = '{broken\n{"pages": 1, "changes": []}'
        payload = phitex_compare.parse_compare_json(stdout)
        self.assertEqual(payload.get('pages'), 1)

    def test_multi_line_json_not_matched(self):
        # _first_json_object 语义：只认单行整体 loads 的对象。跨行 JSON 不认。
        stdout = '{\n  "pages": 1\n}'
        self.assertIsNone(phitex_compare.parse_compare_json(stdout))


class CompareFailedTest(unittest.TestCase):

    def test_error_without_changes_is_failure(self):
        payload = {'error': 'no phitex watch runs here', 'status': 3}
        self.assertTrue(phitex_compare.compare_failed(payload))

    def test_tex_errors_with_changes_are_not_failure(self):
        # diff 构建有 TeX 错误：exit 1，但 payload 仍带 changes（定位全 null）。
        payload = make_payload(errors=['main.tex:12: Undefined control sequence'],
                               pdf=None, pages=None)
        self.assertFalse(phitex_compare.compare_failed(payload))

    def test_none_is_failure(self):
        self.assertTrue(phitex_compare.compare_failed(None))


class ChangesToRectanglesTest(unittest.TestCase):

    def test_y_flip_and_fields(self):
        changes = [{'n': 1, 'kind': 'add', 'page': 1,
                    'bbox': [61.0, 681.53, 254.92, 692.61],
                    'file': 'main.tex', 'line': 9,
                    'old_text': '', 'new_text': 'added', 'section': 'One'}]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(len(rects), 1)
        rect = rects[0]
        # v = 底边距页顶 = y1（y 向下系里的下边）。
        self.assertAlmostEqual(rect['v'], 692.61)
        self.assertAlmostEqual(rect['h'], 61.0)
        self.assertAlmostEqual(rect['width'], 254.92 - 61.0)
        self.assertAlmostEqual(rect['height'], 692.61 - 681.53)
        self.assertEqual(rect['page'], 1)
        self.assertEqual(rect['kind'], 'add')
        self.assertEqual(rect['n'], 1)

    def test_null_page_or_bbox_skipped(self):
        changes = [
            {'n': 1, 'kind': 'del', 'page': None, 'bbox': None,
             'file': 'main.tex', 'line': 3, 'old_text': 'x', 'new_text': ''},
            {'n': 2, 'kind': 'add', 'page': 2, 'bbox': None,
             'file': 'main.tex', 'line': 4, 'old_text': '', 'new_text': 'y'},
            {'n': 3, 'kind': 'add', 'page': None, 'bbox': [1, 2, 3, 4],
             'file': 'main.tex', 'line': 5, 'old_text': '', 'new_text': 'z'},
        ]
        self.assertEqual(phitex_compare.changes_to_rectangles(changes), [])

    def test_malformed_entries_skipped(self):
        changes = [
            'not a dict',
            {'n': 2, 'kind': 'add', 'page': 1, 'bbox': [0, 0], 'file': 'm'},
            {'n': 3, 'kind': 'add', 'page': 1, 'bbox': 'bad', 'file': 'm'},
            {'n': 4, 'kind': 'add', 'page': 1, 'bbox': [5, 5, 2, 9], 'file': 'm'},  # x1<=x0
            {'n': 5, 'kind': 'add', 'page': True, 'bbox': [1, 2, 3, 4], 'file': 'm'},  # bool 冒充页号
            {'n': 6, 'kind': 'add', 'page': 1, 'bbox': [1, 2, 3, 4], 'file': 'm'},  # 合法
        ]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(len(rects), 1)
        self.assertEqual(rects[0]['n'], 6)

    def test_missing_kind_defaults_to_change(self):
        changes = [{'n': 1, 'page': 1, 'bbox': [1, 2, 3, 4], 'file': 'm'}]
        rects = phitex_compare.changes_to_rectangles(changes)
        self.assertEqual(rects[0]['kind'], 'change')

    def test_empty_and_none(self):
        self.assertEqual(phitex_compare.changes_to_rectangles([]), [])
        self.assertEqual(phitex_compare.changes_to_rectangles(None), [])


class BaselineTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.doc_dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_roundtrip(self):
        path = phitex_compare.write_baseline('hello\nworld', self.doc_dir, 'main.tex')
        self.assertIsNotNone(path)
        self.assertTrue(path.endswith(os.path.join('.neosetzer', 'ai-baseline', 'main.tex')))
        self.assertEqual(phitex_compare.read_baseline(path), 'hello\nworld')
        self.assertTrue(phitex_compare.baseline_matches('hello\nworld', path))
        self.assertFalse(phitex_compare.baseline_matches('changed', path))

    def test_overwrite(self):
        first = phitex_compare.write_baseline('v1', self.doc_dir, 'main.tex')
        second = phitex_compare.write_baseline('v2', self.doc_dir, 'main.tex')
        self.assertEqual(first, second)
        self.assertEqual(phitex_compare.read_baseline(second), 'v2')

    def test_read_missing_is_none(self):
        self.assertIsNone(phitex_compare.read_baseline(
            os.path.join(self.doc_dir, 'nope', 'missing.tex')))

    def test_clear_tolerant(self):
        path = phitex_compare.write_baseline('x', self.doc_dir, 'main.tex')
        phitex_compare.clear_baseline(self.doc_dir, 'main.tex')
        self.assertFalse(os.path.exists(path))
        # 再删一次（不存在）不抛异常。
        phitex_compare.clear_baseline(self.doc_dir, 'main.tex')


if __name__ == '__main__':
    unittest.main()

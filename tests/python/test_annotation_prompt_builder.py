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

'''annotation_prompt_builder 单测（纯逻辑，gi-free）。

覆盖精度契约的关键承诺：
  - preamble 声明 verbatim / 精确 1-based 行号 / 不要重读定位；
  - 小文件（≤ WHOLE_FILE_MAX_LINES）全贴带行号 + 批注起点标记；
  - 大文件贴 ±CONTEXT_RADIUS 并集（重叠合并、 distant 分段）；
  - MAX_SOURCE_CHARS 截断保护；
  - 单条直发与批量同格式；多文件按文件分组。
'''

import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from setzer.ai_fix import annotation_prompt_builder as apb
from setzer.ai_fix.presets import ANNOTATION_PRESETS, default_annotation_presets


def make_file(directory, name, lines):
    path = os.path.join(directory, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def ann(filename, start, end, text='selected text', comment='', preset=None):
    return {'filename': filename, 'start_line': start, 'end_line': end,
            'text': text, 'comment': comment, 'preset': preset}


class PreambleTest(unittest.TestCase):

    def test_empty_annotations_returns_empty_string(self):
        self.assertEqual(apb.build_prompt_for_annotations([], '/tmp'), '')

    def test_preamble_contains_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello'])
            prompt = apb.build_prompt_for_annotations(
                [ann(path, 1, 1)], tmp, interpreter='xelatex')
        self.assertIn('Working directory: {}'.format(tmp), prompt)
        self.assertIn('xelatex', prompt)
        self.assertIn('verbatim', prompt)
        self.assertIn('1-based', prompt)
        self.assertIn('Do NOT re-read', prompt)
        self.assertIn('Absolute path: {}'.format(path), prompt)


class SmallFileTest(unittest.TestCase):

    def test_whole_file_posted_with_line_numbers_and_marker(self):
        lines = ['line {}'.format(i) for i in range(1, 31)]
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', lines)
            prompt = apb.build_prompt_for_annotations(
                [ann(path, 5, 6, text='line 5\nline 6')], tmp)
        self.assertIn('Full file content (with exact line numbers):', prompt)
        # 全文件 30 行都带行号出现
        self.assertIn('    1: line 1', prompt)
        self.assertIn('   30: line 30', prompt)
        self.assertIn('    5: line 5  <-- annotation 1 starts here', prompt)
        # 选区 verbatim 块
        self.assertIn('Annotated passage (verbatim):', prompt)
        self.assertIn('```latex\nline 5\nline 6\n```', prompt)
        self.assertIn('### Annotation 1 (lines 5-6)', prompt)

    def test_threshold_boundary_inclusive(self):
        lines = ['x'] * apb.WHOLE_FILE_MAX_LINES
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', lines)
            prompt = apb.build_prompt_for_annotations([ann(path, 1, 1)], tmp)
        self.assertIn('Full file content', prompt)

    def test_request_combines_preset_and_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello'])
            prompt = apb.build_prompt_for_annotations(
                [ann(path, 1, 1, comment='make it shorter', preset='Improve writing')], tmp)
        instruction = ANNOTATION_PRESETS[0]['instruction']
        self.assertIn(instruction, prompt)
        self.assertIn('Additional user request: make it shorter', prompt)

    def test_no_preset_no_comment_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello'])
            prompt = apb.build_prompt_for_annotations([ann(path, 1, 1)], tmp)
        self.assertIn('Review the annotated passage', prompt)

    def test_custom_presets_are_used(self):
        presets = [{'name': 'Translate', 'instruction': 'Translate to English.'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['bonjour'])
            prompt = apb.build_prompt_for_annotations(
                [ann(path, 1, 1, preset='Translate')], tmp, presets=presets)
        self.assertIn('Translate to English.', prompt)
        # 内置预设不应混入
        self.assertNotIn(ANNOTATION_PRESETS[0]['instruction'], prompt)


class LargeFileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lines = ['line {}'.format(i) for i in range(1, 501)]
        self.path = make_file(self.tmp.name, 'big.tex', self.lines)

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_radius_applied(self):
        prompt = apb.build_prompt_for_annotations(
            [ann(self.path, 100, 102)], self.tmp.name)
        self.assertIn('Relevant source context', prompt)
        start = 100 - apb.CONTEXT_RADIUS
        end = 102 + apb.CONTEXT_RADIUS
        self.assertIn('{:>5}: line {}'.format(start, start), prompt)
        self.assertIn('{:>5}: line {}'.format(end, end), prompt)
        # 区间外的行不出现（带行号前缀精确匹配）
        self.assertNotIn('{:>5}: line {}'.format(start - 1, start - 1), prompt)
        self.assertNotIn('{:>5}: line {}'.format(end + 1, end + 1), prompt)

    def test_overlapping_ranges_merged(self):
        prompt = apb.build_prompt_for_annotations(
            [ann(self.path, 100, 101), ann(self.path, 110, 111)], self.tmp.name)
        # 两个 ±10 窗口重叠 → 只有一个上下文代码块
        context_part = prompt.split('Relevant source context')[1]
        self.assertEqual(context_part.count('```latex'), 1)
        self.assertIn('<-- annotation 1 starts here', context_part)
        self.assertIn('<-- annotation 2 starts here', context_part)

    def test_distant_ranges_stay_separate(self):
        prompt = apb.build_prompt_for_annotations(
            [ann(self.path, 50, 51), ann(self.path, 400, 401)], self.tmp.name)
        context_part = prompt.split('Relevant source context')[1]
        self.assertEqual(context_part.count('```latex'), 2)

    def test_truncation_guard(self):
        # 长行 + 大量远距批注 → 上下文超 MAX_SOURCE_CHARS，出现截断说明
        long_lines = ['line {} '.format(i) + 'x' * 60 for i in range(1, 501)]
        path = make_file(self.tmp.name, 'long.tex', long_lines)
        annotations = [ann(path, i, i) for i in range(30, 500, 25)]
        prompt = apb.build_prompt_for_annotations(annotations, self.tmp.name)
        self.assertIn('truncated', prompt)
        context_part = prompt.split('Relevant source context')[1]
        self.assertLess(len(context_part), apb.MAX_SOURCE_CHARS + 2000)

    def test_missing_file_falls_back_gracefully(self):
        prompt = apb.build_prompt_for_annotations(
            [ann(os.path.join(self.tmp.name, 'gone.tex'), 1, 2)], self.tmp.name)
        self.assertIn('source context unavailable', prompt)


class GroupingAndSelectionTest(unittest.TestCase):

    def test_multiple_files_grouped(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = make_file(tmp, 'a.tex', ['alpha'])
            b = make_file(tmp, 'b.tex', ['beta'])
            prompt = apb.build_prompt_for_annotations(
                [ann(a, 1, 1), ann(b, 1, 1), ann(a, 1, 1)], tmp)
        self.assertEqual(prompt.count('## File: a.tex'), 1)
        self.assertEqual(prompt.count('## File: b.tex'), 1)
        # a.tex 两条批注编号连续
        self.assertIn('### Annotation 2', prompt)

    def test_single_selection_same_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello world'])
            single = apb.build_prompt_for_selection(
                path, 1, 1, 'hello world', tmp, comment='fix', preset='Explain')
            batch = apb.build_prompt_for_annotations(
                [ann(path, 1, 1, text='hello world', comment='fix', preset='Explain')], tmp)
        self.assertEqual(single, batch)

    def test_single_line_range_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello'])
            prompt = apb.build_prompt_for_selection(path, 1, 1, 'hello', tmp)
        self.assertIn('### Annotation 1 (lines 1)', prompt)


class PresetsTest(unittest.TestCase):

    def test_default_presets_deep_copy(self):
        presets = default_annotation_presets()
        presets[0]['name'] = 'mutated'
        self.assertNotEqual(ANNOTATION_PRESETS[0]['name'], 'mutated')


class FocusTest(unittest.TestCase):
    '''PDF 派生批注：整行/整块仅作上下文，单独强调用户实际选中部分。'''

    def prompt_with_focus(self, focus_text, focus_kind):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['alpha beta gamma'])
            a = ann(path, 1, 1, text='alpha beta gamma')
            a['focus_text'] = focus_text
            a['focus_kind'] = focus_kind
            return apb.build_prompt_for_annotations([a], tmp)

    def test_source_focus_rendered_after_verbatim(self):
        prompt = self.prompt_with_focus('beta', 'source')
        self.assertIn('The passage above is the full line/block, provided as '
                      'context. The user specifically highlighted:', prompt)
        self.assertIn('```latex\nbeta\n```', prompt)
        self.assertNotIn('rendered PDF text', prompt)
        # focus 块必须排在整行/整块 verbatim 块之后
        self.assertLess(prompt.index('Annotated passage (verbatim):'),
                        prompt.index('The user specifically highlighted:'))

    def test_pdf_text_focus_notes_rendered_difference(self):
        prompt = self.prompt_with_focus('β rendered', 'pdf-text')
        self.assertIn('(rendered PDF text; may differ from the source)', prompt)
        self.assertIn('```latex\nβ rendered\n```', prompt)

    def test_no_focus_keeps_original_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_file(tmp, 'a.tex', ['hello'])
            prompt = apb.build_prompt_for_annotations([ann(path, 1, 1)], tmp)
        self.assertNotIn('specifically highlighted', prompt)

    def test_blank_focus_is_ignored(self):
        prompt = self.prompt_with_focus('   ', 'source')
        self.assertNotIn('specifically highlighted', prompt)


if __name__ == '__main__':
    unittest.main()
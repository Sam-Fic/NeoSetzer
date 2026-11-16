#!/usr/bin/env python3
# coding: utf-8
#
# 反向同步（Ctrl+点击 PDF -> 源码）的文件名匹配。
#
# synctex 的 Input 会带 `./` 段（`…/Example/./main.tex`），而文档注册的是
# 规整过的绝对路径。原先这里是裸 ==，永远不成立，表现成"点了完全没反应"
# 且界面上没有任何线索——静默 return 的代价就是没法从现象定位。

import ast
import os
import unittest
from pathlib import Path

from setzer.helpers.synctex_line_repair import (
    match_pdf_line,
    normalize,
    repair_line,
    strip_latex,
)

ROOT = Path(__file__).resolve().parents[2]
BUILD_SYSTEM = 'setzer/document/build_system/build_system.py'


def backward_branch_source():
    source = (ROOT / BUILD_SYSTEM).read_text(encoding='utf-8')
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    parse_result = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'parse_result')
    return ast.dump(parse_result), source


class SameFileMatchTest(unittest.TestCase):

    def compare(self, target, current):
        return (current is not None and target is not None
                and os.path.normpath(target) == os.path.normpath(current))

    def test_dot_segment_matches(self):
        self.assertTrue(self.compare('/p/Ex/./main.tex', '/p/Ex/main.tex'))

    def test_plain_paths_match(self):
        self.assertTrue(self.compare('/p/Ex/main.tex', '/p/Ex/main.tex'))

    def test_other_file_does_not_match(self):
        self.assertFalse(self.compare('/p/Ex/./chapters/a.tex', '/p/Ex/main.tex'))

    def test_none_is_not_a_match(self):
        self.assertFalse(self.compare('/p/Ex/main.tex', None))
        self.assertFalse(self.compare(None, '/p/Ex/main.tex'))

    def test_branch_normalizes_instead_of_bare_equality(self):
        '''源码层面钉住：不许退回裸 ==（回归时现象无迹可寻）。'''
        _, source = backward_branch_source()
        index = source.index("elif result_blob['backward_sync'] != None:")
        block = source[index:index + 1600]
        self.assertIn('os.path.normpath(target)', block)
        self.assertIn('os.path.normpath(current)', block)
        self.assertNotIn("['filename'] == self.document.get_filename()", block)

    def test_is_root_branch_still_delegates_to_workspace(self):
        '''另一条分支靠 open_document_by_filename，其内部已 normpath。'''
        _, source = backward_branch_source()
        index = source.index("elif result_blob['backward_sync'] != None:")
        block = source[index:index + 1600]
        self.assertIn('open_document_by_filename', block)


class WorkspaceLookupTest(unittest.TestCase):
    '''open_document_by_filename 那条路本来就规整，确认没被我改坏。'''

    def test_lookup_normalizes(self):
        source = (ROOT / 'setzer/workspace/workspace.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'get_document_by_filename')
        self.assertIn('normpath', ast.dump(fn))


class PhitexLineRepairTest(unittest.TestCase):
    '''phitex 的 synctex 行号成片错位，按点击处的 PDF 文本纠正。

    下面的行号与文本都取自用户实测文档：列表项 74-77 在 phitex 的 synctex
    里全被记成 82，点"实现延迟叠加成像…"会跳到 2.1 节标题。
    '''

    SOURCE_LINES = [
        '\\documentclass{ctexart}',
        '\\begin{document}',
        '\\section{实验目的}',
        '\\begin{enumerate}',
        '  \\item 掌握主动导波监测中散射信号的提取方法（有损伤信号与基准信号作差）。',
        '  \\item 掌握由直达波飞行时间标定波包群速度的方法。',
        '  \\item 实现椭圆法、双曲线法两种基于几何交会的损伤定位算法。',
        '  \\item 实现延迟叠加成像，比较三类方法的定位精度、分辨率与适用条件。',
        '\\end{enumerate}',
        '\\section{实验对象与数据}',
        '\\subsection{试件与传感器布置}',
    ]

    def test_repairs_list_item_attributed_to_later_heading(self):
        self.assertEqual(
            repair_line(10, '实现延迟叠加成像，比较三类方法的定位精度、分辨率与适用条件。',
                        self.SOURCE_LINES),
            7)

    def test_keeps_already_matching_line(self):
        self.assertEqual(
            repair_line(7, '实现延迟叠加成像，比较三类方法的定位精度、分辨率与适用条件。',
                        self.SOURCE_LINES),
            7)

    def test_repairs_heading_without_latex_commands(self):
        self.assertEqual(
            repair_line(4, '2.1 试件与传感器布置', self.SOURCE_LINES), 10)

    def test_short_pdf_text_is_left_alone(self):
        '''页码、图号这类两三个字的文本匹配面太广，改错概率高于改对。'''
        self.assertEqual(repair_line(10, '1', self.SOURCE_LINES), 10)
        self.assertEqual(repair_line(10, '', self.SOURCE_LINES), 10)

    def test_ambiguous_duplicate_lines_are_left_alone(self):
        '''两行文字完全一样时无法判断该跳到哪一行，保持 synctex 的结果。'''
        lines = ['\\section{A}', '相同的正文内容。', '\\section{B}', '相同的正文内容。']
        self.assertEqual(repair_line(0, '相同的正文内容。', lines), 0)

    def test_out_of_range_line_is_returned_unchanged(self):
        self.assertEqual(repair_line(99, '实现延迟叠加成像，比较三类方法的定位精度。',
                                     self.SOURCE_LINES), 99)

    def test_wiring_repairs_only_for_phitex(self):
        '''纠正只对 phitex 的 synctex 生效，别改坏其它引擎的正确行号。'''
        source = (ROOT / BUILD_SYSTEM).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'set_synctex_position')
        self.assertIn('repair_line', ast.dump(fn))
        gate = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_phitex_synctex_lines_unreliable')
        self.assertIn("'phitex'", ast.dump(gate))


class ForwardSyncMatchTest(unittest.TestCase):
    '''正向同步：源码行 → 页面上的 PDF 文本行。

    行号来自 phitex 的 synctex 时可能指向错误位置，但页面文字是对的，于是
    反过来拿源码行文字找 PDF 行。
    '''

    PDF_LINES = [
        '基于主动导波的损伤定位实验',
        '1 实验目的',
        '掌握主动导波监测中散射信号的提取方法（有损伤信号与基准信号作差）。',
        '掌握由直达波飞行时间标定波包群速度的方法。',
        '实现椭圆法、双曲线法两种基于几何交会的损伤定位算法。',
        '实现延迟叠加成像，比较三类方法的定位精度、分辨率与适用条件。',
        '2 实验对象与数据',
        '2.1 试件与传感器布置',
    ]

    def test_matches_list_item(self):
        self.assertEqual(
            match_pdf_line('  \\item 实现延迟叠加成像，比较三类方法的定位精度、'
                           '分辨率与适用条件。', self.PDF_LINES),
            5)

    def test_matches_heading_with_section_command(self):
        self.assertEqual(
            match_pdf_line('\\subsection{试件与传感器布置}', self.PDF_LINES), 7)

    def test_line_without_visible_text_is_not_matched(self):
        '''`\\begin{enumerate}` 在 PDF 里没有对应文字，不能乱指一行。'''
        self.assertIsNone(match_pdf_line('\\begin{enumerate}', self.PDF_LINES))

    def test_ambiguous_duplicate_lines_are_left_alone(self):
        lines = ['相同的正文内容。', '相同的正文内容。']
        self.assertIsNone(match_pdf_line('相同的正文内容。', lines))

    def test_unrelated_page_returns_none(self):
        self.assertIsNone(match_pdf_line('\\section{参考文献}', self.PDF_LINES))

    def test_typographic_punctuation_is_ignored(self):
        '''PDF 里是弯引号，源码里是 ASCII 单引号，不该因此匹配不上。'''
        self.assertEqual(
            match_pdf_line("Einstein's famous relation:",
                           ['Einstein’s famous relation:']),
            0)

    def test_shared_fragment_is_not_a_match(self):
        '''只有 "mple" 这类片段相同时不能当成同一行。'''
        self.assertIsNone(match_pdf_line(
            'A simple bullet list:', ['A Appendix Structure Sample']))

    def test_strip_latex_keeps_visible_text(self):
        self.assertEqual(normalize(strip_latex('\\section{实验目的}')), '实验目的')
        self.assertEqual(normalize(strip_latex('  \\item 实现椭圆法。')), '实现椭圆法。')
        self.assertEqual(normalize(strip_latex('% 注释')), '')

    def test_forward_wiring_uses_text_matching(self):
        '''正向同步的纠正接在预览层，且只由 build_system 在 phitex 下触发。'''
        preview_source = (ROOT / 'setzer/document/preview/preview.py').read_text(
            encoding='utf-8')
        preview_tree = ast.parse(preview_source)
        preview_cls = next(n for n in preview_tree.body
                          if isinstance(n, ast.ClassDef))
        finder = next(n for n in preview_cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == '_find_rectangle_for_source_text')
        self.assertIn('match_pdf_line', ast.dump(finder))

        _, source = backward_branch_source()
        index = source.index("if result_blob['forward_sync'] != None:")
        block = source[index:index + 600]
        self.assertIn('_phitex_synctex_lines_unreliable', block)
        self.assertIn("get('text')", block)


if __name__ == '__main__':
    unittest.main()

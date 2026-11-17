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
from types import SimpleNamespace
from unittest.mock import Mock

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
        '''源码层面钉住：不许退回裸 ==（回归时现象无迹可寻）。
        重构后 normpath 比较统一在 workspace.jump_to_synctex_position。'''
        source = (ROOT / 'setzer/workspace/workspace.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'jump_to_synctex_position')
        dump = ast.dump(fn)
        self.assertIn('normpath', dump)
        # 裸 == 回归钉住：所有 == 比较都必须包在 normpath 调用里
        # （is not None / != None 这类守卫比较不在此列）。
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and any(
                    isinstance(op, ast.Eq) for op in node.ops):
                self.assertTrue(all(isinstance(c, ast.Call) for c in
                                    [node.left] + node.comparators))
        _, bs_source = backward_branch_source()
        index = bs_source.index("elif result_blob['backward_sync'] != None:")
        block = bs_source[index:index + 800]
        self.assertIn('jump_to_synctex_position', block)

    def test_is_root_branch_still_delegates_to_workspace(self):
        '''另一条分支靠 open_document_by_filename，其内部已 normpath。'''
        source = (ROOT / 'setzer/workspace/workspace.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'jump_to_synctex_position')
        self.assertIn('open_document_by_filename', ast.dump(fn))


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
        '''纠正只对 phitex 的 synctex 生效，别改坏其它引擎的正确行号。
        重构后修复逻辑在 correct_synctex_line，set_synctex_position 调它。'''
        source = (ROOT / BUILD_SYSTEM).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'correct_synctex_line')
        self.assertIn('repair_line', ast.dump(fn))
        setter = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == 'set_synctex_position')
        self.assertIn('correct_synctex_line', ast.dump(setter))
        gate = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_phitex_synctex_lines_unreliable')
        self.assertIn("'phitex'", ast.dump(gate))

    def test_wiring_redirects_boundary_lines_both_directions(self):
        '''公式内点击 synctex 常返回 \\end{equation}、光标停在 \\begin{align}
        上正向查询拿到周围段落盒：两个方向的入口都要经 nearest_content_line
        把边界行重定向到块内内容行。'''
        source = (ROOT / BUILD_SYSTEM).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        for name in ('set_synctex_position', 'set_forward_sync_arguments'):
            fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == name)
            self.assertIn('nearest_content_line', ast.dump(fn), name)


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
        '''正向同步的纠正接在预览层：phitex 单盒纠正 + 多盒筛选（align 等
        多行环境 synctex 返回多行的盒），phitex 标志由 build_system 传入。'''
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


class ForwardRectangleGatingTest(unittest.TestCase):
    '''set_synctex_rectangles 的文本筛选门控：多盒必筛（align 两行盒污染），
    单盒仅 phitex 筛（其它引擎 synctex 通常是对的，不动）。'''

    @staticmethod
    def _method():
        source = (ROOT / 'setzer/document/preview/preview.py').read_text(
            encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'set_synctex_rectangles')
        namespace = {'_sync_debug': lambda *a: None}
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     '<preview>', 'exec'), namespace)
        return namespace['set_synctex_rectangles']

    def _fake_preview(self, matched):
        shown = []
        fake = SimpleNamespace(
            layout=object(),
            show_synctex_rectangles=lambda rects: shown.append(rects),
            _scroll_to_rectangle=lambda rect: True,
            add_change_code=lambda code: None,
            _find_rectangle_for_source_text=Mock(return_value=matched))
        return fake, shown

    def test_multi_box_filtered_by_source_text(self):
        # align 两行：synctex 返回两行的盒，用光标行文字筛出正确行。
        matched = {'page': 1, 'h': 290, 'v': 243, 'width': 36, 'height': 8}
        fake, shown = self._fake_preview(matched)
        rects = [{'page': 1, 'h': 133, 'v': 230, 'width': 344, 'height': 12},
                 {'page': 1, 'h': 133, 'v': 245, 'width': 344, 'height': 12}]
        self._method()(fake, rects, 'y &= 3 + 4')
        fake._find_rectangle_for_source_text.assert_called_once()
        self.assertEqual(shown, [[matched]])

    def test_single_box_non_phitex_left_alone(self):
        fake, shown = self._fake_preview(None)
        rects = [{'page': 1, 'h': 133, 'v': 230, 'width': 344, 'height': 12}]
        self._method()(fake, rects, 'some source line')
        fake._find_rectangle_for_source_text.assert_not_called()
        self.assertEqual(shown, [rects])

    def test_single_box_phitex_still_corrected(self):
        matched = {'page': 2, 'h': 10, 'v': 100, 'width': 40, 'height': 10}
        fake, shown = self._fake_preview(matched)
        rects = [{'page': 1, 'h': 133, 'v': 230, 'width': 344, 'height': 12}]
        self._method()(fake, rects, 'some source line', phitex=True)
        fake._find_rectangle_for_source_text.assert_called_once()
        self.assertEqual(shown, [[matched]])

    def test_multi_box_without_source_text_left_alone(self):
        fake, shown = self._fake_preview(None)
        rects = [{'page': 1, 'h': 133, 'v': 230, 'width': 344, 'height': 12},
                 {'page': 1, 'h': 133, 'v': 245, 'width': 344, 'height': 12}]
        self._method()(fake, rects)
        fake._find_rectangle_for_source_text.assert_not_called()
        self.assertEqual(shown, [rects])

    def test_multi_box_no_match_keeps_synctex(self):
        fake, shown = self._fake_preview(None)
        rects = [{'page': 1, 'h': 133, 'v': 230, 'width': 344, 'height': 12},
                 {'page': 1, 'h': 133, 'v': 245, 'width': 344, 'height': 12}]
        self._method()(fake, rects, 'y &= 3 + 4')
        self.assertEqual(shown, [rects])


class BackwardBoundaryRedirectTest(unittest.TestCase):
    '''反向跳转落点：公式内点击 synctex 返回 \\end{equation}（0-based 2），
    应重定向到内容行；换行后字符级 offset 精修不再适用，须丢弃。'''

    LINES = '\\begin{equation}\nE = mc^2\n\\end{equation}'

    def _run(self, position):
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        from setzer.ai_fix import source_blocks

        source = (ROOT / BUILD_SYSTEM).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == 'set_synctex_position')
        namespace = {'source_blocks': source_blocks}
        exec(compile(ast.Module(body=[fn], type_ignores=[]),
                     '<build_system>', 'exec'), namespace)

        buffer = Gtk.TextBuffer()
        buffer.set_text(self.LINES, -1)
        highlighted = []
        document = SimpleNamespace(
            source_buffer=buffer,
            highlight_section=lambda a, b: highlighted.append((a, b)))
        fake = SimpleNamespace(
            correct_synctex_line=lambda line, txt: line,
            get_synctex_word_bounds=lambda text, word, context: None,
            _map_pdf_offset_to_source=Mock(return_value=2))
        namespace['set_synctex_position'](fake, document, position)
        # 必须在 buffer 存活时读光标：PyGObject 的 TextIter 不持有 buffer
        # 引用，函数返回后 buffer 被 GC，带出去的迭代器会悬空读成 0。
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        return cursor.get_line(), cursor.get_line_offset(), fake

    def test_boundary_line_redirects_to_content(self):
        line, _offset, fake = self._run(
            {'line': 2, 'word': 'mc', 'context': 'E = mc2',
             'pdf_line_offset': 3, 'pdf_line_text': 'E = mc2'})
        self.assertEqual(line, 1)
        # 换行后点击处 PDF 行文本不再适用，offset 精修不得参与。
        fake._map_pdf_offset_to_source.assert_not_called()

    def test_content_line_keeps_offset_refinement(self):
        line, offset, fake = self._run(
            {'line': 1, 'word': 'mc', 'context': 'E = mc2',
             'pdf_line_offset': 3, 'pdf_line_text': 'E = mc2'})
        self.assertEqual(line, 1)
        self.assertEqual(offset, 2)
        fake._map_pdf_offset_to_source.assert_called_once()


if __name__ == '__main__':
    unittest.main()

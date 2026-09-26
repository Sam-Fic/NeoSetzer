#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''snippet_wrapper 的纯逻辑测试：preamble 复用、最小兜底、cache_key 确定性。'''

import unittest

from setzer.document.snippet_preview.snippet_wrapper import (
    build_figure_document,
    build_snippet_document,
    cache_key,
    find_begin_document_offset,
)


class BuildSnippetDocumentTest(unittest.TestCase):

    FULL_DOC = (
        '\\documentclass{article}\n'
        '\\usepackage{bm}\n'
        '\\newcommand{\\vec}[1]{\\mathbf{#1}}\n'
        '\\begin{document}\n'
        'Hello $x$ world\n'
        '\\end{document}\n'
    )

    def test_reuses_document_preamble(self):
        wrapped = build_snippet_document('$\\vec{x}$', self.FULL_DOC)
        self.assertTrue(wrapped.startswith(
            '\\documentclass{article}\n\\usepackage{bm}\n'
            '\\newcommand{\\vec}[1]{\\mathbf{#1}}\n\\begin{document}\n'))
        self.assertIn('\\pagestyle{empty}', wrapped)
        self.assertTrue(wrapped.endswith('$\\vec{x}$\n\\end{document}\n'))

    def test_preamble_only_keeps_text_before_begin_document(self):
        wrapped = build_snippet_document('$$y$$', self.FULL_DOC)
        self.assertNotIn('Hello', wrapped)
        self.assertNotIn('world', wrapped)

    def test_empty_math_document_falls_back_to_minimal(self):
        wrapped = build_snippet_document('$x$', None)
        self.assertTrue(wrapped.startswith(
            '\\documentclass{article}\n\\usepackage{amsmath}\n'
            '\\usepackage{amssymb}\n\\begin{document}\n'))
        self.assertTrue(wrapped.endswith('$x$\n\\end{document}\n'))

    def test_document_without_begin_document_falls_back(self):
        # snippet 文件等纯片段：没有 \begin{document}。
        wrapped = build_snippet_document('\\[z\\]', '\\usepackage{bm}\n')
        self.assertIn('\\documentclass{article}', wrapped)
        # 无 preamble 可复用时不得混入用户片段，否则可能产生重复
        # \documentclass 或未闭合环境。
        self.assertNotIn('\\usepackage{bm}', wrapped)

    def test_no_documentclass_in_reused_preamble_is_kept_as_is(self):
        # preamble 已含 documentclass 时不应再注入最小 preamble 的
        # documentclass（build_snippet_document 只截取不合并）。
        wrapped = build_snippet_document('$x$', '\\documentclass{memoir}\n\\begin{document}\nbody\n\\end{document}')
        self.assertNotIn('amssymb', wrapped)
        self.assertIn('\\documentclass{memoir}', wrapped)

    def test_pagestyle_after_begin_document(self):
        # \pagestyle{empty} 必须在 \begin{document} 之后（正文声明），且晚于
        # preamble 中的同名声明，保证页码不会出现在裁剪后的预览里。
        wrapped = build_snippet_document('$x$', '\\pagestyle{plain}\\begin{document}\nbody')
        head, sep, _body = wrapped.partition('\\begin{document}\n')
        self.assertTrue(sep)
        self.assertNotIn('\\pagestyle{empty}', head)
        self.assertTrue(wrapped.split('\\begin{document}\n', 1)[1].startswith('\\pagestyle{empty}\n'))

    def test_math_raw_is_stripped(self):
        wrapped = build_snippet_document('  $x$  \n', None)
        self.assertTrue(wrapped.endswith('$x$\n\\end{document}\n'))


class BuildFigureDocumentTest(unittest.TestCase):

    FIGURE = '\\begin{tikzpicture}\n\\draw (0,0) -- (1,1);\n\\end{tikzpicture}'

    ROOT_TEX = (
        '\\documentclass{article}\n'
        '\\usepackage{tikz}\n'
        '\\usetikzlibrary{arrows.meta}\n'
        '\\begin{document}\n'
        'body\n'
        '\\end{document}\n'
    )

    def test_reuses_root_preamble(self):
        wrapped = build_figure_document(self.FIGURE, self.ROOT_TEX)
        self.assertTrue(wrapped.startswith(
            '\\documentclass{article}\n\\usepackage{tikz}\n'
            '\\usetikzlibrary{arrows.meta}\n\\begin{document}\n'))
        self.assertNotIn('body', wrapped)
        self.assertTrue(wrapped.endswith(
            '\\noindent\n' + self.FIGURE + '\n\\end{document}\n'))

    def test_minimal_fallback_only_knows_tikz(self):
        # 无 preamble 可用时不猜库：漏猜与猜错同样是编译失败。
        wrapped = build_figure_document(self.FIGURE, None)
        self.assertTrue(wrapped.startswith(
            '\\documentclass{article}\n\\usepackage{tikz}\n\\begin{document}\n'))
        self.assertNotIn('amssymb', wrapped)
        self.assertNotIn('\\usetikzlibrary', wrapped)

    def test_root_without_begin_document_falls_back(self):
        wrapped = build_figure_document(self.FIGURE, '\\usepackage{tikz}\n')
        self.assertNotIn('\\usetikzlibrary', wrapped)
        self.assertIn('\\documentclass{article}', wrapped)

    def test_beamer_root_wraps_figure_in_frame(self):
        # beamer body 里裸放 tikzpicture 时 \node[right=of A] 这类依赖自动
        # 命名的写法直接报 No shape named 'A' 且无 PDF；包 frame 后通过。
        root = ('\\documentclass[10pt]{beamer}\n'
                '\\usepackage{tikz}\n'
                '\\begin{document}\n\\end{document}\n')
        wrapped = build_figure_document(self.FIGURE, root)
        self.assertIn('\\begin{frame}\n' + self.FIGURE + '\n\\end{frame}\n', wrapped)
        self.assertNotIn('\\noindent', wrapped)

    def test_beamer_with_options_and_comma_list(self):
        root = ('\\documentclass[aspectratio=169,11pt]{article,beamer}\n'
                '\\begin{document}\n\\end{document}\n')
        wrapped = build_figure_document(self.FIGURE, root)
        self.assertIn('\\begin{frame}', wrapped)

    def test_non_beamer_class_containing_word_is_not_beamer(self):
        root = ('\\documentclass{beamerthemer}\n'
                '\\begin{document}\n\\end{document}\n')
        self.assertNotIn('\\begin{frame}', build_figure_document(self.FIGURE, root))

    def test_empty_page_style_after_begin_document(self):
        root = ('\\documentclass{article}\n\\pagestyle{plain}\n'
                '\\begin{document}\n\\end{document}\n')
        wrapped = build_figure_document(self.FIGURE, root)
        head, sep, body = wrapped.partition('\\begin{document}\n')
        self.assertTrue(sep)
        self.assertNotIn('\\pagestyle{empty}', head)
        self.assertTrue(body.startswith('\\pagestyle{empty}\n\\thispagestyle{empty}\n'))

    def test_figure_raw_is_stripped(self):
        wrapped = build_figure_document('  ' + self.FIGURE + '  \n', None)
        self.assertTrue(wrapped.endswith(
            '\\noindent\n' + self.FIGURE + '\n\\end{document}\n'))


class CacheKeyTest(unittest.TestCase):

    def test_deterministic(self):
        self.assertEqual(cache_key('abc', 'xelatex'), cache_key('abc', 'xelatex'))

    def test_differs_by_engine(self):
        self.assertNotEqual(cache_key('abc', 'xelatex'), cache_key('abc', 'pdflatex'))

    def test_differs_by_content(self):
        self.assertNotEqual(cache_key('$x$', 'xelatex'), cache_key('$y$', 'xelatex'))

    def test_cwd_participates_in_key(self):
        # 片段里的 \includegraphics{figs/x.png} 相对 cwd 解析：两个项目
        # preamble 与片段字节相同而仅图不同时，键必须区分开。
        self.assertNotEqual(cache_key('abc', 'pdflatex', '/p/a'),
                            cache_key('abc', 'pdflatex', '/p/b'))

    def test_none_cwd_matches_legacy_key(self):
        self.assertEqual(cache_key('abc', 'xelatex'),
                         cache_key('abc', 'xelatex', None))
        self.assertEqual(cache_key('abc', 'xelatex'),
                         cache_key('abc', 'xelatex', ''))

    def test_format(self):
        key = cache_key('abc', 'xelatex')
        self.assertEqual(len(key), 16)
        int(key, 16)  # 纯 hex，可安全用作文件名

    def test_find_begin_document_offset(self):
        self.assertEqual(find_begin_document_offset('abc\\begin{document}x'), 3)
        self.assertIsNone(find_begin_document_offset('no marker'))


if __name__ == '__main__':
    unittest.main()

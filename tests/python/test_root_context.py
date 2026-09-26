#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''root_context 的纯逻辑测试：解析顺序、缓存失效、open_sources 优先。'''

import os
import tempfile
import unittest

from setzer.document.snippet_preview.root_context import (
    clear_read_cache,
    resolve_root_context,
)
from setzer.project.build_configuration import ProjectBuildConfiguration


class RootContextTest(unittest.TestCase):

    def setUp(self):
        clear_read_cache()

    def _write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as file_object:
            file_object.write(text)

    def test_self_contained_document_needs_no_disk_io(self):
        called = list()

        def read_text(filename):
            called.append(filename)
            raise AssertionError('自足文档不应触碰磁盘')

        text = '\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n'
        context = resolve_root_context('/tmp/whatever/main.tex', text,
                                       read_text=read_text)
        self.assertEqual(called, list())
        self.assertEqual(context.text, text)
        self.assertEqual(context.filename, '/tmp/whatever/main.tex')
        self.assertEqual(context.build_dir, '/tmp/whatever')
        self.assertEqual(context.signature, ('/tmp/whatever/main.tex', None, len(text)))

    def test_magic_root_is_resolved(self):
        with tempfile.TemporaryDirectory() as project:
            root = os.path.join(project, 'main.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(root, '\\documentclass{article}\n\\begin{document}\n\\end{document}\n')
            self._write(chapter, '% !TeX root = ../main.tex\n\\section{Intro}\n')

            context = resolve_root_context(chapter, open(chapter).read())
            self.assertEqual(context.filename, root)
            self.assertIn('\\documentclass{article}', context.text)
            self.assertEqual(context.build_dir, project)
            self.assertEqual(context.signature[0], root)

    def test_project_configuration_is_the_fallback(self):
        with tempfile.TemporaryDirectory() as project:
            root = os.path.join(project, 'main.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(root, '\\documentclass{article}\n\\begin{document}\n\\end{document}\n')
            self._write(chapter, '\\section{Intro}\n')
            ProjectBuildConfiguration(project).save({'root_document': 'main.tex'})

            context = resolve_root_context(chapter, open(chapter).read())
            self.assertEqual(context.filename, root)
            self.assertEqual(context.build_dir, project)

    def test_magic_root_wins_over_project_configuration(self):
        with tempfile.TemporaryDirectory() as project:
            configured = os.path.join(project, 'main.tex')
            magic = os.path.join(project, 'other.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(configured, '\\documentclass{article}\\begin{document}\\end{document}')
            self._write(magic, '\\documentclass{report}\\begin{document}\\end{document}')
            self._write(chapter, '% !TeX root = ../other.tex\n')
            ProjectBuildConfiguration(project).save({'root_document': 'main.tex'})

            context = resolve_root_context(chapter, open(chapter).read())
            self.assertEqual(context.filename, magic)

    def test_open_sources_text_beats_disk(self):
        with tempfile.TemporaryDirectory() as project:
            root = os.path.join(project, 'main.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(root, '\\documentclass{article}  % on disk\n\\begin{document}\n\\end{document}\n')
            self._write(chapter, '% !TeX root = ../main.tex\n')
            unsaved = '\\documentclass{article}  % unsaved edit\n\\begin{document}\n\\end{document}\n'

            context = resolve_root_context(chapter, open(chapter).read(),
                                           open_sources={root: unsaved})
            self.assertEqual(context.text, unsaved)
            # 未保存文本无 stat 可言：signature 的 mtime/size 位以 None/len 表示，
            # 且必须与磁盘版本不同（失败冷却靠它区分两代内容）。
            self.assertEqual(context.signature, (root, None, len(unsaved)))

    def test_disk_cache_invalidates_on_change(self):
        with tempfile.TemporaryDirectory() as project:
            root = os.path.join(project, 'main.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(root, '\\documentclass{article}\n\\begin{document}\n\\end{document}\n')
            self._write(chapter, '% !TeX root = ../main.tex\n')

            first = resolve_root_context(chapter, '% !TeX root = ../main.tex\n')
            second = resolve_root_context(chapter, '% !TeX root = ../main.tex\n')
            self.assertEqual(first.text, second.text)
            self.assertEqual(first.signature, second.signature)

            self._write(root, '\\documentclass{article}\n\\usepackage{tikz}\n\\begin{document}\n\\end{document}\n')
            third = resolve_root_context(chapter, '% !TeX root = ../main.tex\n')
            self.assertIn('\\usepackage{tikz}', third.text)
            self.assertNotEqual(first.signature, third.signature)

    def test_unresolvable_returns_empty_text(self):
        with tempfile.TemporaryDirectory() as project:
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(chapter, '\\section{Intro}\n')
            context = resolve_root_context(chapter, '\\section{Intro}\n')
            self.assertIsNone(context.filename)
            self.assertEqual(context.text, '')
            self.assertIsNone(context.build_dir)

    def test_magic_root_to_missing_file_falls_through(self):
        with tempfile.TemporaryDirectory() as project:
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(chapter, '% !TeX root = ../ghost.tex\n\\section{Intro}\n')
            context = resolve_root_context(chapter, '% !TeX root = ../ghost.tex\n')
            self.assertIsNone(context.filename)
            self.assertEqual(context.text, '')

    def test_no_filename_returns_empty_text(self):
        context = resolve_root_context(None, '\\section{Intro}\n')
        self.assertIsNone(context.filename)
        self.assertEqual(context.text, '')
        self.assertIsNone(context.build_dir)

    def test_unreadable_root_returns_empty_text(self):
        with tempfile.TemporaryDirectory() as project:
            root = os.path.join(project, 'main.tex')
            chapter = os.path.join(project, 'chapters', '05.tex')
            self._write(root, '\\documentclass{article}\\begin{document}\\end{document}')
            self._write(chapter, '% !TeX root = ../main.tex\n')

            context = resolve_root_context(chapter, open(chapter).read(),
                                           read_text=lambda filename: None)
            self.assertIsNone(context.filename)
            self.assertEqual(context.text, '')


if __name__ == '__main__':
    unittest.main()

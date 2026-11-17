#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''预览目标解析单测（bug④：多子文件工程跳子文件后预览侧栏被收起）。

Workspace 导入完整 GTK 应用图，这里沿用 test_workspace_realtime_persistence
的 AST 提取模式：只编译目标方法，装配轻量 harness，不依赖显示器。

契约：
  - get_preview_target_document 无副作用：只认已打开的 magic root /
    项目配置 root，绝不自动开文件；root 未打开退回活跃文档。
  - 显式 root_document 优先，不解析 magic。
  - 解析结果按（文件名, 头部 50 行文本）缓存：滚动/选区高频调用零开销，
    编辑头部或切文档自然失效。
  - update_preview_visibility：渲染器激活跟随预览目标（跳子文件时
    子文件渲染器停用、root 渲染器保持激活）。
'''

import ast
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import builtins
builtins._ = lambda s: s

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk

from setzer.document.magic_comments import (
    MAX_MAGIC_COMMENT_LINES,
    parse_magic_comments,
    resolve_root_filename,
)

WORKSPACE_SOURCE = Path(REPO) / 'setzer' / 'workspace' / 'workspace.py'


def _workspace_methods(*names):
    tree = ast.parse(WORKSPACE_SOURCE.read_text(encoding='utf-8'))
    workspace = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == 'Workspace')
    selected = [
        node for node in workspace.body
        if isinstance(node, ast.FunctionDef) and node.name in names]
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError('Workspace methods missing: ' + repr(missing))
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {
        'MAX_MAGIC_COMMENT_LINES': MAX_MAGIC_COMMENT_LINES,
        'parse_magic_comments': parse_magic_comments,
        'resolve_root_filename': resolve_root_filename,
    }
    exec(compile(ast.fix_missing_locations(module), str(WORKSPACE_SOURCE), 'exec'),
         namespace)
    return {name: namespace[name] for name in names}


_METHODS = _workspace_methods(
    'get_active_document',
    'get_root_or_active_latex_document',
    'get_preview_target_document',
    '_resolve_preview_root_filename',
    'update_preview_visibility',
)


class FakeRenderer:
    def __init__(self):
        self.active = False

    def activate(self):
        self.active = True

    def deactivate(self):
        self.active = False


class FakePreview:
    def __init__(self):
        self.page_renderer = FakeRenderer()


class FakeDocument:
    def __init__(self, filename, text=''):
        self.filename = filename
        self.source_buffer = Gtk.TextBuffer()
        self.source_buffer.set_text(text, -1)
        self.preview = FakePreview()

    def get_filename(self):
        return self.filename

    def is_latex_document(self):
        return True


class Harness:
    def __init__(self):
        self.active_document = None
        self.root_document = None
        self.open_latex_documents = []
        self.project_root_filename = None  # _get_project_root_filename 桩

    def _get_project_root_filename(self, document):
        return self.project_root_filename


for _name, _method in _METHODS.items():
    setattr(Harness, _name, _method)


class _ProjectFixture(unittest.TestCase):
    '''resolve_root_filename 会校验磁盘存在性：用真实临时文件装配工程。'''

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = Path(tmp.name)
        (self.project / 'chapters').mkdir()
        (self.project / 'main.tex').write_text('\\documentclass{article}\n',
                                               encoding='utf-8')
        (self.project / 'chapters' / 'one.tex').write_text(
            '% !TEX root = ../main.tex\n\\section{One}\n', encoding='utf-8')
        self.ws = Harness()
        self.root = FakeDocument(str(self.project / 'main.tex'),
                                 '\\documentclass{article}\n')
        self.child = FakeDocument(
            str(self.project / 'chapters' / 'one.tex'),
            '% !TEX root = ../main.tex\n\\section{One}\n')
        self.ws.open_latex_documents = [self.root, self.child]
        self.ws.active_document = self.child


class PreviewTargetTest(_ProjectFixture):

    def test_magic_root_open_returns_root(self):
        self.assertIs(self.ws.get_preview_target_document(), self.root)

    def test_magic_root_not_open_falls_back_to_active(self):
        self.ws.open_latex_documents = [self.child]
        self.assertIs(self.ws.get_preview_target_document(), self.child)

    def test_no_magic_root_returns_active(self):
        self.child.source_buffer.set_text('\\section{One}\n', -1)
        self.assertIs(self.ws.get_preview_target_document(), self.child)

    def test_explicit_root_wins_without_magic_parse(self):
        other = FakeDocument(str(self.project / 'other.tex'))
        self.ws.root_document = other
        # 提取出的方法经 __globals__ 解引用 parse_magic_comments，
        # 直接 patch 模块属性对其无效，须 patch 方法自己的 globals。
        parse = mock.Mock()
        method = Harness.get_preview_target_document
        with mock.patch.dict(method.__globals__, {'parse_magic_comments': parse}):
            self.assertIs(self.ws.get_preview_target_document(), other)
        parse.assert_not_called()

    def test_no_active_document_returns_none(self):
        self.ws.active_document = None
        self.assertIsNone(self.ws.get_preview_target_document())

    def test_project_config_root_wins_over_magic(self):
        self.ws.project_root_filename = str(self.project / 'main.tex')
        self.child.source_buffer.set_text('% !TEX root = ../elsewhere.tex\n', -1)
        self.assertIs(self.ws.get_preview_target_document(), self.root)

    def test_invalid_magic_root_falls_back(self):
        self.child.source_buffer.set_text('% !TEX root = ../missing.tex\n', -1)
        self.assertIs(self.ws.get_preview_target_document(), self.child)

    def test_absolute_magic_root_rejected(self):
        self.child.source_buffer.set_text(
            '% !TEX root = ' + str(self.project / 'main.tex') + '\n', -1)
        self.assertIs(self.ws.get_preview_target_document(), self.child)


class PreviewRootCacheTest(_ProjectFixture):

    def test_repeat_calls_hit_cache(self):
        with mock.patch.object(self.ws, '_get_project_root_filename',
                               wraps=self.ws._get_project_root_filename) as discover:
            self.ws.get_preview_target_document()
            self.ws.get_preview_target_document()
            self.ws.get_preview_target_document()
        self.assertEqual(discover.call_count, 1)

    def test_head_edit_invalidates_cache(self):
        self.assertIs(self.ws.get_preview_target_document(), self.root)
        self.child.source_buffer.set_text('\\section{One}\n', -1)
        self.assertIs(self.ws.get_preview_target_document(), self.child)

    def test_switch_document_invalidates_cache(self):
        self.assertIs(self.ws.get_preview_target_document(), self.root)
        plain = FakeDocument(str(self.project / 'plain.tex'), 'no magic here\n')
        self.ws.open_latex_documents.append(plain)
        self.ws.active_document = plain
        self.assertIs(self.ws.get_preview_target_document(), plain)

    def test_body_edit_beyond_head_keeps_cache(self):
        # 头部 50 行之外追加内容不影响缓存键（magic 只认前 50 行）。
        self.child.source_buffer.set_text(
            '% !TEX root = ../main.tex\n' + 'body\n' * 60, -1)
        self.ws.get_preview_target_document()
        buf = self.child.source_buffer
        buf.insert(buf.get_end_iter(), 'x\n' * 100)
        with mock.patch.object(self.ws, '_get_project_root_filename',
                               wraps=self.ws._get_project_root_filename) as discover:
            self.ws.get_preview_target_document()
        discover.assert_not_called()


class PreviewVisibilityTest(_ProjectFixture):

    def test_jump_to_child_keeps_root_renderer_active(self):
        self.ws.update_preview_visibility(self.child)
        self.assertFalse(self.child.preview.page_renderer.active)
        self.assertTrue(self.root.preview.page_renderer.active)

    def test_active_root_activates_own_renderer(self):
        self.ws.active_document = self.root
        self.ws.update_preview_visibility(self.root)
        self.assertTrue(self.root.preview.page_renderer.active)

    def test_non_target_document_deactivated(self):
        self.ws.active_document = self.root
        self.ws.update_preview_visibility(self.child)
        self.assertFalse(self.child.preview.page_renderer.active)
        self.assertTrue(self.root.preview.page_renderer.active)

    def test_document_without_preview_skipped(self):
        del self.child.preview
        self.ws.update_preview_visibility(self.child)  # 不抛异常
        self.assertTrue(self.root.preview.page_renderer.active)


if __name__ == '__main__':
    unittest.main()
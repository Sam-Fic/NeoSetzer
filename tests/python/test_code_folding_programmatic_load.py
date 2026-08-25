#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# coding: utf-8

'''回归测试：程序化载入（打开文件 / 会话恢复 / 重载）路径上的 code_folding。

背景：磁盘载入期间 parser 的 insert-text 守卫使 last_edit 保持 None，
CodeFolding.on_parser_update 必须显式处理 None（整篇载入语义）。曾因未处理：
1) 对已有折叠区域的文档重载时 UnboundLocalError——被 Observable 吞掉后表现为
   折叠区域永不建立 / 状态丢失；
2) 同内容重载时折叠状态应完整保留。

需要 GTK 环境；无显示环境（CI headless）自动跳过。
'''

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')

from gi.repository import Gtk, GtkSource

gtk_ready = False
try:
    Gtk.init()  # 失败时抛异常；成功时返回值因版本而异（可能为 None）
    gtk_ready = True
except Exception:
    gtk_ready = False


class _FakeSettings:
    def get_value(self, *a):
        return True

    def connect(self, *a):
        return None


@unittest.skipUnless(gtk_ready, 'GTK 不可用（无显示环境），跳过')
class TestCodeFoldingProgrammaticLoad(unittest.TestCase):

    def _build(self, n_sections=50):
        import setzer.app.service_locator as sl
        from setzer.document.parser.parser_latex import ParserLaTeX
        from setzer.document.code_folding.code_folding import CodeFolding
        from setzer.helpers.observable import Observable

        sl.ServiceLocator.settings = _FakeSettings()

        class Doc(Observable):
            def __init__(self):
                Observable.__init__(self)
                self.source_buffer = GtkSource.Buffer()
                self._loading_from_disk = False

        doc = Doc()
        parser = ParserLaTeX(doc)
        doc.parser = parser
        folding = CodeFolding(doc)

        text = '\n'.join(
            [r'\documentclass{article}', r'\begin{document}']
            + [r'\section{S%d}' % i for i in range(n_sections)]
            + [r'\end{document}'])
        return doc, parser, folding, text

    def _load_from_disk(self, doc, parser, text):
        '''模拟 Document._load_file_content 的程序化载入序列。'''
        doc._loading_from_disk = True
        try:
            if hasattr(parser, 'last_edit'):
                parser.last_edit = None
            doc.source_buffer.set_text(text)
            parser.initial_parse(text)
        finally:
            doc._loading_from_disk = False

    def test_first_open_builds_regions_with_last_edit_none(self):
        '''首次打开：last_edit=None 时必须正常建立折叠区域（不得静默崩溃）。'''
        doc, parser, folding, text = self._build()
        self.assertIsNone(parser.last_edit)
        self._load_from_disk(doc, parser, text)
        self.assertGreater(len(folding.folding_regions), 0)
        self.assertEqual(len(folding.folding_regions_by_line),
                         len(folding.folding_regions))

    def test_reload_same_content_preserves_folded_state(self):
        '''同内容重载：所有折叠状态保留，区域表按新解析结果重建。'''
        doc, parser, folding, text = self._build()
        self._load_from_disk(doc, parser, text)

        first_region = next(iter(folding.folding_regions.values()))
        folding.fold(first_region)
        folded_before = sum(1 for r in folding.folding_regions.values() if r['is_folded'])
        self.assertEqual(folded_before, 1)

        # 重载（内容未变）
        self._load_from_disk(doc, parser, text)

        folded_after = sum(1 for r in folding.folding_regions.values() if r['is_folded'])
        self.assertEqual(folded_after, 1, '同内容重载后折叠状态丢失')
        self.assertGreater(len(folding.folding_regions_by_line), 0)

    def test_edit_after_load_relocates_without_error(self):
        '''载入后真实编辑：insert 分支的偏移平移必须正常工作。

        曾因重构遗漏 else 分支的 folding_regions 初始化而 UnboundLocalError
        （被 Observable 吞掉后表现为编辑后折叠区域全部消失）。此处直接调用
        on_parser_update 以暴露异常——信号分发的回调异常会被静默吞掉。'''
        doc, parser, folding, text = self._build()
        self._load_from_disk(doc, parser, text)

        first_region = next(iter(folding.folding_regions.values()))
        folding.fold(first_region)

        # 模拟用户在文档末尾插入一节（真实编辑路径，last_edit 被正常设置）
        doc.source_buffer.insert(doc.source_buffer.get_end_iter(),
                                 '\n\\section{Brand New}')
        # 防抖到期：全量重解析并 emit finished_parsing（分发路径会吞回调异常，
        # 故随后再直接调用一次 on_parser_update 以暴露潜在异常）
        parser._flush_parsing()
        titles = [b[5] for b in parser.symbols['blocks'] if len(b) > 5]
        self.assertIn('Brand New', titles)
        folding.on_parser_update(parser)   # 异常不应被吞
        new_block = next(b for b in parser.symbols['blocks']
                         if len(b) > 5 and b[5] == 'Brand New')
        self.assertIn(new_block[2], folding.folding_regions_by_line,
                      '编辑后新章节的折叠区域未建立（on_parser_update 可能静默失败）')
        folded_now = sum(1 for r in folding.folding_regions.values() if r['is_folded'])
        self.assertEqual(folded_now, 1, '插入点之前的折叠状态应经平移保留')


if __name__ == '__main__':
    unittest.main()

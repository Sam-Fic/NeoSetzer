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


if __name__ == '__main__':
    unittest.main()

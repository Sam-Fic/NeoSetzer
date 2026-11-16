#!/usr/bin/env python3
# coding: utf-8
#
# 每文档引擎选择的恢复。
#
# 它曾经和"编译日志是否新鲜"绑在一起：只有 .tex 自上次构建后没被改过才恢复。
# 于是"构建后编辑过"或"从未构建过"的文档一重启就回到默认引擎，而随后的
# save_document_state 会把 None 写回存档，选择永久消失。这组测试钉住解耦。

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = 'setzer/settings/document_settings.py'


def load_static(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    value = namespace[method_name]
    return getattr(value, '__func__', value)


def document(latex=True, toolchain=True, interpreter=None):
    doc = mock.Mock()
    doc.is_latex_document.return_value = latex
    doc.build_system.latex_interpreter = interpreter
    if not toolchain:
        doc.build_system = None
    return doc


class RestoreTest(unittest.TestCase):

    def setUp(self):
        namespace = {'DocumentSettings': mock.Mock()}
        namespace['DocumentSettings']._has_latex_toolchain = staticmethod(
            lambda doc: getattr(doc, 'build_system', None) is not None)
        self.restore = load_static(SETTINGS, 'DocumentSettings',
                                   'restore_interpreter_override', namespace)

    def test_restores_even_without_a_build_log(self):
        '''从未构建过的文档：没有任何 build_log_* 字段也要恢复。'''
        doc = document()
        self.restore(doc, {'latex_interpreter': 'tectonic'})
        self.assertEqual(doc.build_system.latex_interpreter, 'tectonic')

    def test_restores_even_when_the_tex_changed_since_the_build(self):
        '''构建后编辑过文件：旧的 mtime 校验会在这里丢弃选择。'''
        doc = document()
        self.restore(doc, {'latex_interpreter': 'phitex',
                           'build_log_mtime': 1.0})
        self.assertEqual(doc.build_system.latex_interpreter, 'phitex')

    def test_missing_key_stays_default(self):
        doc = document(interpreter=None)
        self.restore(doc, {})
        self.assertIsNone(doc.build_system.latex_interpreter)

    def test_skips_documents_without_a_toolchain(self):
        '''会话恢复的轻量文档：此时没有 build_system，交给 pending 路径。'''
        doc = document(toolchain=False)
        self.restore(doc, {'latex_interpreter': 'tectonic'})

    def test_skips_non_latex_documents(self):
        doc = document(latex=False)
        self.restore(doc, {'latex_interpreter': 'tectonic'})
        self.assertIsNone(doc.build_system.latex_interpreter)


class BuildLogRestoreTest(unittest.TestCase):
    '''编译日志的新鲜度门控不得再顺手改写引擎选择。'''

    def test_interpreter_is_not_touched_by_the_build_log_restore(self):
        fd, path = tempfile.mkstemp(suffix='.tex')
        os.close(fd)
        try:
            mtime = os.stat(path).st_mtime
            namespace = {'os': os}
            restore = load_static(SETTINGS, 'DocumentSettings',
                                  '_restore_build_log_state', namespace)
            doc = document(interpreter='xelatex')
            doc.filename = path
            restore(doc, {
                'build_log_data': {}, 'has_been_built': True, 'build_time': 1,
                'latex_interpreter': 'tectonic', 'has_synctex_file': False,
                'build_log_mtime': mtime,
            })
            self.assertEqual(doc.build_system.latex_interpreter, 'xelatex')
        finally:
            os.unlink(path)


if __name__ == '__main__':
    unittest.main()

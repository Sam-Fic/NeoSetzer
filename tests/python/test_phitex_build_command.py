#!/usr/bin/env python3
# coding: utf-8
#
# PhiTeX 作为 LaTeX 引擎接入后的命令拼装与各处引擎清单一致性。
#
# get_build_command 用 load_methods 那套 AST 抽取，避免为了断言一条字符串
# 而实例化整个 builder（它依赖 ServiceLocator / latex_log_parser）。

import ast
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = 'setzer/document/build_system/builder/builder_build_latex.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class FakeQuery:

    def __init__(self, interpreter, **overrides):
        self.tex_filename = '/proj/main.tex'
        self.build_data = {
            'latex_interpreter': interpreter,
            'additional_arguments': '',
            'use_latexmk': True,
            'enable_synctex': True,
            'output_chain': 'pdf',
        }
        self.build_data.update(overrides)


class BuildCommandTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 抽出来的是未绑定函数，第一个形参仍是 self；包成 staticmethod
        # 才不会在被当作类属性访问时额外吃掉一个 self。
        cls.get_build_command = staticmethod(load_method(
            BUILDER, 'BuilderBuildLaTeX', 'get_build_command', {'shutil': shutil}))

    def command(self, interpreter, **overrides):
        # 抽出的函数保留原 self 形参（方法体内不使用），传 None 占位。
        return self.get_build_command(None, FakeQuery(interpreter, **overrides), '/out')

    def test_phitex_uses_its_own_flag_shape(self):
        '''phitex 是子命令 + 位置参数：输出目录 -o、SynTeX --synctex。'''
        self.assertEqual(
            self.command('phitex'),
            'phitex build -o "/out" --synctex "/proj/main.tex"')

    def test_phitex_omits_synctex_when_disabled(self):
        self.assertEqual(
            self.command('phitex', enable_synctex=False),
            'phitex build -o "/out" "/proj/main.tex"')

    def test_phitex_never_routes_through_latexmk(self):
        '''use_latexmk 默认开启，但 phitex 自带到不动点的编排，二次编排会
        让 latexmk 去驱动 pdflatex 而不是 phitex。'''
        command = self.command('phitex', use_latexmk=True)
        self.assertNotIn('latexmk', command)

    def test_phitex_passes_additional_arguments(self):
        command = self.command('phitex', additional_arguments=' --shell-escape')
        self.assertEqual(
            command,
            'phitex build --shell-escape -o "/out" --synctex "/proj/main.tex"')

    def test_texlive_engines_unchanged(self):
        '''回归：抽方法后 texlive/tectonic 直连路线与 latexmk 路线逐字不变。'''
        self.assertEqual(
            self.command('xelatex', use_latexmk=False),
            'xelatex -synctex=1 -interaction=nonstopmode -output-directory="/out" '
            '"/proj/main.tex"')
        self.assertEqual(
            self.command('tectonic', additional_arguments=''),
            'tectonic --synctex --keep-logs --outdir "/out" "/proj/main.tex"')
        if shutil.which('latexmk'):
            self.assertTrue(
                self.command('pdflatex').startswith('latexmk -pdf -synctex=1'))
        else:
            self.assertTrue(
                self.command('pdflatex').startswith('pdflatex -synctex=1'))


class EngineRegistryConsistencyTest(unittest.TestCase):
    '''phitex 必须同时出现在每一处引擎登记表里。

    这些清单散落在 6 个文件，漏掉任何一个的表现都是静默的：项目配置里被
    白名单丢弃、菜单选不到、tooltip 显示原始 key、或 snippet 编译拼错命令。
    用源码扫描钉死，而不是等某次人工发现。'''

    EXPECTED = (
        'setzer/document/magic_comments.py',
        'setzer/project/build_configuration.py',
        'setzer/dialogs/preferences/pages/page_build_system.py',
        'setzer/document/build_widget/build_widget.py',
        'setzer/dialogs/document_properties/document_properties.py',
        'setzer/dialogs/project_build_configuration_viewgtk.py',
        'setzer/popovers/shortcutsbar/document_menu.py',
        'setzer/document/snippet_preview/snippet_compiler.py',
    )

    def test_phitex_listed_everywhere(self):
        missing = [path for path in self.EXPECTED
                   if "'phitex'" not in (ROOT / path).read_text(encoding='utf-8')
                   and '"phitex"' not in (ROOT / path).read_text(encoding='utf-8')]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()

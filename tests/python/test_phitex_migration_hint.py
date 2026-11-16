#!/usr/bin/env python3
# coding: utf-8
#
# `% !TEX program` 迁移提示的前置条件矩阵。
#
# 这条提示的价值完全取决于它出现的时机：说错比不说说更糟——PhiTeX 目前
# 不支持 lualatex，也没有 pdfps 那条 latex→dvips→ps2pdf 路线，在这两种
# 文档上建议迁移是把用户往编不出来的方向推。所以每一格"不该出现"都要钉住。

import ast
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
DIALOG = 'setzer/dialogs/document_properties/document_properties.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class Group:
    '''提示现在走组的 description，显隐即"文本为空还是非空"。'''

    def __init__(self):
        self.description = ''

    def set_description(self, value):
        self.description = value


class Doc:
    '''只满足 _sync_magic_hint 用到的那几个访问点。'''

    class _BS:
        def __init__(self, effective, configured):
            self._effective = effective
            self._configured = configured

        def get_effective_interpreter(self, text=None):
            return self._effective

        def get_configured_interpreter(self, project_data=None):
            return self._configured

    def __init__(self, effective, configured):
        self.build_system = Doc._BS(effective, configured)

    def is_latex_document(self):
        return True


def run_hint(effective, configured, *, phitex_present=True, output_chain='pdf'):
    namespace = {'shutil': mock.Mock(which=mock.Mock(return_value='/usr/bin/phitex'
                                                      if phitex_present else None)),
                 'GLib': mock.Mock(markup_escape_text=lambda text, length: text),
                 '_': lambda s: s}
    method = load_method(DIALOG, 'DocumentPropertiesDialog', '_sync_magic_hint', namespace)
    group = Group()
    dialog = mock.Mock()
    dialog.document = Doc(effective, configured)
    dialog.view.build_group = group
    dialog.settings.get_value.return_value = output_chain
    method(dialog)
    return group


class MigrationHintTest(unittest.TestCase):

    def test_shown_when_comment_overrides_and_phitex_can_fix_it(self):
        group = run_hint('xelatex', 'pdflatex')
        self.assertTrue(group.description)
        self.assertIn('xelatex', group.description)

    def test_hidden_without_a_comment(self):
        # 没有注释时 effective 就等于 configured，不存在顶掉。
        self.assertFalse(run_hint('pdflatex', 'pdflatex').description)

    def test_hidden_when_comment_matches_the_choice(self):
        self.assertFalse(run_hint('xelatex', 'xelatex').description)

    def test_hidden_when_already_on_phitex(self):
        # 选的就是 PhiTeX：引擎解析里注释不会替换它，本来就没冲突。
        self.assertFalse(run_hint('phitex', 'phitex').description)

    def test_hidden_when_comment_asks_for_phitex(self):
        # 注释已经在推 PhiTeX 且已生效，再建议一次只是重复。
        self.assertFalse(run_hint('phitex', 'pdflatex').description)

    def test_hidden_for_lualatex_documents(self):
        '''PhiTeX 明确不支持 lualatex，迁移过去文档直接编不出来。'''
        self.assertFalse(run_hint('lualatex', 'pdflatex').description)

    def test_hidden_on_the_pdfps_output_chain(self):
        '''PSTricks/psfrag 走 latex→dvips→ps2pdf，PhiTeX 没有这条路线。'''
        self.assertFalse(run_hint('xelatex', 'pdflatex', output_chain='pdfps').description)

    def test_hidden_when_phitex_is_not_installed(self):
        '''建议一件这台机器上做不了的事没有意义。'''
        self.assertFalse(run_hint('xelatex', 'pdflatex', phitex_present=False).description)


class SharedInterpreterResolverTest(unittest.TestCase):
    '''构建按钮 tooltip 必须问 build_system，不能自己解一遍链。

    漂移过的形态：用户选 PhiTeX、文档写着 `% !TEX program = xelatex`，实际用
    PhiTeX 编译（注释不替换 phitex），但 tooltip 显示 XeLaTeX。
    '''

    def test_build_widget_uses_the_shared_resolver(self):
        source = (ROOT / 'setzer/document/build_widget/build_widget.py').read_text(encoding='utf-8')
        self.assertIn('build_system.get_effective_interpreter()', source)
        self.assertNotIn('parse_magic_comments', source)

    def test_hint_does_not_reimplement_the_chain(self):
        '''提示只看 build_system 的两个取值，不再自己读 DocumentSettings。

        组合框自己的状态同步仍要读 per-document 覆盖，那是另一件事；这里
        约束的是 _sync_magic_hint 这一个方法体。
        '''
        source = (ROOT / DIALOG).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == 'DocumentPropertiesDialog')
        body = next(node for node in cls.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == '_sync_magic_hint')
        rendered = ast.dump(body)
        self.assertIn('get_effective_interpreter', rendered)
        self.assertIn('get_configured_interpreter', rendered)
        self.assertNotIn('DocumentSettings', rendered)


if __name__ == '__main__':
    unittest.main()

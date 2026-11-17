#!/usr/bin/env python3
# coding: utf-8
#
# 会话恢复的标签页首次激活时，预览区不能显示 "No preview available"。
#
# 根因：Preview 只靠 document 的 filename_change 信号加载 PDF。正常打开流程
# 是先建 Preview 再 set_filename，信号能把 PDF 带进来；会话恢复的非活跃文档
# 反过来——先 set_filename（attach_preview=False），等用户切到该标签页时才
# 构造 Preview。那次 filename_change 早已发过，Preview 构造完就停在空白页，
# 磁盘上编译好的 PDF 一直没被读。
#
# 修法：Preview 构造末尾按文档当前文件名自举一次（等价于再走一次
# on_filename_change）：有现成 PDF 就显示，没有才留空白页。

import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = 'setzer/document/preview/preview.py'


def load_preview_class():
    source = ROOT / PREVIEW
    tree = ast.parse(source.read_text(encoding='utf-8'))
    return tree, next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'Preview')


def load_methods(names, namespace):
    source = ROOT / PREVIEW
    _, cls = load_preview_class()
    nodes = [node for node in cls.body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    missing = set(names) - {node.name for node in nodes}
    if missing:
        raise AssertionError('Preview 缺少方法: %s' % ', '.join(sorted(missing)))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


class FakePreview:
    '''只记录副作用的最小 self：Mock 会把 self.on_filename_change 也变成
    Mock，测不到真实链路，所以手动绑上抽取出来的两个方法。'''

    def __init__(self, filename):
        self.document = mock.Mock()
        self.document.get_filename.return_value = filename
        self.pdf_filenames = []
        self.load_calls = 0

    def set_pdf_filename(self, pdf_filename):
        self.pdf_filenames.append(pdf_filename)

    def load_pdf(self):
        self.load_calls += 1


class BootstrapTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ns = {'os': os,
                   'ProjectBuildConfiguration': mock.Mock(
                       discover=mock.Mock(return_value=None))}
        load_methods({'on_filename_change', '_initialize_pdf_from_filename',
                      '_existing_pdf_for_source'}, self.ns)
        FakePreview.on_filename_change = self.ns['on_filename_change']
        FakePreview._initialize_pdf_from_filename = self.ns['_initialize_pdf_from_filename']
        FakePreview._existing_pdf_for_source = self.ns['_existing_pdf_for_source']

    def bootstrap(self, filename):
        preview = FakePreview(filename)
        preview._initialize_pdf_from_filename()
        return preview

    def test_existing_pdf_is_loaded(self):
        '''源码旁边有编译产物：构造即接上，不能停在空白页。'''
        tex = os.path.join(self.tmp.name, 'main.tex')
        pdf = os.path.join(self.tmp.name, 'main.pdf')
        Path(pdf).write_bytes(b'%PDF-1.5\n')
        preview = self.bootstrap(tex)
        self.assertEqual(preview.pdf_filenames, [pdf])
        self.assertEqual(preview.load_calls, 1)

    def test_no_pdf_falls_back_to_blank_slate(self):
        '''没有产物时仍走 load_pdf（回落空白页），不伪造路径。'''
        tex = os.path.join(self.tmp.name, 'main.tex')
        preview = self.bootstrap(tex)
        self.assertEqual(preview.pdf_filenames, [])
        self.assertEqual(preview.load_calls, 1)

    def test_untitled_document_is_left_alone(self):
        '''未命名文档没有文件名，构造期不该有任何加载动作。'''
        preview = self.bootstrap(None)
        self.assertEqual(preview.pdf_filenames, [])
        self.assertEqual(preview.load_calls, 0)


class InitWiringTest(unittest.TestCase):

    def test_init_calls_bootstrap(self):
        '''方法存在但没在构造期调用 = 修复无效（回归护栏）。'''
        _, cls = load_preview_class()
        init = next(node for node in cls.body
                    if isinstance(node, ast.FunctionDef) and node.name == '__init__')
        calls = [node for node in ast.walk(init)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == '_initialize_pdf_from_filename']
        self.assertTrue(
            calls,
            'Preview.__init__ 必须调用 _initialize_pdf_from_filename()，'
            '否则会话恢复后首次激活的标签页仍会显示 "No preview available"')


SETTINGS = 'setzer/settings/document_settings.py'


def load_restore_preview_state(namespace):
    source = ROOT / SETTINGS
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'DocumentSettings')
    nodes = [node for node in cls.body
             if isinstance(node, ast.FunctionDef) and node.name == '_restore_preview_state']
    if not nodes:
        raise AssertionError('DocumentSettings 缺少 _restore_preview_state')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_restore_preview_state']


class RestorePreviewStateTest(unittest.TestCase):
    '''自定义输出目录等情形：状态里记的 PDF 不在 .tex 旁边，构造期自举拿不到，
    恢复状态时若只填路径不读盘，标签页依旧是空白页。'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pdf = os.path.join(self.tmp.name, 'main.pdf')
        Path(self.pdf).write_bytes(b'%PDF-1.5\n')
        self.ns = {'os': os}
        self.restore = load_restore_preview_state(self.ns)

    def run_restore(self, poppler_document, loaded_filename):
        document = mock.Mock()
        document.is_latex_document.return_value = True
        preview = mock.Mock()
        preview.poppler_document = poppler_document
        preview.pdf_filename = loaded_filename
        preview.layout = None
        document.preview = preview
        data = {'pdf_filename': self.pdf, 'pdf_date': None, 'xoffset': 0,
                'yoffset': 0, 'zoom_level': 1.0, 'zoom_mode': 'manual'}
        self.restore(document, data)
        return preview

    def test_missing_document_is_loaded(self):
        preview = self.run_restore(None, None)
        preview.set_pdf_filename.assert_called_once_with(self.pdf)
        preview.load_pdf.assert_called_once_with()

    def test_other_pdf_is_replaced_and_loaded(self):
        preview = self.run_restore(mock.Mock(), '/elsewhere/other.pdf')
        preview.set_pdf_filename.assert_called_once_with(self.pdf)
        preview.load_pdf.assert_called_once_with()

    def test_already_loaded_pdf_is_not_reloaded(self):
        preview = self.run_restore(mock.Mock(), self.pdf)
        preview.set_pdf_filename.assert_called_once_with(self.pdf)
        preview.load_pdf.assert_not_called()


class ExistingPdfLookupTest(unittest.TestCase):
    '''源码对应的产物位置：`.tex` 旁边优先，其次项目自定义输出目录。'''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tex = os.path.join(self.tmp.name, 'main.tex')
        Path(self.tex).write_text('x', encoding='utf-8')
        self.ns = {'os': os}
        load_methods({'on_filename_change', '_existing_pdf_for_source'}, self.ns)
        self.lookup = getattr(self.ns['_existing_pdf_for_source'], '__func__',
                              self.ns['_existing_pdf_for_source'])

    def use_configuration(self, output_directory):
        class FakeConfiguration:
            def load(self):
                return {'output_directory': output_directory}

            def effective_path(self, path):
                if not path:
                    return None
                return os.path.join(self_root, path)

        self_root = self.tmp.name
        self.ns['ProjectBuildConfiguration'] = mock.Mock(
            discover=mock.Mock(return_value=FakeConfiguration()))

    def test_sibling_pdf_wins(self):
        self.use_configuration('build')
        sibling = os.path.join(self.tmp.name, 'main.pdf')
        Path(sibling).write_bytes(b'%PDF-1.5\n')
        self.assertEqual(self.lookup(self.tex), sibling)

    def test_pdf_in_project_output_directory(self):
        '''自定义输出目录：产物不在 .tex 旁边，仍要能找到。'''
        self.use_configuration('build')
        outdir = os.path.join(self.tmp.name, 'build')
        os.makedirs(outdir)
        pdf = os.path.join(outdir, 'main.pdf')
        Path(pdf).write_bytes(b'%PDF-1.5\n')
        self.assertEqual(self.lookup(self.tex), pdf)

    def test_no_pdf_anywhere(self):
        self.use_configuration('build')
        self.assertIsNone(self.lookup(self.tex))

    def test_no_project_configuration(self):
        self.ns['ProjectBuildConfiguration'] = mock.Mock(
            discover=mock.Mock(return_value=None))
        self.assertIsNone(self.lookup(self.tex))


PANEL = 'setzer/workspace/preview_panel/preview_panel_presenter.py'


def load_panel_method(name, namespace):
    source = ROOT / PANEL
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef)
               and node.name == 'PreviewPanelPresenter')
    nodes = [node for node in cls.body
             if isinstance(node, ast.FunctionDef) and node.name == name]
    if not nodes:
        raise AssertionError('PreviewPanelPresenter 缺少 %s' % name)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name], cls


class EnsureToolchainTest(unittest.TestCase):
    '''预览目标本身没有工具链时（多文件工程里 root 从未被点开）侧栏只剩空白
    占位，必须为它补挂一次。'''

    def setUp(self):
        self.ns = {'GLib': mock.Mock()}
        self.ensure, _ = load_panel_method('_ensure_preview_toolchain', self.ns)

    def make_presenter(self):
        presenter = mock.Mock()
        return presenter

    def make_document(self, latex=True, preview=None):
        document = mock.Mock()
        document.is_latex_document.return_value = latex
        document.preview = preview
        return document

    def test_attaches_missing_toolchain(self):
        presenter = self.make_presenter()
        document = self.make_document()
        self.ensure(presenter, document)
        presenter.workspace._attach_latex_toolchain.assert_called_once_with(document)
        # 挂接后必须重估一次侧栏显隐：否则「未编译就收起预览栏」的旧判定
        # 会把刚有 PDF 的目标继续收在侧栏外。
        self.ns['GLib'].idle_add.assert_called_once_with(
            presenter.workspace.presenter.update_preview_help_visibility, False)

    def test_existing_toolchain_is_left_alone(self):
        presenter = self.make_presenter()
        document = self.make_document(preview=mock.Mock())
        self.ensure(presenter, document)
        presenter.workspace._attach_latex_toolchain.assert_not_called()
        self.ns['GLib'].idle_add.assert_not_called()

    def test_non_latex_document_is_ignored(self):
        presenter = self.make_presenter()
        document = self.make_document(latex=False)
        self.ensure(presenter, document)
        presenter.workspace._attach_latex_toolchain.assert_not_called()

    def test_none_document_is_ignored(self):
        presenter = self.make_presenter()
        self.ensure(presenter, None)
        presenter.workspace._attach_latex_toolchain.assert_not_called()


class PanelWiringTest(unittest.TestCase):

    def setUp(self):
        _, self.cls = load_panel_method('_ensure_preview_toolchain', {})
        self.show = next(node for node in self.cls.body
                         if isinstance(node, ast.FunctionDef)
                         and node.name == 'set_preview_document')

    def called_attrs(self):
        return {node.func.attr for node in ast.walk(self.show)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}

    def test_showing_a_document_ensures_the_toolchain(self):
        self.assertIn(
            '_ensure_preview_toolchain', self.called_attrs(),
            'set_preview_document 必须先为目标文档补挂工具链，否则没有工具链的'
            '预览目标只能显示空白占位')

    def test_view_is_added_to_the_stack_before_making_it_visible(self):
        '''set_visible_child 对非子控件无效（面板停在旧内容/全空）。'''
        self.assertIn('add_child', self.called_attrs())


if __name__ == '__main__':
    unittest.main()

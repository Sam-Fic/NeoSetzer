#!/usr/bin/env python3
# coding: utf-8
#
# 引擎显示链：describe_interpreter 的来源标注 + 文档菜单的标题/说明行。
#
# 文档菜单和文档属性页都靠 describe_interpreter 回答"用哪个引擎、为什么"，
# 所以这里钉住来源映射与 PhiTeX 例外：两处 UI 一旦各自重新实现一遍优先链，
# 迟早会出现"实际用 A、界面显示 B"的漂移。

import ast
import types
import unittest
from pathlib import Path

import setzer.helpers.interpreter_labels as labels

# 应用启动时才把 _ 注入内建命名空间；测试里给这个模块补一份直通实现。
labels._ = lambda text: text

ROOT = Path(__file__).resolve().parents[2]
BUILD_SYSTEM = 'setzer/document/build_system/build_system.py'
DOCUMENT_MENU = 'setzer/popovers/shortcutsbar/document_menu.py'
DOCUMENT_PROPERTIES = 'setzer/dialogs/document_properties/document_properties.py'
DOCUMENT_PROPERTIES_VIEW = 'setzer/dialogs/document_properties/document_properties_viewgtk.py'
SHORTCUTSBAR = 'setzer/workspace/shortcutsbar/shortcutsbar.py'
SHORTCUTSBAR_VIEW = 'setzer/workspace/shortcutsbar/shortcutsbar_viewgtk.py'


def load_method(relative_path, class_name, method_name, namespace):
    source = ROOT / relative_path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    fn = next(node for node in cls.body
              if isinstance(node, ast.FunctionDef) and node.name == method_name)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[method_name]


class Magic:
    def __init__(self, program):
        self.program = program


class FakeBuildSystem:
    '''只实现 describe_interpreter 用到的访问点。'''

    def __init__(self, configured, source, document_override=None):
        self.latex_interpreter = document_override
        self.configured = configured
        self.source = source

    def _project_build_data(self):
        return None, {}

    def _effective_build_value_with_source(self, preference_key, project_data,
                                           project_key=None):
        return self.configured, self.source


def describe(build_system, text=''):
    namespace = {'parse_magic_comments': lambda t: Magic(t or None)}
    method = load_method(BUILD_SYSTEM, 'BuildSystem', 'describe_interpreter', namespace)
    return method(build_system, text)


class DescribeInterpreterTest(unittest.TestCase):

    def test_global_preference(self):
        self.assertEqual(describe(FakeBuildSystem('xelatex', 'global')), ('xelatex', 'global'))

    def test_project_configuration(self):
        self.assertEqual(describe(FakeBuildSystem('tectonic', 'project')),
                         ('tectonic', 'project'))

    def test_document_override_beats_project_and_global(self):
        bs = FakeBuildSystem('xelatex', 'global', document_override='lualatex')
        self.assertEqual(describe(bs), ('lualatex', 'document'))

    def test_magic_comment_overrides_the_configured_engine(self):
        self.assertEqual(describe(FakeBuildSystem('xelatex', 'global'), 'pdflatex'),
                         ('pdflatex', 'comment'))

    def test_magic_comment_cannot_replace_phitex(self):
        # PhiTeX 自己读注释挑引擎，外层再换成 texlive 引擎会绕过它的 session。
        self.assertEqual(describe(FakeBuildSystem('phitex', 'global'), 'xelatex'),
                         ('phitex', 'global'))

    def test_phitex_from_document_keeps_document_source(self):
        bs = FakeBuildSystem('phitex', 'global', document_override='phitex')
        self.assertEqual(describe(bs, 'xelatex'), ('phitex', 'document'))


class FakeBuilder:
    '''记录 DocumentMenu.__init__ 的调用序列，不碰 GTK。'''

    def __init__(self):
        self.pages = []
        self.actions = []      # (page, label, action, parameter)
        self.labels = []       # (page, label)
        self.submenus = []     # (title, page)
        self.separators = []   # page

    def add_page(self, pagename, label=None):
        self.pages.append((pagename, label))

    def add_insert_symbol_item(self, *args, **kwargs):
        pass

    def add_before_after_item(self, *args, **kwargs):
        pass

    def add_action_button(self, page, title, action, parameter=None,
                          icon=None, shortcut=None):
        self.actions.append((page, title, action, parameter))

    def add_label_item(self, page, title):
        self.labels.append((page, title))

    def add_menu_button(self, title, page):
        self.submenus.append((title, page))

    def add_separator(self, page='main'):
        self.separators.append(page)


def build_menu(interpreter_info):
    namespace = {
        'GioMenuBuilder': FakeBuilder,
        'GLib': types.SimpleNamespace(Variant=lambda kind, value: (kind, value)),
        '_': labels._,
        'interpreter_name': labels.interpreter_name,
        'interpreter_source': labels.interpreter_source,
    }
    init = load_method(DOCUMENT_MENU, 'DocumentMenu', '__init__', namespace)
    builder = FakeBuilder()
    init(builder, interpreter_info=interpreter_info)
    return builder


class DocumentMenuInterpreterTest(unittest.TestCase):

    def test_submenu_title_shows_the_effective_engine(self):
        menu = build_menu(('xelatex', 'global'))
        self.assertIn(('Build Interpreter: XeLaTeX', 'interpreter-submenu'),
                      menu.submenus)

    def test_info_row_explains_the_source(self):
        menu = build_menu(('xelatex', 'global'))
        self.assertEqual(menu.labels, [
            ('interpreter-submenu',
             'Currently using XeLaTeX (from preferences)'),
        ])
        self.assertIn('interpreter-submenu', menu.separators)

    def test_comment_source_names_the_magic_comment(self):
        menu = build_menu(('pdflatex', 'comment'))
        self.assertEqual(len(menu.labels), 1)
        self.assertIn('% !TEX program', menu.labels[0][1])

    def test_no_build_system_keeps_the_plain_submenu(self):
        menu = build_menu(None)
        self.assertEqual(menu.labels, [])
        self.assertIn(('Build Interpreter', 'interpreter-submenu'), menu.submenus)

    def test_override_entries_are_unchanged(self):
        menu = build_menu(('xelatex', 'global'))
        targets = [parameter for (page, _title, action, parameter) in menu.actions
                   if action == 'win.set-build-interpreter']
        self.assertEqual(targets, [
            ('s', 'default'),
            ('s', 'pdflatex'),
            ('s', 'xelatex'),
            ('s', 'lualatex'),
            ('s', 'tectonic'),
            ('s', 'phitex'),
        ])


class InterpreterLabelsTest(unittest.TestCase):

    def test_known_engine_names(self):
        self.assertEqual(labels.interpreter_name('phitex'), 'PhiTeX')
        self.assertEqual(labels.interpreter_name('pdflatex'), 'PDFLaTeX')

    def test_unknown_engine_passes_through(self):
        self.assertEqual(labels.interpreter_name('some-engine'), 'some-engine')

    def test_source_phrases(self):
        self.assertEqual(labels.interpreter_source('global'), 'from preferences')
        self.assertEqual(labels.interpreter_source('project'), 'from project configuration')
        self.assertEqual(labels.interpreter_source('document'), 'set for this document')


class MenuRefreshWiringTest(unittest.TestCase):
    '''菜单模型是静态快照：展开前必须按当前文档重建。'''

    def test_controller_rebuilds_on_open(self):
        source = (ROOT / SHORTCUTSBAR).read_text(encoding='utf-8')
        self.assertIn("'notify::active'", source)
        self.assertIn('rebuild_document_menu', source)

    def test_view_rebuild_asks_the_build_system(self):
        source = (ROOT / SHORTCUTSBAR_VIEW).read_text(encoding='utf-8')
        self.assertIn('describe_interpreter()', source)
        self.assertIn('interpreter_info=interpreter_info', source)


class DocumentPropertiesConsolidationTest(unittest.TestCase):
    '''每文档引擎只保留文档菜单一个入口；属性页只读显示。'''

    def test_properties_view_has_no_interpreter_combo(self):
        source = (ROOT / DOCUMENT_PROPERTIES_VIEW).read_text(encoding='utf-8')
        self.assertNotIn('combo_interpreter', source)
        self.assertIn('row_interpreter', source)

    def test_properties_apply_does_not_write_the_interpreter(self):
        source = (ROOT / DOCUMENT_PROPERTIES).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == 'DocumentPropertiesDialog')
        apply_fn = next(node for node in cls.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == '_on_apply')
        self.assertNotIn('latex_interpreter', ast.dump(apply_fn))

    def test_properties_row_reads_the_shared_resolver(self):
        source = (ROOT / DOCUMENT_PROPERTIES).read_text(encoding='utf-8')
        tree = ast.parse(source)
        cls = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef)
                   and node.name == 'DocumentPropertiesDialog')
        sync_fn = next(node for node in cls.body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == '_sync_interpreter_row')
        rendered = ast.dump(sync_fn)
        self.assertIn('describe_interpreter', rendered)
        self.assertNotIn('DocumentSettings', rendered)


if __name__ == '__main__':
    unittest.main()

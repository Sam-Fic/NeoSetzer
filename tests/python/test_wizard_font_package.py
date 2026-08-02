#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：document_wizard 的字体包选择 (Problem 5) 与 preamble 完整性
# (Problem 2 重构回归防护)。
#
# 测试目标:
#   1. _get_font_package_line: lmodern / fontspec / none 三种选择各生成
#      正确的 \usepackage 行。
#   2. _get_preamble_packages: 默认配置下包含 \usepackage[utf8]{inputenc}
#      (Problem 2 重构曾遗漏此行, 导致 21/21 模板用例字节级不一致——
#      此测试防止该回归)。
#   3. 5 个文档类模板在 lmodern (默认) 与 fontspec 下均正确切换字体包行。
#
# 通过 ast 提取 DocumentWizard 类定义执行, 绕开 setzer.* 的 Gtk import 链,
# 与 test_wizard_presets.py / test_wizard_page_map.py 的 gi-free 风格一致。

import ast
import os
import sys
import types
import unittest
import xml.etree.ElementTree as ET

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# 桩 gettext (setzer.in 在运行时注入 _, 测试环境手动注入)
import builtins
if not hasattr(builtins, '_'):
    builtins._ = lambda s: s
if not hasattr(builtins, 'ngettext'):
    builtins.ngettext = lambda s, p, n: s if n == 1 else p


def _load_document_wizard_class():
    '''从 setzer/dialogs/document_wizard/document_wizard.py 用 ast 提取
    DocumentWizard 类, 在隔离 namespace 中 exec, 返回类对象。
    避开顶层 `import setzer.dialogs.document_wizard.document_wizard_viewgtk`
    等触发的 Gtk 真实依赖。'''
    src_path = os.path.join(REPO, 'setzer/dialogs/document_wizard/document_wizard.py')
    tree = ast.parse(open(src_path).read())
    cls_node = next(n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == 'DocumentWizard')

    def get_languages_dict():
        d = {}
        xml_path = os.path.join(REPO, 'data/resources/latexdb/languages/languages.xml')
        for child in ET.parse(xml_path).getroot():
            d[child.attrib['code']] = _(child.attrib['name'])
        return d

    LaTeXDB = type('LaTeXDB', (),
                    {'get_languages_dict': staticmethod(get_languages_dict)})
    ns = {
        '_': _,
        'ngettext': builtins.ngettext,
        'LaTeXDB': LaTeXDB,
        'pickle': __import__('pickle'),
        'os': os,
        'Gdk': types.SimpleNamespace(keyval_from_name=lambda name: 0),
    }
    exec(compile(ast.Module(body=[cls_node], type_ignores=[]),
                 src_path, 'exec'), ns)
    return ns['DocumentWizard']


DocumentWizard = _load_document_wizard_class()


def _make_instance(font_package='lmodern'):
    '''构造 DocumentWizard 实例 (绕过 __init__ 的 main_window 依赖),
    直接赋值模板生成所需属性。'''
    inst = DocumentWizard.__new__(DocumentWizard)
    inst.current_values = {
        'title': 'T', 'author': 'A', 'date': '\\today',
        'font_package': font_package,
        'languages': {'english': 'English'},
        'packages': {
            'ams': True, 'graphicx': True, 'color': True, 'xcolor': True,
            'url': True, 'theorem': False, 'textcomp': True,
            'listings': False, 'hyperref': False, 'glossaries': False,
            'parskip': True,
        },
        'article': {'page_format': 'US Letter', 'font_size': 11,
                    'option_twocolumn': False, 'option_default_margins': True,
                    'margin_left': 3.5, 'margin_right': 3.5,
                    'margin_top': 3.5, 'margin_bottom': 3.5,
                    'is_landscape': False},
        'report': {'page_format': 'US Letter', 'font_size': 11,
                   'option_twocolumn': False, 'option_default_margins': True,
                   'margin_left': 3.5, 'margin_right': 3.5,
                   'margin_top': 3.5, 'margin_bottom': 3.5,
                   'is_landscape': False},
        'book': {'page_format': 'US Letter', 'font_size': 11,
                 'option_twocolumn': False, 'option_default_margins': True,
                 'margin_left': 3.5, 'margin_right': 3.5,
                 'margin_top': 3.5, 'margin_bottom': 3.5,
                 'is_landscape': False},
        'letter': {'page_format': 'US Letter', 'font_size': 11,
                   'option_default_margins': True,
                   'margin_left': 3.5, 'margin_right': 3.5,
                   'margin_top': 3.5, 'margin_bottom': 3.5},
        'beamer': {'theme': 'default', 'option_show_navigation': True,
                   'option_top_align': True},
    }
    inst.page_formats = {
        'US Letter': 'letterpaper', 'US Legal': 'legalpaper',
        'A4': 'a4paper', 'A5': 'a5paper', 'B5': 'b5paper',
    }
    return inst


class TestFontPackageLine(unittest.TestCase):

    def test_lmodern(self):
        inst = _make_instance('lmodern')
        self.assertEqual(inst._get_font_package_line(), '\\usepackage{lmodern}\n')

    def test_fontspec(self):
        inst = _make_instance('fontspec')
        self.assertEqual(inst._get_font_package_line(), '\\usepackage{fontspec}\n')

    def test_none_returns_empty(self):
        inst = _make_instance('none')
        self.assertEqual(inst._get_font_package_line(), '')

    def test_missing_key_defaults_to_empty(self):
        # font_package 键缺失时 .get('font_package', 'lmodern') 回退到 lmodern。
        # 此用例确认默认值行为: 没有显式 'none' 就不会返回空串。
        inst = _make_instance('lmodern')
        del inst.current_values['font_package']
        self.assertEqual(inst._get_font_package_line(), '\\usepackage{lmodern}\n')

    def test_unknown_choice_returns_empty(self):
        # 未知值 (如 presets 被篡改) → 不生成字体包行, 避免生成无效 \usepackage。
        # 注意: load_presets 已对未知值做白名单校验回退到 lmodern, 此处仅测
        # _get_font_package_line 自身的防御行为。
        inst = _make_instance('invalid_pkg')
        self.assertEqual(inst._get_font_package_line(), '')


class TestPreambleIntegrity(unittest.TestCase):
    '''Problem 2 重构回归防护: preamble 必须保留 inputenc 行。'''

    def test_preamble_contains_inputenc(self):
        # 重构曾遗漏 \usepackage[utf8]{inputenc}, 此测试防止回归。
        inst = _make_instance('lmodern')
        preamble = inst._get_preamble_packages()
        self.assertIn('\\usepackage[utf8]{inputenc}', preamble)

    def test_preamble_contains_fontenc(self):
        inst = _make_instance('lmodern')
        self.assertIn('\\usepackage[T1]{fontenc}', inst._get_preamble_packages())

    def test_preamble_contains_babel(self):
        inst = _make_instance('lmodern')
        self.assertIn('\\usepackage[english]{babel}',
                      inst._get_preamble_packages())

    def test_preamble_order_fontenc_before_inputenc_before_babel(self):
        # 顺序: fontenc → inputenc → babel → 字体包 → 其他包
        inst = _make_instance('lmodern')
        preamble = inst._get_preamble_packages()
        i_fontenc = preamble.index('\\usepackage[T1]{fontenc}')
        i_inputenc = preamble.index('\\usepackage[utf8]{inputenc}')
        i_babel = preamble.index('\\usepackage[english]{babel}')
        i_lmodern = preamble.index('\\usepackage{lmodern}')
        self.assertLess(i_fontenc, i_inputenc)
        self.assertLess(i_inputenc, i_babel)
        self.assertLess(i_babel, i_lmodern)


class TestTemplatesSwitchFontPackage(unittest.TestCase):
    '''5 个文档类模板在 lmodern / fontspec / none 下的字体包行切换。'''

    ALL_TEMPLATES = [
        ('get_insert_text_article', 'article'),
        ('get_insert_text_report', 'report'),
        ('get_insert_text_book', 'book'),
        ('get_insert_text_letter', 'letter'),
        ('get_insert_text_beamer', 'beamer'),
    ]

    def _template_contains(self, method_name, font_package, expected_line):
        inst = _make_instance(font_package)
        start, end = getattr(inst, method_name)()
        full = start + end
        if expected_line is None:
            self.assertNotIn('\\usepackage{lmodern}', full)
            self.assertNotIn('\\usepackage{fontspec}', full)
        else:
            self.assertIn(expected_line, full)

    def test_lmodern_in_all_templates(self):
        for method, _cls in self.ALL_TEMPLATES:
            with self.subTest(method=method):
                self._template_contains(method, 'lmodern', '\\usepackage{lmodern}')

    def test_fontspec_in_all_templates(self):
        for method, _cls in self.ALL_TEMPLATES:
            with self.subTest(method=method):
                self._template_contains(method, 'fontspec', '\\usepackage{fontspec}')

    def test_none_in_all_templates(self):
        for method, _cls in self.ALL_TEMPLATES:
            with self.subTest(method=method):
                self._template_contains(method, 'none', None)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8

# 单元测试：setzer.helpers.symbol_categories
#
# 覆盖白名单内接受、路径遍历/绝对路径/空字符串/None 等拒绝场景。

import unittest

from setzer.helpers.symbol_categories import ALLOWED_CATEGORIES, is_valid_category


class TestAllowedCategories(unittest.TestCase):

    def test_six_categories_present(self):
        # 与 setzer.in 入口的 6 个 icon_theme folder 对齐
        expected = {
            'arrows', 'greek_letters', 'misc_math',
            'misc_text', 'operators', 'relations',
        }
        self.assertEqual(set(ALLOWED_CATEGORIES), expected)

    def test_is_frozenset_immutable(self):
        self.assertIsInstance(ALLOWED_CATEGORIES, frozenset)


class TestIsValidCategory(unittest.TestCase):

    def test_accepts_all_whitelisted(self):
        for cat in ALLOWED_CATEGORIES:
            self.assertTrue(is_valid_category(cat), f'{cat} 应通过白名单')

    def test_rejects_path_traversal(self):
        # 经典路径遍历尝试
        self.assertFalse(is_valid_category('../etc'))
        self.assertFalse(is_valid_category('../../etc/passwd'))
        self.assertFalse(is_valid_category('arrows/../../etc'))
        self.assertFalse(is_valid_category('..'))
        self.assertFalse(is_valid_category('./arrows'))

    def test_rejects_absolute_paths(self):
        self.assertFalse(is_valid_category('/etc/passwd'))
        self.assertFalse(is_valid_category('/home/user'))
        self.assertFalse(is_valid_category('/arrows'))

    def test_rejects_empty_and_whitespace(self):
        self.assertFalse(is_valid_category(''))
        self.assertFalse(is_valid_category(' '))
        self.assertFalse(is_valid_category('   '))
        self.assertFalse(is_valid_category('\t'))

    def test_rejects_none(self):
        self.assertFalse(is_valid_category(None))

    def test_rejects_non_string_types(self):
        self.assertFalse(is_valid_category(42))
        self.assertFalse(is_valid_category(['arrows']))
        self.assertFalse(is_valid_category({'category': 'arrows'}))
        self.assertFalse(is_valid_category(True))

    def test_rejects_lookalikes(self):
        # 大小写、前缀、后缀变体都不应通过
        self.assertFalse(is_valid_category('Arrows'))
        self.assertFalse(is_valid_category('ARROWS'))
        self.assertFalse(is_valid_category('arrows '))
        self.assertFalse(is_valid_category(' arrows'))
        self.assertFalse(is_valid_category('arrows2'))
        self.assertFalse(is_valid_category('myarrows'))

    def test_rejects_xml_extension(self):
        # 不应接受带 .xml 后缀的（拼接时已加 .xml）
        self.assertFalse(is_valid_category('arrows.xml'))

    def test_rejects_directory_separator(self):
        # 任何含路径分隔符的都拒绝
        self.assertFalse(is_valid_category('arrows/greek_letters'))
        self.assertFalse(is_valid_category('a/b'))


if __name__ == '__main__':
    unittest.main()

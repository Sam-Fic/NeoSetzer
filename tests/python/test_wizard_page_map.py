#!/usr/bin/env python3
# coding: utf-8

# 单元测试：setzer.dialogs.document_wizard.page_map
#
# 覆盖所有 7 个页面 × 5 个 document_class 组合下的 next/prev 转移、
# is_settings_page / is_before_or_at_general / is_before_general /
# is_at_or_after_general 边界。
#
# 注意：next_page / prev_page 在 document_class 非法时返回 None（与原
# document_wizard.py 的 if/elif 链无 else 分支行为一致：不跳转、留在原页），
# 而非回退到 GENERAL/FIRST。这避免了「未知 document_class 时点 next 仍跳页」
# 的行为变化。

import unittest

from setzer.dialogs.document_wizard.page_map import (
    DOCUMENT_CLASS_PAGE_INDEX, GENERAL_PAGE_INDEX,
    FIRST_CLASS_PAGE_INDEX, LAST_CLASS_PAGE_INDEX,
    CLASS_TO_SETTINGS_PAGE,
    next_page, prev_page,
    is_settings_page, is_before_or_at_general,
    is_before_general, is_at_or_after_general,
)


ALL_CLASSES = ['article', 'report', 'book', 'letter', 'beamer']
ALL_PAGES = [0, 1, 2, 3, 4, 5, 6]


class TestConstants(unittest.TestCase):

    def test_constants_match_setup_order(self):
        # 与 document_wizard.py 的 setup() 中 self.pages.append 顺序对齐
        self.assertEqual(DOCUMENT_CLASS_PAGE_INDEX, 0)
        self.assertEqual(GENERAL_PAGE_INDEX, 6)
        self.assertEqual(FIRST_CLASS_PAGE_INDEX, 1)
        self.assertEqual(LAST_CLASS_PAGE_INDEX, 5)

    def test_class_to_settings_page_mapping(self):
        expected = {'article': 1, 'report': 2, 'book': 3, 'letter': 4, 'beamer': 5}
        self.assertEqual(CLASS_TO_SETTINGS_PAGE, expected)


class TestNextPage(unittest.TestCase):

    def test_from_document_class_to_each_class_page(self):
        for cls in ALL_CLASSES:
            self.assertEqual(
                next_page(DOCUMENT_CLASS_PAGE_INDEX, cls),
                CLASS_TO_SETTINGS_PAGE[cls],
                f'从 DocumentClass 页 next 应到 {cls} 的设置页',
            )

    def test_from_document_class_unknown_class_returns_none(self):
        # 未知 document_class 返回 None（保留原 if/elif 链无 else 的行为）。
        # 调用方 goto_page_next 检查 None 后不跳转，留在 DocumentClass 页。
        self.assertIsNone(next_page(DOCUMENT_CLASS_PAGE_INDEX, 'unknown'))

    def test_from_settings_page_to_general(self):
        for cls in ALL_CLASSES:
            settings_page = CLASS_TO_SETTINGS_PAGE[cls]
            self.assertEqual(
                next_page(settings_page, cls),
                GENERAL_PAGE_INDEX,
                f'从 {cls} 设置页 next 应到 General 页',
            )

    def test_from_general_returns_none(self):
        for cls in ALL_CLASSES:
            self.assertIsNone(next_page(GENERAL_PAGE_INDEX, cls))


class TestPrevPage(unittest.TestCase):

    def test_from_general_back_to_class_settings_page(self):
        for cls in ALL_CLASSES:
            self.assertEqual(
                prev_page(GENERAL_PAGE_INDEX, cls),
                CLASS_TO_SETTINGS_PAGE[cls],
                f'从 General 页 prev 应回到 {cls} 的设置页',
            )

    def test_from_general_unknown_class_returns_none(self):
        # 与 next_page 对称：未知 document_class 返回 None。
        self.assertIsNone(prev_page(GENERAL_PAGE_INDEX, 'unknown'))

    def test_from_settings_page_back_to_document_class(self):
        for cls in ALL_CLASSES:
            settings_page = CLASS_TO_SETTINGS_PAGE[cls]
            self.assertEqual(
                prev_page(settings_page, cls),
                DOCUMENT_CLASS_PAGE_INDEX,
                f'从 {cls} 设置页 prev 应回到 DocumentClass 页',
            )

    def test_from_document_class_returns_none(self):
        for cls in ALL_CLASSES:
            self.assertIsNone(prev_page(DOCUMENT_CLASS_PAGE_INDEX, cls))


class TestRoundTripNavigation(unittest.TestCase):

    def test_next_then_prev_returns_to_settings_page(self):
        # 从 settings 页 next 到 General，再 prev 应回到同一 settings 页
        for cls in ALL_CLASSES:
            settings_page = CLASS_TO_SETTINGS_PAGE[cls]
            forward = next_page(settings_page, cls)
            self.assertEqual(forward, GENERAL_PAGE_INDEX)
            backward = prev_page(forward, cls)
            self.assertEqual(backward, settings_page)

    def test_prev_then_next_returns_to_document_class(self):
        # 从 settings 页 prev 到 DocumentClass，但 DocumentClass 的 next
        # 取决于 document_class，应回到同一 settings 页
        for cls in ALL_CLASSES:
            settings_page = CLASS_TO_SETTINGS_PAGE[cls]
            backward = prev_page(settings_page, cls)
            self.assertEqual(backward, DOCUMENT_CLASS_PAGE_INDEX)
            forward = next_page(backward, cls)
            self.assertEqual(forward, settings_page)


class TestHelpers(unittest.TestCase):

    def test_is_settings_page(self):
        self.assertFalse(is_settings_page(DOCUMENT_CLASS_PAGE_INDEX))
        for i in range(FIRST_CLASS_PAGE_INDEX, LAST_CLASS_PAGE_INDEX + 1):
            self.assertTrue(is_settings_page(i), f'{i} 应是 settings 页')
        self.assertFalse(is_settings_page(GENERAL_PAGE_INDEX))

    def test_is_settings_page_out_of_range(self):
        self.assertFalse(is_settings_page(-1))
        self.assertFalse(is_settings_page(7))
        self.assertFalse(is_settings_page(100))

    def test_is_before_or_at_general(self):
        for p in ALL_PAGES:
            self.assertTrue(is_before_or_at_general(p),
                            f'{p} <= GENERAL_PAGE_INDEX 应为 True')
        self.assertFalse(is_before_or_at_general(7))
        self.assertFalse(is_before_or_at_general(100))

    def test_is_before_or_at_general_negative(self):
        # 负数也 <= 6，虽然实际不会出现
        self.assertTrue(is_before_or_at_general(-1))

    def test_is_before_general(self):
        # page < GENERAL_PAGE_INDEX(6)：0-5 为 True，6+ 为 False
        for p in [0, 1, 2, 3, 4, 5]:
            self.assertTrue(is_before_general(p), f'{p} < 6 应为 True')
        self.assertFalse(is_before_general(GENERAL_PAGE_INDEX))
        self.assertFalse(is_before_general(7))

    def test_is_at_or_after_general(self):
        # page >= GENERAL_PAGE_INDEX(6)：6+ 为 True，0-5 为 False
        self.assertFalse(is_at_or_after_general(5))
        self.assertTrue(is_at_or_after_general(GENERAL_PAGE_INDEX))
        self.assertTrue(is_at_or_after_general(7))

    def test_is_before_and_at_or_after_are_complementary(self):
        # 在合法页面范围内，is_before_general 与 is_at_or_after_general 互补
        for p in ALL_PAGES:
            self.assertEqual(
                is_before_general(p),
                not is_at_or_after_general(p),
                f'page {p}: is_before_general 应为 is_at_or_after_general 的否定',
            )


if __name__ == '__main__':
    unittest.main()

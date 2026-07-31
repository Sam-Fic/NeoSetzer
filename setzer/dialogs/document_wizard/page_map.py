#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''文档向导页面索引映射。刻意不 import gi，便于单元测试。

页面顺序在 document_wizard.py 的 setup() 中通过 self.pages.append(...)
确定，索引与名称对应关系如下：

    0: DocumentClass
    1: StandardSettings (article/report/book + KOMA 变体)
    2: LetterSettings (letter + scrlttr2)
    3: BeamerSettings
    4: GeneralSettings

任何新增/删除页面须同步更新本表与 setup() 中的 self.pages.append 顺序。
'''


DOCUMENT_CLASS_PAGE_INDEX = 0
GENERAL_PAGE_INDEX = 4
FIRST_CLASS_PAGE_INDEX = 1
LAST_CLASS_PAGE_INDEX = 3


# document_class 字符串 → 该类的设置页索引
# article/report/book 及其 KOMA 变体合并到同一页
CLASS_TO_SETTINGS_PAGE = {
    'article': 1,
    'report': 1,
    'book': 1,
    'letter': 2,
    'beamer': 3,
    # KOMA-Script 类复用对应标准类的设置页（报告 #4）。
    'scrartcl': 1,
    'scrreprt': 1,
    'scrbook': 1,
    'scrlttr2': 2,
}


def next_page(current_page, document_class):
    '''返回下一页索引，或 None（无下一页 / document_class 非法）。

    在 DocumentClassPage（索引 0）上，下一页取决于 document_class。
    若 document_class 不在已知 5 类中（理论不可达，但守卫防御），返回 None
    —— 与原 ``if/elif`` 链无 else 分支的行为一致（不跳转，留在原页）。
    '''
    if current_page == DOCUMENT_CLASS_PAGE_INDEX:
        return CLASS_TO_SETTINGS_PAGE.get(document_class)
    if FIRST_CLASS_PAGE_INDEX <= current_page <= LAST_CLASS_PAGE_INDEX:
        return GENERAL_PAGE_INDEX
    return None


def prev_page(current_page, document_class):
    '''返回上一页索引，或 None（无上一页 / document_class 非法）。'''
    if current_page == GENERAL_PAGE_INDEX:
        return CLASS_TO_SETTINGS_PAGE.get(document_class)
    if FIRST_CLASS_PAGE_INDEX <= current_page <= LAST_CLASS_PAGE_INDEX:
        return DOCUMENT_CLASS_PAGE_INDEX
    return None


def is_settings_page(page_index):
    '''page_index 是否为某个文档类的设置页（1-3）。'''
    return FIRST_CLASS_PAGE_INDEX <= page_index <= LAST_CLASS_PAGE_INDEX


def is_before_or_at_general(page_index):
    '''page_index 是否 <= GENERAL_PAGE_INDEX（用于 next 按钮可见性判断）。'''
    return page_index <= GENERAL_PAGE_INDEX


def is_before_general(page_index):
    '''page_index 是否 < GENERAL_PAGE_INDEX（next 按钮可见 / 回车前进）。'''
    return page_index < GENERAL_PAGE_INDEX


def is_at_or_after_general(page_index):
    '''page_index 是否 >= GENERAL_PAGE_INDEX（create 按钮可见 / 回车创建）。'''
    return page_index >= GENERAL_PAGE_INDEX
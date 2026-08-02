#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
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

'''document_stats 的文案格式化。刻意不 import gi，便于单元测试。

国际化方案：定义模块级 ``_`` 函数，运行时委托 ``builtins._``
（由 setzer.in 入口注入 ``trans.gettext``）。用 ``_`` 而非自定义
``_tr`` 包装器：xgettext 默认识别 ``_`` 关键字，可自动提取字符串到
.pot 文件；同时提供 ``builtins._`` 未注入时的 identity 回退（开发/测试）。

测试时通过 ``builtins._`` 注入 identity 或 mock 函数即可：
    import builtins
    builtins._ = lambda s: s
'''

import builtins


def _(s):
    '''翻译函数。运行时委托 ``builtins._``，未注入时回退到原字符串。

    设为模块级函数（而非直接引用 ``builtins._``）的原因：
    1. xgettext 默认识别 ``_`` 关键字 → 字符串自动提取到 .pot
    2. ``builtins._`` 未注入时提供 identity 回退 → 测试环境无依赖
    3. ``builtins._`` 被删除后仍可调用 → 比 ``_()`` 直接查找更健壮
    '''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


def format_whole_document_markup(text_words, header_words, outside_words):
    '''整篇文档的统计文案（Pango markup）。

    text_words/header_words/outside_words 可为 int 或 '?'（texcount
    缺失或解析失败时）。统一用 str() 转换，避免类型分支。
    '''
    return _('The whole document has <b>{text_words}</b> words in text, '
             '<b>{header_words}</b> words in headers and '
             '<b>{outside_words}</b> words outside text (captions, ...).').format(
        text_words=str(text_words),
        header_words=str(header_words),
        outside_words=str(outside_words),
    )


def format_current_file_markup(displayname, text_words, header_words, outside_words):
    '''当前文件的统计文案（Pango markup）。'''
    return _('{file} has <b>{text_words}</b> words in text, '
             '<b>{header_words}</b> words in headers and '
             '<b>{outside_words}</b> words outside text (captions, ...).').format(
        file=displayname,
        text_words=str(text_words),
        header_words=str(header_words),
        outside_words=str(outside_words),
    )


def format_chars_lines_markup_whole(chars, chars_no_spaces, lines):
    '''整篇文档的字符数/行数（纯 Python 计数，不依赖 texcount）。

    对 CJK 用户尤其有用——texcount 的 word count 对中文意义有限，
    字符数更贴近「字数」概念。chars 含所有字符（含空白），chars_no_spaces
    排除空格/制表/换行。lines 为逻辑行数。
    '''
    return _('The whole document has <b>{chars}</b> characters '
             '(<b>{chars_no_spaces}</b> excluding whitespace) '
             'and <b>{lines}</b> lines.').format(
        chars=str(chars),
        chars_no_spaces=str(chars_no_spaces),
        lines=str(lines),
    )


def format_chars_lines_markup_current(displayname, chars, chars_no_spaces, lines):
    '''当前文件的字符数/行数（Pango markup）。'''
    return _('{file} has <b>{chars}</b> characters '
              '(<b>{chars_no_spaces}</b> excluding whitespace) '
              'and <b>{lines}</b> lines.').format(
        file=displayname,
        chars=str(chars),
        chars_no_spaces=str(chars_no_spaces),
        lines=str(lines),
    )


def format_selection_markup(words, chars, chars_no_spaces):
    '''选区统计文案。仅在有非空选区时显示。

    words 按空白分割计数（对 CJK 不完美但与多数编辑器一致）；
    chars 含所有字符，chars_no_spaces 排除空白。CJK 用户主要看 chars。
    '''
    return _('Selection: <b>{words}</b> words, <b>{chars}</b> characters '
             '(<b>{chars_no_spaces}</b> excluding whitespace).').format(
        words=str(words),
        chars=str(chars),
        chars_no_spaces=str(chars_no_spaces),
    )


def format_texcount_missing_markup():
    '''texcount 未安装时的提示文案（Pango markup，含安装指引链接）。

    替代原「静默隐藏整个统计面板」的行为：用户能看到为什么 word count
    缺失，以及如何安装。char/line 计数仍可用（纯 Python 实现）。
    '''
    return _('Word counts require <tt>texcount</tt> (part of TeX Live), '
             'which was not found. Character and line counts remain available '
             'above. See <a href="https://en.wikibooks.org/wiki/LaTeX/Installation">'
             'installation guide</a>.')

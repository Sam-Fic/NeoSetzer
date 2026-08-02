#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：setzer.workspace.sidebar.document_stats.stats_text
#
# 覆盖整文档/当前文件 markup 正确 format、_() 注入 identity 与 mock
# 翻译均可、数值转字符串、非 ASCII 文件名等。
#
# 注意：stats_text.py 现在直接用 _()（标准 gettext 关键字，xgettext 可提取），
# 不再用自定义 _tr() 包装器。测试 setUp 注入 builtins._ = lambda s: s（identity）
# 作为默认回退；需要测试翻译时注入 mock。

import builtins
import unittest

from setzer.workspace.sidebar.document_stats.stats_text import (
    format_whole_document_markup, format_current_file_markup,
    format_chars_lines_markup_whole, format_chars_lines_markup_current,
    format_selection_markup, format_texcount_missing_markup,
)
from setzer.workspace.sidebar.document_stats.document_stats import (
    count_chars_lines, count_words_simple,
)


class _IdentityTranslation:
    '''上下文管理器：临时设置 builtins._ 为 identity，退出时恢复。'''

    def __enter__(self):
        self._original = getattr(builtins, '_', None)
        builtins._ = lambda s: s
        return self

    def __exit__(self, *exc):
        if self._original is not None:
            builtins._ = self._original
        elif hasattr(builtins, '_'):
            del builtins._
        return False


class TestFormatWholeDocumentMarkup(unittest.TestCase):

    def setUp(self):
        # 默认注入 identity _()，使 format_* 可在无真实 gettext 时工作
        self._ctx = _IdentityTranslation()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__()

    def test_basic_numbers(self):
        markup = format_whole_document_markup(100, 20, 5)
        self.assertIn('<b>100</b>', markup)
        self.assertIn('<b>20</b>', markup)
        self.assertIn('<b>5</b>', markup)
        self.assertIn('words in text', markup)
        self.assertIn('words in headers', markup)
        self.assertIn('words outside text', markup)

    def test_question_mark_placeholder(self):
        # texcount 缺失或解析失败时传 '?'
        markup = format_whole_document_markup('?', '?', '?')
        self.assertIn('<b>?</b>', markup)
        self.assertEqual(markup.count('<b>?</b>'), 3)

    def test_zero_words(self):
        markup = format_whole_document_markup(0, 0, 0)
        self.assertIn('<b>0</b>', markup)

    def test_large_numbers(self):
        markup = format_whole_document_markup(1234567, 0, 0)
        self.assertIn('<b>1234567</b>', markup)

    def test_markup_structure_stable(self):
        # 签名缓存依赖 markup 字符串稳定，相同输入应产出相同输出
        m1 = format_whole_document_markup(100, 20, 5)
        m2 = format_whole_document_markup(100, 20, 5)
        self.assertEqual(m1, m2)


class TestFormatCurrentFileMarkup(unittest.TestCase):

    def setUp(self):
        self._ctx = _IdentityTranslation()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__()

    def test_basic(self):
        markup = format_current_file_markup('doc.tex', 50, 10, 3)
        self.assertIn('doc.tex', markup)
        self.assertIn('<b>50</b>', markup)
        self.assertIn('<b>10</b>', markup)
        self.assertIn('<b>3</b>', markup)

    def test_non_ascii_filename(self):
        markup = format_current_file_markup('文档.tex', 1, 0, 0)
        self.assertIn('文档.tex', markup)

    def test_filename_with_spaces(self):
        markup = format_current_file_markup('my doc.tex', 5, 2, 1)
        self.assertIn('my doc.tex', markup)


class TestTranslationInjection(unittest.TestCase):
    '''验证 _() 注入的 mock 翻译能被 format_* 正确使用。'''

    def setUp(self):
        self._original_underscore = getattr(builtins, '_', None)

    def tearDown(self):
        if self._original_underscore is not None:
            builtins._ = self._original_underscore
        elif hasattr(builtins, '_'):
            del builtins._

    def test_identity_returns_original(self):
        builtins._ = lambda s: s
        self.assertEqual(format_whole_document_markup(1, 0, 0).count('words in text'), 1)

    def test_format_whole_document_uses_translation(self):
        # 模拟翻译：把 "words in text" 翻译成 "WORTER"
        def fake_gettext(s):
            return s.replace('words in text', 'WORTER').replace('words in headers', 'WORTER_K').replace('words outside text', 'WORTER_O')
        builtins._ = fake_gettext
        markup = format_whole_document_markup(1, 2, 3)
        self.assertIn('WORTER', markup)
        self.assertIn('WORTER_K', markup)
        self.assertIn('WORTER_O', markup)
        # 数字仍正确填入
        self.assertIn('<b>1</b>', markup)

    def test_format_current_file_uses_translation(self):
        def fake_gettext(s):
            return '[ZH]' + s
        builtins._ = fake_gettext
        markup = format_current_file_markup('doc.tex', 1, 2, 3)
        self.assertTrue(markup.startswith('[ZH]'))
        self.assertIn('doc.tex', markup)


class TestFormatCharsLinesMarkup(unittest.TestCase):
    '''字符/行数文案（纯 Python 计数，CJK 友好）。'''

    def setUp(self):
        self._ctx = _IdentityTranslation()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__()

    def test_whole_basic(self):
        markup = format_chars_lines_markup_whole(1000, 800, 42)
        self.assertIn('<b>1000</b>', markup)
        self.assertIn('<b>800</b>', markup)
        self.assertIn('<b>42</b>', markup)
        self.assertIn('characters', markup)
        self.assertIn('lines', markup)

    def test_current_basic(self):
        markup = format_chars_lines_markup_current('doc.tex', 500, 400, 20)
        self.assertIn('doc.tex', markup)
        self.assertIn('<b>500</b>', markup)
        self.assertIn('<b>400</b>', markup)
        self.assertIn('<b>20</b>', markup)

    def test_question_mark_placeholder(self):
        # 首次打开、尚未计数时传 '?'
        markup = format_chars_lines_markup_current('doc.tex', '?', '?', '?')
        self.assertEqual(markup.count('<b>?</b>'), 3)

    def test_cjk_filename(self):
        markup = format_chars_lines_markup_current('论文.tex', 100, 90, 5)
        self.assertIn('论文.tex', markup)


class TestFormatSelectionMarkup(unittest.TestCase):
    '''选区统计文案。'''

    def setUp(self):
        self._ctx = _IdentityTranslation()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__()

    def test_basic(self):
        markup = format_selection_markup(12, 80, 65)
        self.assertIn('<b>12</b>', markup)
        self.assertIn('<b>80</b>', markup)
        self.assertIn('<b>65</b>', markup)
        self.assertIn('Selection', markup)

    def test_zero_words_empty_selection(self):
        # 空选区不应进入本路径（controller 会 hide），但函数本身应能处理 0
        markup = format_selection_markup(0, 0, 0)
        self.assertIn('<b>0</b>', markup)


class TestFormatTexcountMissingMarkup(unittest.TestCase):
    '''texcount 缺失提示文案。'''

    def setUp(self):
        self._ctx = _IdentityTranslation()
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__()

    def test_contains_install_hint_and_link(self):
        markup = format_texcount_missing_markup()
        # 应包含 texcount 关键词、安装指引链接
        self.assertIn('texcount', markup)
        self.assertIn('<a href=', markup)
        # 应说明 char/line 仍可用
        self.assertIn('Character', markup)

    def test_stable_across_calls(self):
        # 无参数，每次调用应返回相同字符串（签名缓存依赖）
        self.assertEqual(format_texcount_missing_markup(), format_texcount_missing_markup())


class TestCountCharsLines(unittest.TestCase):
    '''纯 Python 字符/行数计数。'''

    def test_empty(self):
        self.assertEqual(count_chars_lines(''), (0, 0, 0))
        self.assertEqual(count_chars_lines(None), (0, 0, 0))

    def test_simple_ascii(self):
        text = 'hello world\nfoo bar'
        chars, no_spaces, lines = count_chars_lines(text)
        # len('hello world\nfoo bar') = 19
        self.assertEqual(chars, 19)
        # 非空白字符: helloworldfoobar = 16
        self.assertEqual(no_spaces, 16)
        # 两行
        self.assertEqual(lines, 2)

    def test_cjk_text(self):
        # CJK 字符不应被 isspace() 当作空白
        text = '你好世界\n第二行'
        chars, no_spaces, lines = count_chars_lines(text)
        self.assertEqual(chars, len(text))
        # 所有 CJK 字符都不是空白，no_spaces 应等于去掉 \n 后的长度
        self.assertEqual(no_spaces, len(text.replace('\n', '')))
        self.assertEqual(lines, 2)

    def test_mixed_whitespace(self):
        # 制表符、\r\n 都应算空白
        text = 'a\tb\r\nc'
        chars, no_spaces, lines = count_chars_lines(text)
        self.assertEqual(chars, len(text))
        # 非空白: a, b, c = 3
        self.assertEqual(no_spaces, 3)
        # splitlines 把 \r\n 算一行边界 → 2 行
        self.assertEqual(lines, 2)

    def test_trailing_newline(self):
        # 末尾换行：splitlines 不产生空尾行
        text = 'line1\nline2\n'
        self.assertEqual(count_chars_lines(text)[2], 2)

    def test_cjk_no_spaces_counts_each_char(self):
        # 对纯中文（无空格），no_spaces = 字符数 = 字数概念
        text = '中文文本无空格'
        chars, no_spaces, lines = count_chars_lines(text)
        self.assertEqual(chars, len(text))
        self.assertEqual(no_spaces, len(text))
        self.assertEqual(lines, 1)


class TestCountWordsSimple(unittest.TestCase):
    '''简单词数计数（按空白分割）。'''

    def test_empty(self):
        self.assertEqual(count_words_simple(''), 0)
        self.assertEqual(count_words_simple(None), 0)

    def test_english(self):
        self.assertEqual(count_words_simple('hello world foo'), 3)

    def test_multiple_whitespace(self):
        # 多个连续空白应正确处理（split() 默认行为）
        self.assertEqual(count_words_simple('a   b\tc\n\nd'), 4)

    def test_cjk_treated_as_single_word(self):
        # 无空格的整段中文算 1 词——这是已知限制，CJK 用户应看字符数
        self.assertEqual(count_words_simple('你好世界'), 1)

    def test_mixed_cjk_english(self):
        # 中英混合按空格分割
        self.assertEqual(count_words_simple('hello 世界 foo'), 3)


if __name__ == '__main__':
    unittest.main()


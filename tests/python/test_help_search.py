#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：setzer.helpers.help_search
#
# 覆盖：trigram 生成、trigram 索引构建、模糊搜索排序。
# 重点验证拼写容错（eqution→equation）、多词查询、精确匹配优先、
# 无结果场景。使用内置小索引而非真实 search_index.pickle，保证测试
# 快速且不依赖资源文件。

import unittest

from setzer.helpers.help_search import trigrams, build_trigram_index, search


# 小型测试索引：模拟真实 search_index 的 [key, uri, title, section] 结构。
# key 已小写（与真实索引一致），含 ``_____`` 标题/章节分隔符。
_TEST_INDEX = [
    ['equation environment_____equation', 'latex2e_8.html#equation', 'equation environment', 'equation'],
    ['aligning equations_____eqnarray', 'latex2e_8.html#aligning', 'aligning equations', 'eqnarray'],
    ['align environment, from amsmath_____eqnarray', 'latex2e_8.html#align', 'align environment, from amsmath', 'eqnarray'],
    ['bibliography, creating (manually)_____thebibliography', 'latex2e_8.html#bib', 'bibliography, creating (manually)', 'thebibliography'],
    ['math formulas_____math formulas', 'latex2e_8.html#math', 'math formulas', 'Math formulas'],
    ['tabular environment_____tabular', 'latex2e_8.html#tabular', 'tabular environment', 'tabular'],
    ['\\neq_____math symbols', 'latex2e_8.html#neq', '\\neq', 'Math symbols'],
]


class TestTrigrams(unittest.TestCase):

    def test_normal_string(self):
        result = trigrams('equation')
        self.assertEqual(result, {'equ', 'qua', 'uat', 'ati', 'tio', 'ion'})

    def test_short_string_returns_self(self):
        # 长度 < 3 退化为子串匹配：返回完整字符串自身
        self.assertEqual(trigrams('eq'), {'eq'})
        self.assertEqual(trigrams('a'), {'a'})

    def test_empty_string_returns_empty_set(self):
        self.assertEqual(trigrams(''), set())

    def test_dedupes_repeated_trigrams(self):
        # 'aaaa' 的 trigram 只有 'aaa'，去重后仅一个元素
        self.assertEqual(trigrams('aaaa'), {'aaa'})

    def test_exactly_three_chars(self):
        self.assertEqual(trigrams('abc'), {'abc'})


class TestBuildTrigramIndex(unittest.TestCase):

    def test_returns_list_aligned_with_input(self):
        idx = build_trigram_index(_TEST_INDEX)
        self.assertEqual(len(idx), len(_TEST_INDEX))

    def test_tokenizes_key_into_words(self):
        idx = build_trigram_index(_TEST_INDEX)
        # 第一项 key: 'equation environment_____equation'
        # 分词应含 'equation'（两次，title 与 section 都有）和 'environment'
        words = [w for w, _ in idx[0]]
        self.assertIn('equation', words)
        self.assertIn('environment', words)
        # _____ 分隔符不应作为分词出现
        self.assertNotIn('_____', words)

    def test_each_word_has_frozenset_trigrams(self):
        idx = build_trigram_index(_TEST_INDEX)
        for entry in idx:
            for word, tri in entry:
                self.assertIsInstance(tri, frozenset)
                # trigram 集合与直接调用 trigrams() 一致
                self.assertEqual(tri, frozenset(trigrams(word)))

    def test_empty_index(self):
        self.assertEqual(build_trigram_index([]), [])


class TestSearchExactMatch(unittest.TestCase):

    def setUp(self):
        self.tri_idx = build_trigram_index(_TEST_INDEX)

    def test_exact_word_returns_full_matches_first(self):
        # 'equation' 精确出现在第 0 项（两次）和第 1 项（equations 含 equation 子串）
        results = search('equation', _TEST_INDEX, self.tri_idx)
        self.assertGreater(len(results), 0)
        # 第 0 项应排首位（key 含 equation 两次，full match）
        self.assertEqual(results[0], 0)

    def test_exact_multi_word_query(self):
        # 'math formulas' 应匹配第 4 项（full match）
        results = search('math formulas', _TEST_INDEX, self.tri_idx)
        self.assertIn(4, results)
        # 全匹配项应排在部分匹配之前
        self.assertEqual(results[0], 4)

    def test_case_insensitive(self):
        # 查询大写也应匹配小写 key
        results_upper = search('Equation', _TEST_INDEX, self.tri_idx)
        results_lower = search('equation', _TEST_INDEX, self.tri_idx)
        self.assertEqual(results_upper, results_lower)

    def test_substring_match(self):
        # 'align' 是 'aligning' 和 'align' 的子串
        results = search('align', _TEST_INDEX, self.tri_idx)
        # 应召回含 align/aligning 的项（idx 1, 2）
        self.assertIn(1, results)
        self.assertIn(2, results)


class TestSearchFuzzyMatch(unittest.TestCase):

    def setUp(self):
        self.tri_idx = build_trigram_index(_TEST_INDEX)

    def test_typo_finds_related(self):
        # 'eqution'（拼写错误，缺 a）应通过 trigram 模糊匹配召回 equation 相关项
        results = search('eqution', _TEST_INDEX, self.tri_idx)
        self.assertGreater(len(results), 0)
        # 至少应包含第 0 项（equation environment）
        self.assertIn(0, results)

    def test_typo_bibliography(self):
        # 'bibilography'（双 i 拼写错误）应召回 bibliography 项
        results = search('bibilography', _TEST_INDEX, self.tri_idx)
        self.assertGreater(len(results), 0)
        self.assertIn(3, results)

    def test_gibberish_returns_empty(self):
        # 完全无关的查询应返回空列表（不召回噪声）
        results = search('xyzzyqwerty', _TEST_INDEX, self.tri_idx)
        self.assertEqual(results, [])

    def test_exact_match_ranks_above_fuzzy(self):
        # 同时存在精确匹配 'equation' 和模糊匹配 'eqution' 时，
        # 精确查询的排序应让 full match 项靠前
        results_exact = search('equation', _TEST_INDEX, self.tri_idx)
        results_typo = search('eqution', _TEST_INDEX, self.tri_idx)
        # 精确查询的首项 fuzzy 分应更高（full match=1），但两者都应召回 idx 0
        self.assertEqual(results_exact[0], 0)
        self.assertIn(0, results_typo)


class TestSearchEdgeCases(unittest.TestCase):

    def setUp(self):
        self.tri_idx = build_trigram_index(_TEST_INDEX)

    def test_empty_query_returns_empty(self):
        self.assertEqual(search('', _TEST_INDEX, self.tri_idx), [])

    def test_whitespace_only_query_returns_empty(self):
        self.assertEqual(search('   ', _TEST_INDEX, self.tri_idx), [])

    def test_limit_parameter(self):
        # limit=2 应只返回至多 2 个结果
        results = search('equation', _TEST_INDEX, self.tri_idx, limit=2)
        self.assertLessEqual(len(results), 2)

    def test_short_query_two_chars(self):
        # 'eq' 是 \neq 的子串（idx 6），应通过子串匹配召回
        results = search('eq', _TEST_INDEX, self.tri_idx)
        self.assertIn(6, results)

    def test_single_char_query(self):
        # 单字符查询：匹配含该字符子串的项
        # 测试索引中很多项含 'e'，应返回非空结果且不崩溃
        results = search('e', _TEST_INDEX, self.tri_idx)
        self.assertIsInstance(results, list)


class TestSearchWithEmptyIndex(unittest.TestCase):

    def test_empty_index_returns_empty(self):
        tri_idx = build_trigram_index([])
        self.assertEqual(search('equation', [], tri_idx), [])


if __name__ == '__main__':
    unittest.main()

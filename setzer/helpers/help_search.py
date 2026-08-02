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

'''帮助面板搜索的纯逻辑层（无 GTK 依赖，便于单元测试）。

设计要点：
- **trigram 模糊匹配**：对查询词与索引项 key 的每个分词计算 trigram
  Jaccard 相似度，取最大值。支持拼写容错（``eqution`` → ``equation``）、
  多词查询（``math align`` → ``align environment``）。
- **逐词而非整串**：索引 key 形如 ``title words_____section``，整串 Jaccard
  会被无关词稀释；逐词匹配避免此问题。
- **三级排序**：全词精确子串命中优先 → 部分词命中 → 纯模糊相似度。
  保证精确查询行为不变，模糊仅作兜底召回。

剥离到 helpers/ 的动机见 ``conftest_stub.py`` 注释：优先把可测纯逻辑
放到 gi-free 模块，使其可在无 GTK 环境下直接测试。
'''

import re


# 分词正则：连续字母数字。``_____``（标题/章节分隔符）与标点自动排除。
# 模块级编译一次复用。
_TOKENIZER = re.compile(r'[a-z0-9]+')


def trigrams(text):
    '''生成 text 的 trigram 集合（连续 3 字符子串）。

    text 须已小写化。长度 < 3 时返回完整字符串自身作为唯一元素，
    保证短查询（如 ``eq``）也能参与匹配（退化为子串包含检查）。
    集合去重，避免长文本重复 trigram 抬高 Jaccard 分母。

    返回 set（可变），调用方需要不可变副本时自行 ``frozenset()``。
    '''
    if len(text) < 3:
        return {text} if text else set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


def build_trigram_index(search_index):
    '''为每个索引项预计算 key 的分词 + 每词 trigram 集合。

    search_index 项结构: ``[key, uri_ending, title, section]``
    key 须已小写（构建索引时统一处理）。

    返回与 search_index 对齐的列表：每项是 ``[(word, frozenset_trigrams), ...]``。
    frozenset 便于 ``&`` 运算返回 set 求长度，且不可变可哈希。

    2080 项 × 平均 ~5 分词 × ~5 trigrams ≈ 5 万短字符串，内存可忽略；
    构建一次约 5ms，远小于 pickle.load 的一次性开销。
    '''
    index = []
    for item in search_index:
        words = _TOKENIZER.findall(item[0])
        entry = [(w, frozenset(trigrams(w))) for w in words]
        index.append(entry)
    return index


def search(query, search_index, trigram_index, limit=8, threshold=0.3):
    '''模糊搜索帮助索引，返回排序后的索引下标列表。

    参数：
        query: 用户输入（原始大小写，内部小写化）。
        search_index: ``[[key, uri, title, section], ...]``。
        trigram_index: ``build_trigram_index(search_index)`` 的返回值。
        limit: 最多返回的下标数（默认 8，与 UI 显示上限一致）。
        threshold: 逐词 Jaccard 阈值（默认 0.3）。
            ``eqution`` vs ``equation`` = 0.375，``formla`` vs ``formula`` = 0.286
            （转置错误 trigram 容忍度低，属已知限制）。0.3 平衡召回与噪声。

    返回：按相关性降序排列的 ``search_index`` 下标列表，至多 ``limit`` 项。
    调用方用下标取 ``item[1:]``（uri/title/section）渲染结果。

    排序规则（降序）：
        1. ``full_match``：所有查询词在 key 某分词中精确子串命中
        2. ``matched``：精确命中的查询词数
        3. ``fuzzy_avg``：每查询词最佳逐词 Jaccard 的平均值（0..1）

    候选条件：至少一个词精确命中，或平均模糊相似度 ≥ threshold。
    无任何候选时返回空列表（调用方显示 "no results" 状态）。
    '''
    words = query.split()
    if not words:
        return []
    words_lower = [w.lower() for w in words]
    # 预计算每个查询词的 trigram 集合，避免循环内重复生成。
    query_word_trigrams = [frozenset(trigrams(w)) for w in words_lower]
    n_words = len(words_lower)

    candidates = []
    for idx in range(len(search_index)):
        entry = trigram_index[idx]  # list of (word, frozenset_trigrams)

        matched = 0
        fuzzy_sum = 0.0
        for qw, qw_tri in zip(words_lower, query_word_trigrams):
            best_sim = 0.0
            qw_matched = False
            for kw, kw_tri in entry:
                if not qw_matched and qw in kw:
                    qw_matched = True
                # 逐词 Jaccard
                if qw_tri and kw_tri:
                    inter = len(qw_tri & kw_tri)
                    if inter > 0:
                        union = len(qw_tri) + len(kw_tri) - inter
                        sim = inter / union if union > 0 else 0.0
                        if sim > best_sim:
                            best_sim = sim
            if qw_matched:
                matched += 1
            fuzzy_sum += best_sim

        # 归一化模糊得分到 0..1，便于跨不同查询词数比较
        fuzzy_avg = fuzzy_sum / n_words if n_words > 0 else 0.0

        # 候选条件：至少一个词精确子串命中，或平均模糊相似度超阈值
        if matched > 0 or fuzzy_avg >= threshold:
            full = 1 if matched == n_words else 0
            candidates.append((full, matched, fuzzy_avg, idx))

    # 排序：full_match 降序 → matched 降序 → fuzzy 降序
    # Python sort 稳定，同分项保持原索引顺序（索引构建时的文档顺序）
    candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)

    return [idx for full, matched, fuzzy, idx in candidates[:limit]]

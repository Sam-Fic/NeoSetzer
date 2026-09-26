#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# GPL-3.0-or-later

'''snippet_cache 的纯逻辑测试：内存 LRU 语义 + 磁盘存储/清理（tempdir）。'''

import os
import tempfile
import unittest

from setzer.document.snippet_preview.snippet_cache import SnippetCache


class MemoryLruTest(unittest.TestCase):

    def test_put_get(self):
        cache = SnippetCache('/nonexistent')
        cache.put('a', object())
        self.assertIsNotNone(cache.get('a'))
        self.assertIsNone(cache.get('b'))

    def test_lru_evicts_oldest(self):
        cache = SnippetCache('/nonexistent', max_items=2)
        cache.put('a', 'A')
        cache.put('b', 'B')
        cache.get('a')  # a 变为最近使用
        cache.put('c', 'C')  # 应逐出 b
        self.assertIsNone(cache.get('b'))
        self.assertEqual(cache.get('a'), 'A')
        self.assertEqual(cache.get('c'), 'C')

    def test_overwrite_refreshes_recency(self):
        cache = SnippetCache('/nonexistent', max_items=2)
        cache.put('a', 'A1')
        cache.put('b', 'B')
        cache.put('a', 'A2')  # put 同键也算一次使用
        cache.put('c', 'C')
        self.assertIsNone(cache.get('b'))
        self.assertEqual(cache.get('a'), 'A2')

    def test_clear_memory(self):
        cache = SnippetCache('/nonexistent')
        cache.put('a', 'A')
        cache.clear_memory()
        self.assertIsNone(cache.get('a'))


class DiskCacheTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = SnippetCache(os.path.join(self.tmp.name, 'cache'))

    def test_store_and_has(self):
        src = os.path.join(self.tmp.name, 'out.pdf')
        with open(src, 'wb') as f:
            f.write(b'%PDF-1.4 fake')
        target = self.cache.store_pdf(src, 'deadbeef')
        self.assertTrue(os.path.isfile(target))
        self.assertTrue(self.cache.has_pdf('deadbeef'))
        self.assertFalse(os.path.exists(src))  # move 语义：源文件不再存在
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'%PDF-1.4 fake')

    def test_store_overwrites_same_key(self):
        for i, content in enumerate((b'one', b'two')):
            src = os.path.join(self.tmp.name, f'out{i}.pdf')
            with open(src, 'wb') as f:
                f.write(content)
            target = self.cache.store_pdf(src, 'key1')
            with open(target, 'rb') as f:
                self.assertEqual(f.read(), content)

    def test_prune_oldest_beyond_limit(self):
        cache_dir = os.path.join(self.tmp.name, 'prune')
        cache = SnippetCache(cache_dir, max_disk_files=3)
        for i in range(5):
            src = os.path.join(self.tmp.name, f'p{i}.pdf')
            with open(src, 'wb') as f:
                f.write(b'x' * (i + 1))
            os.utime(src, (1000 + i, 1000 + i))  # 可控 mtime 排序
            cache.store_pdf(src, f'k{i}')
        names = sorted(os.listdir(cache_dir))
        self.assertEqual(names, ['k2.pdf', 'k3.pdf', 'k4.pdf'])  # 最旧的 k0/k1 被清理

    def test_pdf_path_shape(self):
        path = self.cache.pdf_path('abc123')
        self.assertTrue(path.endswith(os.path.join('cache', 'abc123.pdf')))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8

# 单元测试：setzer.helpers.document_state_paths
#
# 覆盖：
# - state_paths 命名格式（hash+basename）
# - legacy_state_paths 命名格式（base64）
# - 不同路径不冲突（同 basename 不同目录 / 仅大小写不同）
# - 文件名长度有界（长路径不会爆文件系统名长上限）
# - basename 中的特殊字符被替换为 _
# - hash 部分确定性（同路径每次结果一致）

import os
import tempfile
import unittest

from setzer.helpers.document_state_paths import (
    state_paths, legacy_state_paths,
)


class TestStatePathsFormat(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_format_is_hash_underscore_underscore_basename(self):
        json_path, pickle_path = state_paths('/home/user/docs/paper.tex', self.tmp)
        # 文件名应为 <16hex>__paper.tex.json
        filename = os.path.basename(json_path)
        # 16 hex + '__' + basename + '.json'
        self.assertRegex(filename, r'^[0-9a-f]{16}__paper\.tex\.json$')
        self.assertTrue(pickle_path.endswith('__paper.tex.pickle'))

    def test_hash_is_first_16_of_sha256(self):
        import hashlib
        path = '/home/user/docs/paper.tex'
        expected_hash = hashlib.sha256(path.encode('utf-8')).hexdigest()[:16]
        json_path, _ = state_paths(path, self.tmp)
        filename = os.path.basename(json_path)
        self.assertTrue(filename.startswith(expected_hash + '__'))

    def test_returns_json_and_pickle_in_config_folder(self):
        json_path, pickle_path = state_paths('/foo/bar.tex', self.tmp)
        self.assertTrue(json_path.startswith(self.tmp + os.sep))
        self.assertTrue(pickle_path.startswith(self.tmp + os.sep))
        self.assertTrue(json_path.endswith('.json'))
        self.assertTrue(pickle_path.endswith('.pickle'))

    def test_deterministic_same_input_same_output(self):
        path = '/home/user/docs/paper.tex'
        j1, p1 = state_paths(path, self.tmp)
        j2, p2 = state_paths(path, self.tmp)
        self.assertEqual(j1, j2)
        self.assertEqual(p1, p2)


class TestStatePathsCollisionAvoidance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_same_basename_different_dirs_do_not_collide(self):
        # 两个不同目录下的 paper.tex 必须映射到不同状态文件，
        # 否则用户的两个文档状态会互相覆盖。
        j1, _ = state_paths('/home/user/paper1/paper.tex', self.tmp)
        j2, _ = state_paths('/home/user/paper2/paper.tex', self.tmp)
        self.assertNotEqual(j1, j2)
        # basename 部分相同，差异在 hash 前缀
        self.assertTrue(os.path.basename(j1).endswith('__paper.tex.json'))
        self.assertTrue(os.path.basename(j2).endswith('__paper.tex.json'))

    def test_paths_differing_only_in_case_do_not_collide(self):
        # 大小写不敏感文件系统上，路径仅大小写不同时，hash 前缀不同
        # → 完整文件名不同 → 不互相覆盖。
        j1, _ = state_paths('/home/user/Paper.tex', self.tmp)
        j2, _ = state_paths('/home/user/paper.tex', self.tmp)
        self.assertNotEqual(j1, j2)

    def test_unicode_path_handled(self):
        # Unicode 路径不应崩；basename 中的非 ASCII 字符替换为 _。
        j, _ = state_paths('/home/user/文档/论文.tex', self.tmp)
        filename = os.path.basename(j)
        # 仍是 16hex + '__' + sanitized + '.json' 格式
        self.assertRegex(filename, r'^[0-9a-f]{16}__.+\.json$')
        # 文档/论文.tex → 文档被替换（非 ASCII），论文.tex 部分也替换
        # 最终 basename 应仅含字母数字与 . _ -
        stem = filename[len('aaaaaaaaaaaaaaaa__'):-len('.json')]
        for c in stem:
            self.assertTrue(c.isalnum() or c in '._-',
                            'unexpected char {!r} in stem {!r}'.format(c, stem))


class TestStatePathsBoundedLength(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_long_path_does_not_exceed_filename_limit(self):
        # ext4/btrfs/APFS/NTFS 文件名上限 255 字节。原 base64 方案对长路径
        # 可能产生 200+ 字符的文件名，逼近上限。新方案 hash 固定 16 字符，
        # basename 受文档名本身长度限制（用户文档名通常 < 100 字符）。
        long_path = '/home/user/' + 'a' * 200 + '/paper.tex'
        j, _ = state_paths(long_path, self.tmp)
        filename = os.path.basename(j)
        # 16 (hash) + 2 (__) + len('paper.tex') + 5 ('.json') = 33 字符
        # basename 取自路径末段，不受路径总长度影响
        self.assertLessEqual(len(filename), 255,
                             'filename too long: {} chars'.format(len(filename)))
        self.assertTrue(filename.endswith('__paper.tex.json'))


class TestStatePathsSanitization(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_special_chars_in_basename_replaced_with_underscore(self):
        # basename 中的空格、括号等不应出现在文件名里（避免 shell 转义问题）
        j, _ = state_paths('/home/user/my paper (v2).tex', self.tmp)
        filename = os.path.basename(j)
        stem = filename[18:-len('.json')]  # strip 16hash + '__' prefix, '.json' suffix
        for c in stem:
            self.assertTrue(c.isalnum() or c in '._-',
                            'unexpected char {!r} in stem {!r}'.format(c, stem))

    def test_dot_dash_underscore_preserved(self):
        # 这些字符在文件名里是安全的，应保留
        j, _ = state_paths('/home/user/my-paper_v2.0.tex', self.tmp)
        filename = os.path.basename(j)
        self.assertIn('my-paper_v2.0.tex', filename)


class TestLegacyStatePaths(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_legacy_format_is_base64(self):
        import base64
        path = '/home/user/docs/paper.tex'
        j, p = legacy_state_paths(path, self.tmp)
        expected_stem = base64.urlsafe_b64encode(path.encode('utf-8')).decode()
        self.assertEqual(os.path.basename(j), expected_stem + '.json')
        self.assertEqual(os.path.basename(p), expected_stem + '.pickle')

    def test_legacy_and_new_differ(self):
        # 迁移前提：旧名与新名不同，否则 rename 是 no-op。
        path = '/home/user/docs/paper.tex'
        new_j, _ = state_paths(path, self.tmp)
        legacy_j, _ = legacy_state_paths(path, self.tmp)
        self.assertNotEqual(new_j, legacy_j)


class TestMigrationRoundTrip(unittest.TestCase):
    '''端到端：旧 base64 名文件 → 迁移 → 新名文件可读。

    复现 load_document_state 中迁移分支的行为，但绕过 DocumentSettings 类
    （需要 GTK）直接调 helpers。
    '''

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_legacy_json_gets_renamed_to_new_name(self):
        from setzer.helpers.persistence import save_json, load_json
        path = '/home/user/docs/paper.tex'
        new_json, _ = state_paths(path, self.tmp)
        legacy_json, _ = legacy_state_paths(path, self.tmp)

        # 旧名写一个 JSON
        save_json(legacy_json, {'folded_regions': [], 'build_log_data': None,
                                'has_been_built': False, 'build_time': 0,
                                'has_synctex_file': False, 'pdf_filename': None,
                                'pdf_date': None, 'xoffset': 0, 'yoffset': 0,
                                'zoom_level': 1.0, 'save_date': 0})
        self.assertTrue(os.path.exists(legacy_json))
        self.assertFalse(os.path.exists(new_json))

        # 模拟 load_document_state 的迁移分支
        if not os.path.exists(new_json):
            if os.path.exists(legacy_json):
                os.rename(legacy_json, new_json)

        # 旧名消失，新名出现，内容相同
        self.assertFalse(os.path.exists(legacy_json))
        self.assertTrue(os.path.exists(new_json))
        data = load_json(new_json)
        self.assertEqual(data['zoom_level'], 1.0)

    def test_legacy_pickle_gets_migrated_to_new_json(self):
        from setzer.helpers.persistence import (
            load_pickle_trusted, save_json, load_json, migrate_pickle_to_json)
        import pickle
        path = '/home/user/docs/old_paper.tex'
        new_json, _ = state_paths(path, self.tmp)
        _, legacy_pickle = legacy_state_paths(path, self.tmp)

        # 旧名写一个 pickle（用户自己的文件，可信）
        with open(legacy_pickle, 'wb') as f:
            pickle.dump({'zoom_level': 1.5, 'save_date': 100}, f)

        # 模拟 load_document_state 的迁移分支
        migrate_pickle_to_json(new_json, legacy_pickle)

        # 新名 JSON 出现，内容来自 pickle
        self.assertTrue(os.path.exists(new_json))
        data = load_json(new_json)
        self.assertEqual(data['zoom_level'], 1.5)
        self.assertEqual(data['save_date'], 100)

    def test_no_legacy_files_no_migration(self):
        # 全新用户：旧名和新名文件都不存在 → 迁移分支什么都不做，
        # load_json 返回 None，文档以无状态启动。
        from setzer.helpers.persistence import load_json
        path = '/home/user/docs/brand_new.tex'
        new_json, _ = state_paths(path, self.tmp)
        legacy_json, legacy_pickle = legacy_state_paths(path, self.tmp)

        self.assertFalse(os.path.exists(new_json))
        self.assertFalse(os.path.exists(legacy_json))
        self.assertFalse(os.path.exists(legacy_pickle))

        # 模拟 load 分支：什么迁移都不发生
        if not os.path.exists(new_json):
            if os.path.exists(legacy_json):
                pass
            elif os.path.exists(legacy_pickle):
                pass

        # 仍然没有新文件
        self.assertFalse(os.path.exists(new_json))
        self.assertIsNone(load_json(new_json))


if __name__ == '__main__':
    unittest.main()

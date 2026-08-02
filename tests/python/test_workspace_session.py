#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：.stzs 会话文件读取（双路径 + 受限反序列化）
#
# workspace.py 的 load_documents_from_session_file 调用
# helpers.persistence.try_migrate_session_file_pickle 实现双路径读取。
# 本测试直接验证 persistence 层的行为，并构造真实 .stzs 数据结构
# 验证往返保真。

import json
import os
import pickle
import tempfile
import unittest

from setzer.helpers.persistence import (
    save_json, try_migrate_session_file_pickle, RestrictedUnpickler,
)


def make_legacy_session_data():
    '''构造一个旧版 .stzs 文件应有的数据结构（基于 workspace.save_session）。'''
    return {
        'open_documents': {
            '/home/user/doc.tex': {
                'filename': '/home/user/doc.tex',
                'last_activated': 1700000000.5,
                'cursor_offset': 42,
                'scroll_offset': 12.5,
                'folded_regions': [
                    {'starting_line': 5, 'ending_line': 10},
                    {'starting_line': 20, 'ending_line': 30},
                ],
            },
        },
        'active_document_filename': '/home/user/doc.tex',
        'root_document_filename': '/home/user/doc.tex',
        'window_state': {
            'show_symbols': True,
            'show_document_structure': False,
            'show_preview': True,
            'show_help': False,
            'show_build_log': False,
        },
    }


def make_new_session_data():
    '''新版 .stzs（JSON）数据结构，与 legacy 相同（结构无变化）。'''
    return make_legacy_session_data()


class TestStzsReadPaths(unittest.TestCase):

    def test_read_legacy_pickle_stzs(self):
        legacy = make_legacy_session_data()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            with open(p, 'wb') as f:
                pickle.dump(legacy, f)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertTrue(was_pickle)
            self.assertEqual(data, legacy)
            # 关键字段完整保留
            self.assertIn('/home/user/doc.tex', data['open_documents'])
            self.assertEqual(
                data['open_documents']['/home/user/doc.tex']['folded_regions'],
                [{'starting_line': 5, 'ending_line': 10},
                 {'starting_line': 20, 'ending_line': 30}],
            )

    def test_read_new_json_stzs(self):
        new_data = make_new_session_data()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            save_json(p, new_data)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertFalse(was_pickle)
            self.assertEqual(data, new_data)

    def test_malicious_stzs_rejected(self):
        # 构造 RCE payload：GLOBAL os.system + 'rm -rf /' + REDUCE
        malicious = (
            b"\x80\x04\x95\x23\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x02os\x94\x8c\x06system\x94\x93\x94"
            b"\x8c\x08rm -rf /\x85R."
        )
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'evil.stzs')
            with open(p, 'wb') as f:
                f.write(malicious)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertIsNone(data)
            self.assertTrue(was_pickle)

    def test_json_preferred_over_pickle_when_both_paths_applicable(self):
        # 同一 .stzs 路径不可能同时是合法 JSON 和 pickle（pickle 字节流不是
        # 合法 JSON），但若文件内容是合法 JSON，was_pickle 应为 False
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            with open(p, 'w') as f:
                json.dump({'open_documents': {}}, f)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertFalse(was_pickle)
            self.assertEqual(data, {'open_documents': {}})


class TestRestrictedUnpicklerForSession(unittest.TestCase):

    def test_session_with_tuple_survives_restricted_unpickler(self):
        # 旧 .stzs 中可能含 tuple（如 cursor_offset 的位置元组）
        data = {'positions': (1, 2, 3), 'open_documents': {}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            with open(p, 'wb') as f:
                pickle.dump(data, f)
            with open(p, 'rb') as f:
                loaded = RestrictedUnpickler(f).load()
            self.assertEqual(loaded, data)
            self.assertIsInstance(loaded['positions'], tuple)

    def test_session_with_set_survives_restricted_unpickler(self):
        data = {'filenames': {'a.tex', 'b.tex'}, 'open_documents': {}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            with open(p, 'wb') as f:
                pickle.dump(data, f)
            with open(p, 'rb') as f:
                loaded = RestrictedUnpickler(f).load()
            self.assertEqual(loaded['filenames'], {'a.tex', 'b.tex'})


if __name__ == '__main__':
    unittest.main()

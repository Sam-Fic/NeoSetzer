#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：setzer.helpers.persistence
#
# 覆盖：JSON 往返、原子写、pickle 受限/信任读、migrate_pickle_to_json 幂等、
# try_migrate_session_file_pickle 双路径、RestrictedUnpickler 拒绝恶意 payload。

import json
import os
import pickle
import tempfile
import unittest
from unittest.mock import patch

import setzer.helpers.persistence as persistence
from setzer.helpers.persistence import (
    load_json, save_json,
    load_pickle_trusted, load_pickle_restricted, RestrictedUnpickler,
    migrate_pickle_to_json, try_migrate_session_file_pickle,
)


# 模块级类，用于测试 RestrictedUnpickler 拒绝非 builtins 类。
# pickle 不能序列化函数内定义的局部类（PicklingError），故放模块级。
class _EvilForTesting:
    value = 42


class TestJSONRoundTrip(unittest.TestCase):

    def test_roundtrip_preserves_types(self):
        data = {
            'str': 'hello',
            'int': 42,
            'float': 3.14,
            'bool_true': True,
            'bool_false': False,
            'none': None,
            'list': [1, 'x', None, True, [2, 3]],
            'nested': {'a': {'b': {'c': 'd'}}},
        }
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.json')
            save_json(p, data)
            self.assertEqual(load_json(p), data)

    def test_load_missing_file_returns_fallback(self):
        self.assertIsNone(load_json('/nonexistent/path/f.json'))
        self.assertEqual(load_json('/nonexistent/path/f.json', fallback={}), {})

    def test_load_corrupt_json_returns_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'bad.json')
            with open(p, 'w') as f:
                f.write('{not valid json')
            self.assertIsNone(load_json(p))

    def test_save_is_atomic_no_tmp_leftover(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.json')
            save_json(p, {'x': 1})
            self.assertFalse(os.path.exists(p + '.tmp'))
            self.assertEqual(load_json(p), {'x': 1})

    def test_save_utf8_non_ascii(self):
        # ensure_ascii=False：非 ASCII 字符直接 UTF-8 写入
        data = {'unicode': 'αβγ 你好 • ✓'}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.json')
            save_json(p, data)
            with open(p, 'rb') as f:
                content = f.read()
            # 不应含 \\uXXXX 转义
            self.assertNotIn(b'\\u', content)
            self.assertEqual(load_json(p), data)

    def test_save_flushes_written_file_and_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.json')
            with patch.object(persistence.os, 'fsync', wraps=os.fsync) as fsync:
                save_json(p, {'durable': True})
            # File fsync is mandatory; POSIX also fsyncs its parent directory.
            self.assertGreaterEqual(fsync.call_count, 1)
            self.assertEqual(load_json(p), {'durable': True})

    def test_encoding_error_preserves_existing_file_without_temp_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'f.json')
            save_json(p, {'previous': True})
            with self.assertRaises(TypeError):
                save_json(p, {'not_json': object()})
            self.assertEqual(load_json(p), {'previous': True})
            self.assertEqual(
                [name for name in os.listdir(d)
                 if name.startswith('.f.json.') and name.endswith('.tmp')],
                [])


class TestRestrictedUnpickler(unittest.TestCase):

    def test_accepts_plain_dict_with_nested_containers(self):
        # 合法 .stzs 数据：仅 dict/list/str/int/float/bool/None
        data = {
            'open_documents': {'f.tex': {'filename': 'f.tex', 'last_activated': 1.5}},
            'list': [1, 'x', None, True, (1, 2, 3)],
            'none': None,
        }
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'ok.pickle')
            with open(p, 'wb') as f:
                pickle.dump(data, f)
            self.assertEqual(load_pickle_restricted(p), data)

    def test_rejects_arbitrary_global(self):
        # 构造引用 os.system 的恶意 pickle（经典 RCE payload）。
        # 字节流：pickle protocol 4，GLOBAL os.system + 'id' 参数 + REDUCE。
        malicious = (
            b"\x80\x04\x95\x1a\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x02os\x94\x8c\x06system\x94\x93\x94\x8c\x02id\x85R."
        )
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'evil.pickle')
            with open(p, 'wb') as f:
                f.write(malicious)
            with self.assertRaises(pickle.UnpicklingError):
                load_pickle_restricted(p)

    def test_rejects_class_instance(self):
        # 自定义类的 pickle 应被拒绝（find_class 触发）
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'class.pickle')
            with open(p, 'wb') as f:
                pickle.dump(_EvilForTesting(), f)
            with self.assertRaises(pickle.UnpicklingError):
                load_pickle_restricted(p)

    def test_load_pickle_trusted_accepts_class(self):
        # 对照：load_pickle_trusted 不受限，可读自定义类
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'class.pickle')
            with open(p, 'wb') as f:
                pickle.dump(_EvilForTesting(), f)
            obj = load_pickle_trusted(p)
            self.assertEqual(obj.value, 42)


class TestMigratePickleToJson(unittest.TestCase):

    def test_migrate_when_json_absent_pickle_present(self):
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 's.json')
            pp = os.path.join(d, 's.pickle')
            with open(pp, 'wb') as f:
                pickle.dump({'x': 1, 'y': [2, 3]}, f)
            result = migrate_pickle_to_json(jp, pp)
            self.assertEqual(result, {'x': 1, 'y': [2, 3]})
            self.assertEqual(load_json(jp), {'x': 1, 'y': [2, 3]})

    def test_idempotent_when_json_already_exists(self):
        # JSON 已存在时跳过，即便 pickle 改了也不覆盖
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 's.json')
            pp = os.path.join(d, 's.pickle')
            save_json(jp, {'migrated': True})
            with open(pp, 'wb') as f:
                pickle.dump({'x': 999}, f)
            result = migrate_pickle_to_json(jp, pp)
            self.assertIsNone(result)
            self.assertEqual(load_json(jp), {'migrated': True})

    def test_skip_when_pickle_absent(self):
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 's.json')
            pp = os.path.join(d, 's.pickle')
            self.assertIsNone(migrate_pickle_to_json(jp, pp))
            self.assertFalse(os.path.exists(jp))

    def test_skip_when_pickle_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 's.json')
            pp = os.path.join(d, 's.pickle')
            with open(pp, 'wb') as f:
                f.write(b'not a pickle at all')
            self.assertIsNone(migrate_pickle_to_json(jp, pp))
            self.assertFalse(os.path.exists(jp))

    def test_migrate_value_callback_applied(self):
        # migrate_value 用于在落盘前修正结构（如解嵌套 pickle bytes）
        def decode_bytes(data):
            for k, v in list(data.items()):
                if isinstance(v, (bytes, bytearray)):
                    data[k] = pickle.loads(v)
            return data
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 's.json')
            pp = os.path.join(d, 's.pickle')
            with open(pp, 'wb') as f:
                pickle.dump({'nested': pickle.dumps({'inner': 1})}, f)
            result = migrate_pickle_to_json(jp, pp, migrate_value=decode_bytes)
            self.assertEqual(result, {'nested': {'inner': 1}})
            self.assertEqual(load_json(jp), {'nested': {'inner': 1}})


class TestSessionFileDualPath(unittest.TestCase):

    def test_json_preferred_over_pickle(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            # 注意：写 JSON 内容到 .stzs 文件
            with open(p, 'w') as f:
                json.dump({'json': True, 'open_documents': {}}, f)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertEqual(data, {'json': True, 'open_documents': {}})
            self.assertFalse(was_pickle)

    def test_pickle_fallback_for_legacy_stzs(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'session.stzs')
            with open(p, 'wb') as f:
                pickle.dump({'pickle': True, 'open_documents': {}}, f)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertEqual(data, {'pickle': True, 'open_documents': {}})
            self.assertTrue(was_pickle)

    def test_malicious_pickle_rejected(self):
        malicious = (
            b"\x80\x04\x95\x1a\x00\x00\x00\x00\x00\x00\x00"
            b"\x8c\x02os\x94\x8c\x06system\x94\x93\x94\x8c\x02id\x85R."
        )
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'evil.stzs')
            with open(p, 'wb') as f:
                f.write(malicious)
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertIsNone(data)
            self.assertTrue(was_pickle)

    def test_missing_file_returns_none(self):
        data, was_pickle = try_migrate_session_file_pickle('/nonexistent/f.stzs')
        self.assertIsNone(data)
        self.assertFalse(was_pickle)

    def test_corrupt_pickle_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'bad.stzs')
            with open(p, 'wb') as f:
                f.write(b'garbage not pickle')
            data, was_pickle = try_migrate_session_file_pickle(p)
            self.assertIsNone(data)
            self.assertTrue(was_pickle)


if __name__ == '__main__':
    unittest.main()

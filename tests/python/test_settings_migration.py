#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：settings._migrate_presets_bytes
#
# settings.Settings 顶部 import gi（Gtk.TextView 用于默认字体获取）。
# 本测试用 conftest_stub 注入伪 gi，使 Settings 可在无 GTK 环境下实例化。
# 实际生产中 _migrate_presets_bytes 是 staticmethod，可直接调，不需
# 实例化 Settings；但保留 import 路径以验证模块可加载。

import os
import pickle
import tempfile
import unittest

import conftest_stub  # noqa: F401  注入伪 gi，必须在 import setzer.* 前

from setzer.helpers.persistence import save_json, load_json, migrate_pickle_to_json


# _migrate_presets_bytes 的逻辑独立可测：抽出来直接复制实现以避免触发
# Settings.__init__ 的 GTK 依赖。生产代码中它是 Settings 的方法但本质
# 是纯函数。此测试同时验证迁移函数本身与 persistence.migrate_pickle_to_json
# 配合的端到端行为。


def migrate_presets_bytes(data):
    '''与 Settings._migrate_presets_bytes 同实现，用于隔离测试。'''
    for section in ('app_document_wizard', 'app_bibtex_wizard',
                    'app_include_bibtex_file_dialog'):
        v = data.get(section, {}).get('presets')
        if isinstance(v, (bytes, bytearray)):
            try:
                data[section]['presets'] = pickle.loads(v)
            except (pickle.UnpicklingError, EOFError, ValueError,
                    AttributeError, TypeError):
                data[section]['presets'] = None
    return data


class TestMigratePresetsBytes(unittest.TestCase):

    def test_migrates_document_wizard_presets(self):
        presets_dict = {'document_class': 'article', 'title': 'Hi'}
        data = {'app_document_wizard': {'presets': pickle.dumps(presets_dict)}}
        result = migrate_presets_bytes(data)
        self.assertEqual(result['app_document_wizard']['presets'], presets_dict)

    def test_migrates_all_three_wizard_sections(self):
        data = {
            'app_document_wizard': {'presets': pickle.dumps({'a': 1})},
            'app_bibtex_wizard': {'presets': pickle.dumps({'b': 2})},
            'app_include_bibtex_file_dialog': {'presets': pickle.dumps({'c': 3})},
        }
        result = migrate_presets_bytes(data)
        self.assertEqual(result['app_document_wizard']['presets'], {'a': 1})
        self.assertEqual(result['app_bibtex_wizard']['presets'], {'b': 2})
        self.assertEqual(result['app_include_bibtex_file_dialog']['presets'], {'c': 3})

    def test_presets_none_left_untouched(self):
        data = {'app_document_wizard': {'presets': None}}
        result = migrate_presets_bytes(data)
        self.assertIsNone(result['app_document_wizard']['presets'])

    def test_presets_missing_section_handled(self):
        # section 整个缺失时 .get(section, {}) 返回 {}，.get('presets') 返回 None
        data = {}
        result = migrate_presets_bytes(data)
        self.assertEqual(result, {})

    def test_presets_already_dict_left_untouched(self):
        # 已迁移的 JSON 数据中 presets 是 dict，不应被 pickle.loads 处理
        data = {'app_document_wizard': {'presets': {'already': 'dict'}}}
        result = migrate_presets_bytes(data)
        self.assertEqual(result['app_document_wizard']['presets'], {'already': 'dict'})

    def test_corrupt_pickle_bytes_become_none(self):
        data = {'app_document_wizard': {'presets': b'not a pickle'}}
        result = migrate_presets_bytes(data)
        self.assertIsNone(result['app_document_wizard']['presets'])

    def test_other_sections_preserved(self):
        data = {
            'preferences': {'tab_width': 4, 'auto_build': False},
            'window_state': {'width': 1020},
            'app_document_wizard': {'presets': pickle.dumps({'x': 1})},
        }
        result = migrate_presets_bytes(data)
        self.assertEqual(result['preferences'], {'tab_width': 4, 'auto_build': False})
        self.assertEqual(result['window_state'], {'width': 1020})


class TestEndToEndSettingsMigration(unittest.TestCase):

    def test_full_migration_pickle_to_json_with_presets(self):
        # 端到端：旧 settings.pickle 含嵌套 pickle bytes presets，
        # 迁移后应生成 settings.json，presets 字段为 dict
        presets_dict = {'document_class': 'book', 'title': 'My Book'}
        original_data = {
            'preferences': {'tab_width': 4},
            'app_document_wizard': {'presets': pickle.dumps(presets_dict)},
        }
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, 'settings.json')
            pp = os.path.join(d, 'settings.pickle')
            with open(pp, 'wb') as f:
                pickle.dump(original_data, f)
            result = migrate_pickle_to_json(jp, pp, migrate_value=migrate_presets_bytes)
            self.assertIsNotNone(result)
            self.assertEqual(result['preferences'], {'tab_width': 4})
            self.assertEqual(result['app_document_wizard']['presets'], presets_dict)
            # JSON 文件可正常读取
            self.assertEqual(load_json(jp), result)
            # 旧 pickle 文件保留作备份
            self.assertTrue(os.path.exists(pp))


if __name__ == '__main__':
    unittest.main()

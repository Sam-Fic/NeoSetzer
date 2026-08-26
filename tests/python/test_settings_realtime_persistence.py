#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''运行时设置持久化的去抖行为测试。

注意：不依赖 sys.modules 里当时的 GLib 身份。全套件运行时，字母序靠前的
测试（如 test_matrix_generator）会先加载真实 gi，使 conftest_stub.install()
让位；若直接使用 settings_module.GLib，会拿到真实 GLib——重置过的假
_sources 字典与真实 timeout_add 登记的 id 对不上，产生 KeyError 污染。
因此 setUp 显式构建独立桩实例（make_glib_stub）并 patch 进被测模块，
桩/真机环境下行为完全一致。
'''

import os
import tempfile
import unittest
from unittest.mock import patch

from tests.python import conftest_stub  # noqa: F401  无 GTK 环境时提供 Gtk/Pango 桩

from setzer.helpers.observable import Observable
from setzer.helpers.persistence import load_json
from setzer.settings import settings as settings_module
from setzer.settings.settings import Settings


class TestSettingsRealtimePersistence(unittest.TestCase):

    def setUp(self):
        # 独立 GLib 桩：登记表、自增 id 均为本测试私有，杜绝环境串扰。
        self.glib = conftest_stub.make_glib_stub()
        # 生产代码在调用时解引用模块级名字 GLib.timeout_add / GLib.Source.remove，
        # patch settings_module.GLib 即可让被测路径写入上面的桩。
        patcher = patch.object(settings_module, 'GLib', self.glib)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.tempdir = tempfile.TemporaryDirectory()

        # 通过最小实例避开 Gtk.TextView 默认字体探测；本测试只验证运行时
        # 调度和 JSON 写入，不测试 Settings 的默认值或迁移逻辑。
        self.settings = Settings.__new__(Settings)
        Observable.__init__(self.settings)
        self.settings.pathname = self.tempdir.name
        self.settings._json_path = os.path.join(self.tempdir.name, 'settings.json')
        self.settings.data = {}
        self.settings.defaults = {'preferences': {'tab_width': 4}}
        self.settings._persistence_source_id = None

    def tearDown(self):
        self.tempdir.cleanup()

    def test_multiple_changes_share_one_debounced_save(self):
        self.settings.set_value('preferences', 'tab_width', 2)
        first_source_id = self.settings._persistence_source_id
        self.assertEqual(
            self.glib._sources[first_source_id][0],
            Settings.PERSISTENCE_DELAY_MS,
        )

        self.settings.set_value('preferences', 'tab_width', 8)
        second_source_id = self.settings._persistence_source_id

        self.assertNotEqual(first_source_id, second_source_id)
        self.assertNotIn(first_source_id, self.glib._sources)
        self.assertIn(second_source_id, self.glib._sources)
        self.assertEqual(len(self.glib._sources), 1)

    def test_debounced_callback_persists_latest_value(self):
        self.settings.set_value('preferences', 'tab_width', 6)
        source_id = self.settings._persistence_source_id
        _, callback, callback_args = self.glib._sources[source_id]

        self.assertFalse(callback(*callback_args))
        self.assertIsNone(self.settings._persistence_source_id)
        self.assertEqual(
            load_json(self.settings._json_path),
            {'preferences': {'tab_width': 6}},
        )

    def test_flush_persistence_cancels_timer_and_writes_immediately(self):
        self.settings.set_value('preferences', 'tab_width', 3)
        source_id = self.settings._persistence_source_id

        self.assertTrue(self.settings.flush_persistence())
        self.assertNotIn(source_id, self.glib._sources)
        self.assertIsNone(self.settings._persistence_source_id)
        self.assertEqual(
            load_json(self.settings._json_path),
            {'preferences': {'tab_width': 3}},
        )

    def test_reset_preferences_schedules_persistence(self):
        self.settings.reset_preferences()

        self.assertEqual(self.settings.data['preferences'], {'tab_width': 4})
        self.assertIsNotNone(self.settings._persistence_source_id)


if __name__ == '__main__':
    unittest.main()

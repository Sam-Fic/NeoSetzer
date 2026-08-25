#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''运行时设置持久化的去抖行为测试。'''

import os
import tempfile
import unittest

from tests.python import conftest_stub  # noqa: F401  必须先注入伪 gi

from setzer.helpers.observable import Observable
from setzer.helpers.persistence import load_json
from setzer.settings import settings as settings_module
from setzer.settings.settings import Settings


class TestSettingsRealtimePersistence(unittest.TestCase):

    def setUp(self):
        self.glib = settings_module.GLib
        self.glib._next_source_id = 0
        self.glib._sources = {}
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

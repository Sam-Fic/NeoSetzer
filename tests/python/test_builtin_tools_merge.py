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

'''内置 Agent 工具预设的升级补全测试。

pi 等内置预设是版本演进中加入 BUILTIN_TOOLS 的：ai_fix_tools 一旦持久化，
旧 settings.json 升级后原样读回，新工具永远不会出现（内置预设又不可经
UI 删除，用户只能整体重置）。本测试覆盖 presets.merge_missing_builtins
与 settings._merge_missing_builtin_tools 的补全行为：缺失内置补到尾部、
同名保留用户已存版本、自定义工具与顺序不动、无缺失不回写。
'''

import os
import tempfile
import unittest
from unittest.mock import Mock

from tests.python import conftest_stub  # noqa: F401  必须先注入伪 gi

from setzer.ai_fix.presets import (
    BUILTIN_TOOLS,
    builtin_names,
    default_tools,
    merge_missing_builtins,
)
from setzer.settings.settings import Settings


class TestPiBuiltinPreset(unittest.TestCase):
    '''pi 的内置预设与 CLI 实测形式一致（pi 1.1.0，2026-11 本机核实）。'''

    def test_pi_is_builtin(self):
        self.assertIn('pi', builtin_names())

    def test_pi_template_uses_positional_message_after_double_dash(self):
        tool = next(t for t in BUILTIN_TOOLS if t['name'] == 'pi')
        self.assertEqual(tool['executable'], 'pi')
        self.assertEqual(tool['headed_template'], ['pi', '--', '{prompt}'])
        self.assertTrue(tool['builtin'])


class TestCodebuddyBuiltinPreset(unittest.TestCase):
    '''codebuddy 的内置预设与 CLI 实测形式一致（CodeBuddy Code 2.184.0，
    2026-11 本机核实：位置参数=初始 prompt，与 claude 同款）。'''

    def test_codebuddy_is_builtin(self):
        self.assertIn('codebuddy', builtin_names())

    def test_codebuddy_template_uses_positional_prompt(self):
        tool = next(t for t in BUILTIN_TOOLS if t['name'] == 'codebuddy')
        self.assertEqual(tool['executable'], 'codebuddy')
        self.assertEqual(tool['headed_template'], ['codebuddy', '{prompt}'])
        self.assertTrue(tool['builtin'])


class TestMergeMissingBuiltins(unittest.TestCase):

    def test_old_list_without_pi_gets_pi_appended_last(self):
        # pi 之后加入内置清单的 codebuddy 一并被补全（模拟 pi 前版本的存量）
        tools = [dict(t) for t in default_tools()
                 if t['name'] not in ('pi', 'codebuddy')]
        merged = merge_missing_builtins(tools)
        names = [t['name'] for t in merged]
        self.assertEqual(names, ['opencode', 'claude', 'codex', 'gemini',
                                 'aider', 'pi', 'codebuddy'])
        # 追加的是内置全量定义，不是残缺占位
        self.assertEqual(merged[-1], next(t for t in BUILTIN_TOOLS if t['name'] == 'codebuddy'))
        self.assertEqual(merged[-2], next(t for t in BUILTIN_TOOLS if t['name'] == 'pi'))

    def test_user_edited_builtin_template_is_preserved(self):
        tools = default_tools()
        tools[0]['headed_template'] = ['opencode', '--prompt', 'custom']
        merged = merge_missing_builtins(tools)
        self.assertIs(merged, tools)
        self.assertEqual(merged[0]['headed_template'], ['opencode', '--prompt', 'custom'])

    def test_custom_tool_kept_in_place_before_appended_builtin(self):
        tools = [{'name': 'mytool', 'executable': 'mytool', 'headed_template': ['mytool', '{prompt}']}]
        merged = merge_missing_builtins(tools)
        names = [t['name'] for t in merged]
        self.assertEqual(names[0], 'mytool')
        self.assertEqual(names[1:], builtin_names())

    def test_full_list_returns_same_object_without_copy(self):
        tools = default_tools()
        self.assertIs(merge_missing_builtins(tools), tools)

    def test_missing_multiple_builtins_appended_in_builtin_order(self):
        tools = [{'name': 'claude', 'executable': 'claude', 'builtin': True}]
        merged = merge_missing_builtins(tools)
        self.assertEqual([t['name'] for t in merged],
                         ['claude'] + [n for n in builtin_names() if n != 'claude'])

    def test_non_list_falls_back_to_defaults(self):
        for broken in (None, 'opencode', {'name': 'opencode'}):
            self.assertEqual(merge_missing_builtins(broken), default_tools())

    def test_empty_list_returned_as_is(self):
        # 空列表的自愈归 runtime 回退（resolve_tool_config）与偏好页
        # _get_tools，本函数不越权处理。
        self.assertEqual(merge_missing_builtins([]), [])


class TestSettingsMergeMissingBuiltinTools(unittest.TestCase):

    def _settings(self, preferences):
        settings = Settings.__new__(Settings)
        settings.data = {'preferences': preferences}
        settings.pickle = Mock()
        return settings

    def test_persisted_list_without_pi_is_merged_and_saved(self):
        tools = [dict(t) for t in default_tools()
                 if t['name'] not in ('pi', 'codebuddy')]
        settings = self._settings({'ai_fix_tools': tools})
        settings._merge_missing_builtin_tools()
        names = [t['name'] for t in settings.data['preferences']['ai_fix_tools']]
        self.assertIn('pi', names)
        self.assertIn('codebuddy', names)
        self.assertEqual(names[-2:], ['pi', 'codebuddy'])
        settings.pickle.assert_called_once()

    def test_up_to_date_list_not_rewritten(self):
        settings = self._settings({'ai_fix_tools': default_tools()})
        settings._merge_missing_builtin_tools()
        settings.pickle.assert_not_called()

    def test_unpersisted_key_left_to_defaults(self):
        # 键未持久化过：get_value 直接回退 defaults（含全量内置），不落盘。
        settings = self._settings({'tab_width': 4})
        settings._merge_missing_builtin_tools()
        settings.pickle.assert_not_called()
        self.assertNotIn('ai_fix_tools', settings.data['preferences'])

    def test_end_to_end_unpickle_then_merge(self):
        # 端到端：旧 settings.json（无 pi）→ unpickle 读回 → 补全后 get_value 可见
        tools = [dict(t) for t in default_tools() if t['name'] != 'pi']
        with tempfile.TemporaryDirectory() as d:
            settings = Settings.__new__(Settings)
            settings.pathname = d
            settings._json_path = os.path.join(d, 'settings.json')
            settings._pickle_path = os.path.join(d, 'settings.pickle')
            settings.data = {'preferences': {'ai_fix_tools': tools}}
            settings.defaults = {'preferences': {}}
            settings.pickle()
            settings.data = {}
            self.assertTrue(settings.unpickle())
            settings._merge_missing_builtin_tools()
            names = [t['name'] for t in settings.get_value('preferences', 'ai_fix_tools')]
            self.assertIn('pi', names)


if __name__ == '__main__':
    unittest.main()

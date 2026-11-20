#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''「Enable AI agent」关闭时各入口消失的回归测试。

契约：总开关关闭后，AI 入口不是置灰/弹提示，而是直接从界面消失——
标题栏 Agent 按钮隐藏、侧栏 AI 面板档位摘除并在必要时回落、Build Log 的
AI 修复按钮隐藏、右键菜单的批注 section 为空。

方法体经 AST 提取 exec（与 test_ai_from_pdf_wiring 同模式），避免构造
完整 GTK 视图；其余为真实模块导入。
'''

import ast
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import builtins
builtins._ = lambda s: s

from setzer.ai_fix.availability import is_ai_agent_enabled
from setzer.workspace.headerbar.headerbar import HeaderBar

HEADERBAR_SRC = os.path.join(REPO, 'setzer', 'workspace', 'headerbar', 'headerbar.py')
SIDEBAR_SRC = os.path.join(REPO, 'setzer', 'workspace', 'sidebar', 'sidebar.py')
WORKSPACE_SRC = os.path.join(REPO, 'setzer', 'workspace', 'workspace.py')
BUILD_LOG_SRC = os.path.join(REPO, 'setzer', 'dialogs', 'build_log',
                            'build_log_dialog_controller.py')
CONTEXT_MENU_SRC = os.path.join(REPO, 'setzer', 'popovers', 'context_menu',
                               'context_menu_viewgtk.py')
GIT_SECTION_SRC = os.path.join(REPO, 'setzer', 'workspace', 'sidebar', 'git',
                              'git_section.py')


class FakeSettings:
    def __init__(self, enabled):
        self.enabled = enabled

    def get_value(self, section, key):
        if key == 'ai_fix_enabled':
            return self.enabled
        return None


def _load_method(src, class_name, name, extra_ns=None):
    with open(src, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {'_': lambda s: s}
    ns.update(extra_ns or {})
    exec(compile(ast.Module(body=[fn], type_ignores=[]), src, 'exec'), ns)
    return ns[name]


class AvailabilityTest(unittest.TestCase):

    def test_follows_switch(self):
        self.assertTrue(is_ai_agent_enabled(FakeSettings(True)))
        self.assertFalse(is_ai_agent_enabled(FakeSettings(False)))

    def test_unavailable_settings_defaults_to_enabled(self):
        # 取不到全局设置时按启用处理：宁可多显示入口，也不能把功能误判为关闭。
        with mock.patch('setzer.app.service_locator.ServiceLocator.get_settings',
                        side_effect=RuntimeError('not initialized')):
            self.assertTrue(is_ai_agent_enabled())


class HeaderBarButtonTest(unittest.TestCase):

    def _bar(self, enabled):
        bar = HeaderBar.__new__(HeaderBar)
        bar.workspace = SimpleNamespace(
            settings=FakeSettings(enabled),
            get_root_or_active_latex_document=lambda: object())
        bar.view = SimpleNamespace(agent_terminal_button=mock.Mock())
        return bar

    def test_hidden_when_disabled(self):
        bar = self._bar(False)
        HeaderBar.update_agent_terminal_button_visibility(bar)
        bar.view.agent_terminal_button.set_visible.assert_called_once_with(False)

    def test_shown_when_enabled(self):
        bar = self._bar(True)
        HeaderBar.update_agent_terminal_button_visibility(bar)
        bar.view.agent_terminal_button.set_visible.assert_called_once_with(True)


class SidebarPanelTest(unittest.TestCase):

    def test_toggle_removed_and_page_falls_back(self):
        update = _load_method(SIDEBAR_SRC, 'Sidebar', 'update_ai_panel_availability')
        workspace = mock.Mock()
        workspace.settings = FakeSettings(False)
        workspace.show_ai_agent = True
        sidebar = SimpleNamespace(workspace=workspace,
                                  view=mock.Mock())
        update(sidebar)
        sidebar.view.set_switch_option_visible.assert_called_once_with('ai_agent', False)
        workspace.set_show_symbols_or_document_structure.assert_called_once_with(False, True)

    def test_no_fallback_when_ai_page_not_active(self):
        update = _load_method(SIDEBAR_SRC, 'Sidebar', 'update_ai_panel_availability')
        workspace = mock.Mock()
        workspace.settings = FakeSettings(False)
        workspace.show_ai_agent = False
        sidebar = SimpleNamespace(workspace=workspace,
                                  view=mock.Mock())
        update(sidebar)
        sidebar.view.set_switch_option_visible.assert_called_once_with('ai_agent', False)
        workspace.set_show_symbols_or_document_structure.assert_not_called()


class WorkspaceGuardTest(unittest.TestCase):

    def test_set_show_ai_agent_is_refused_when_disabled(self):
        set_show = _load_method(WORKSPACE_SRC, 'Workspace', 'set_show_ai_agent',
                                {'is_ai_agent_enabled': is_ai_agent_enabled})
        workspace = mock.Mock()
        workspace.settings = FakeSettings(False)
        workspace.show_ai_agent = False
        set_show(workspace, True)
        self.assertFalse(workspace.show_ai_agent)
        workspace.add_change_code.assert_not_called()


class BuildLogAiButtonTest(unittest.TestCase):

    def test_ai_buttons_hidden_when_disabled(self):
        update = _load_method(BUILD_LOG_SRC, 'BuildLogDialogController',
                              'update_ai_availability',
                              {'is_ai_agent_enabled': is_ai_agent_enabled})
        controller = SimpleNamespace(
            build_log=SimpleNamespace(settings=FakeSettings(False)),
            view=SimpleNamespace(ai_fix_all_button=mock.Mock(),
                                 lists={'Error': SimpleNamespace(ai_fix_enabled=True),
                                        'Warning': SimpleNamespace(ai_fix_enabled=True)}),
            presenter=None)
        update(controller)
        controller.view.ai_fix_all_button.set_visible.assert_called_once_with(False)
        for lst in controller.view.lists.values():
            self.assertFalse(lst.ai_fix_enabled)

    def test_ai_buttons_shown_when_enabled(self):
        update = _load_method(BUILD_LOG_SRC, 'BuildLogDialogController',
                              'update_ai_availability',
                              {'is_ai_agent_enabled': is_ai_agent_enabled})
        controller = SimpleNamespace(
            build_log=SimpleNamespace(settings=FakeSettings(True)),
            view=SimpleNamespace(ai_fix_all_button=mock.Mock(),
                                 lists={'Error': SimpleNamespace(ai_fix_enabled=False)}),
            presenter=None)
        update(controller)
        controller.view.ai_fix_all_button.set_visible.assert_called_once_with(True)
        self.assertTrue(controller.view.lists['Error'].ai_fix_enabled)


class GitAiCommitButtonTest(unittest.TestCase):

    def _update(self, enabled):
        update = _load_method(GIT_SECTION_SRC, 'GitSection',
                              'update_ai_availability',
                              {'is_ai_agent_enabled': is_ai_agent_enabled})
        controller = SimpleNamespace(
            settings=FakeSettings(enabled),
            view=SimpleNamespace(button_ai_commit=mock.Mock()),
            _update_commit_sensitivity=mock.Mock())
        update(controller)
        return controller

    def test_hidden_when_disabled(self):
        controller = self._update(False)
        controller.view.button_ai_commit.set_visible.assert_called_once_with(False)
        controller._update_commit_sensitivity.assert_not_called()

    def test_shown_when_enabled(self):
        controller = self._update(True)
        controller.view.button_ai_commit.set_visible.assert_called_once_with(True)
        controller._update_commit_sensitivity.assert_called_once()


class FakeMenu:
    def __init__(self):
        self.items = []

    def remove_all(self):
        self.items = []

    def append_item(self, item):
        self.items.append(item)


class ContextMenuAiSectionTest(unittest.TestCase):

    def _section(self, enabled, has_selection):
        rebuild = _load_method(
            CONTEXT_MENU_SRC, 'ContextMenuView', 'rebuild_ai_section',
            {'is_ai_agent_enabled': lambda: enabled,
             '_action_item': lambda label, action: (label, action)})
        view = SimpleNamespace(ai_section=FakeMenu())
        rebuild(view, has_selection)
        return view.ai_section

    def test_empty_when_disabled(self):
        self.assertEqual(self._section(False, True).items, [])

    def test_two_items_when_enabled_with_selection(self):
        self.assertEqual(
            [action for _, action in self._section(True, True).items],
            ['win.ai-add-annotation', 'win.ai-send-selection'])

    def test_empty_without_selection(self):
        self.assertEqual(self._section(True, False).items, [])


if __name__ == '__main__':
    unittest.main()

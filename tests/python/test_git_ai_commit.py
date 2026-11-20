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

'''Git AI 提交提示词组装测试（纯函数，不构造 GTK）。'''

import ast
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from setzer.ai_fix.presets import build_git_commit_prompt

GIT_SECTION_SRC = os.path.join(
    os.path.dirname(__file__), '..', '..', 'setzer', 'workspace', 'sidebar',
    'git', 'git_section.py')


def _load_method(name, extra_ns):
    with open(GIT_SECTION_SRC, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == 'GitSection')
    fn = next(n for n in cls.body
              if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {'_': lambda s: s}
    ns.update(extra_ns)
    exec(compile(ast.Module(body=[fn], type_ignores=[]),
                 GIT_SECTION_SRC, 'exec'), ns)
    return ns[name]


class GitCommitPromptTest(unittest.TestCase):

    def test_lists_only_selected_files(self):
        prompt = build_git_commit_prompt(
            ['src/a.py', 'docs/b.md'], cwd='/repo')
        self.assertIn('/repo', prompt)
        self.assertIn('- src/a.py', prompt)
        self.assertIn('- docs/b.md', prompt)
        self.assertIn('Commit the changes in the following files', prompt)
        self.assertIn('split them into multiple commits', prompt)
        self.assertIn('Do not push', prompt)

    def test_message_hint_appended(self):
        prompt = build_git_commit_prompt(
            ['a.tex'], message_hint='Fix typo in intro', cwd='/repo')
        self.assertIn('Fix typo in intro', prompt)
        self.assertIn('drafted this commit message', prompt)

    def test_blank_hint_omitted(self):
        prompt = build_git_commit_prompt(['a.tex'], message_hint='   ', cwd='/repo')
        self.assertNotIn('drafted this commit message', prompt)

    def test_empty_file_list_still_yields_prompt(self):
        prompt = build_git_commit_prompt([], cwd='/repo')
        self.assertIn('allowed', prompt)
        self.assertNotIn('\n- ', prompt)


class ExecuteAiCommitTest(unittest.TestCase):

    def _run(self):
        session = mock.Mock()
        session.send.return_value = (True, 'sent')
        manager = mock.Mock()
        manager.new_session.return_value = session
        workspace = mock.Mock()
        workspace.get_agent_sessions.return_value = manager
        settings = mock.Mock()
        settings.get_value.side_effect = (
            lambda section, key: True if key == 'ai_fix_embedded_terminal'
            else None)
        show_toast = mock.Mock()
        fn = _load_method('_execute_ai_commit', {'show_toast': show_toast})
        controller = SimpleNamespace(settings=settings, workspace=workspace,
                                     _toast=mock.Mock())
        fn(controller, 'PROMPT', {'name': 'pi'}, '/repo')
        return session, manager, workspace, show_toast

    def test_new_session_receives_prompt(self):
        session, manager, workspace, show_toast = self._run()
        manager.new_session.assert_called_once()
        session.send.assert_called_once_with('PROMPT', '/repo')

    def test_toast_only_no_jump(self):
        session, manager, workspace, show_toast = self._run()
        show_toast.assert_called_once()
        kwargs = show_toast.call_args.kwargs
        self.assertEqual(kwargs['button_label'], 'Show')
        self.assertIs(kwargs['on_button_clicked'],
                      workspace.pop_out_agent_terminal)
        workspace.set_show_ai_agent.assert_not_called()
        workspace.reveal_agent_terminal.assert_not_called()
        workspace.pop_out_agent_terminal.assert_not_called()

    def test_failed_send_removes_session(self):
        session = mock.Mock()
        session.send.return_value = (False, 'nope')
        manager = mock.Mock()
        manager.new_session.return_value = session
        workspace = mock.Mock()
        workspace.get_agent_sessions.return_value = manager
        settings = mock.Mock()
        settings.get_value.side_effect = (
            lambda section, key: True if key == 'ai_fix_embedded_terminal'
            else None)
        fn = _load_method('_execute_ai_commit', {'show_toast': mock.Mock()})
        controller = SimpleNamespace(settings=settings, workspace=workspace,
                                     _toast=mock.Mock())
        fn(controller, 'PROMPT', {'name': 'pi'}, '/repo')
        manager.remove.assert_called_once_with(session)
        controller._toast.assert_called_once_with('nope')


if __name__ == '__main__':
    unittest.main()

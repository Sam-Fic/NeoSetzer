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

'''agent_runner 有头启动的单元测试（mock 掉真实终端检测与 Popen）。

覆盖：
- run_headed_bare：裸 executable 组装、workdir flag、flatpak 包裹、
  工具不可用失败、无可用终端失败。
- run_headed：{prompt} 模板渲染路径重构后行为不变（通过共用的
  _launch_in_terminal 组装）。
'''

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from setzer.ai_fix import agent_runner


GNOME_TERMINAL_INFO = ('/usr/bin/gnome-terminal', {'sep': '--', 'workdir_arg': '--working-directory'})


def _popen_calls():
    '''返回 Popen mock 记录到的 (final_cmd, kwargs) 列表。'''
    return [(call.args[0], call.kwargs) for call in agent_runner.subprocess.Popen.call_args_list]


class RunHeadedBareTest(unittest.TestCase):

    def setUp(self):
        self.popen_patch = mock.patch.object(agent_runner.subprocess, 'Popen')
        self.popen_mock = self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)

    def test_bare_launch_no_prompt(self):
        '''裸启动：命令 = 终端 + workdir + '--' + executable（无参数）。'''
        tool = {'name': 'opencode', 'executable': 'opencode'}
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=GNOME_TERMINAL_INFO), \
             mock.patch.object(agent_runner, 'is_flatpak', return_value=False), \
             mock.patch.object(agent_runner, '_which_on_host', return_value='/usr/bin/opencode'):
            success, msg = agent_runner.run_headed_bare(tool, '/tmp/proj')

        assert success is True
        assert 'opencode' in msg
        calls = _popen_calls()
        assert len(calls) == 1
        final_cmd, kwargs = calls[0]
        assert final_cmd == ['/usr/bin/gnome-terminal', '--working-directory=/tmp/proj', '--', 'opencode']
        assert kwargs['cwd'] == '/tmp/proj'
        assert kwargs['start_new_session'] is True

    def test_unavailable_tool(self):
        '''executable 不在 host PATH：返回失败提示，不启动终端。'''
        tool = {'name': 'myagent', 'executable': 'myagent'}
        with mock.patch.object(agent_runner, '_which_on_host', return_value=None):
            success, msg = agent_runner.run_headed_bare(tool, '/tmp/proj')

        assert success is False
        assert 'myagent' in msg
        assert _popen_calls() == []

    def test_no_terminal_available(self):
        '''系统无可用终端：返回失败提示。'''
        tool = {'name': 'claude', 'executable': 'claude'}
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=None), \
             mock.patch.object(agent_runner, '_which_on_host', return_value='/usr/bin/claude'):
            success, msg = agent_runner.run_headed_bare(tool, '/tmp/proj')

        assert success is False
        assert 'terminal' in msg.lower()
        assert _popen_calls() == []

    def test_flatpak_wrapping(self):
        '''Flatpak 下命令以 flatpak-spawn --host 包裹。'''
        tool = {'name': 'claude', 'executable': 'claude'}
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=GNOME_TERMINAL_INFO), \
             mock.patch.object(agent_runner, 'is_flatpak', return_value=True), \
             mock.patch.object(agent_runner, '_which_on_host', return_value='/usr/bin/claude'), \
             mock.patch.object(agent_runner.shutil, 'which', return_value='/usr/bin/flatpak-spawn'):
            success, msg = agent_runner.run_headed_bare(tool, '/tmp/proj')

        assert success is True
        final_cmd, _ = _popen_calls()[0]
        assert final_cmd[:2] == ['/usr/bin/flatpak-spawn', '--host']
        assert final_cmd[-1] == 'claude'

    def test_tuple_workdir_arg(self):
        '''kitty/konsole 风格 workdir：'--flag' '<cwd>' 空格格式。'''
        tool = {'name': 'claude', 'executable': 'claude'}
        konsole_info = ('/usr/bin/konsole', {'sep': '-e', 'workdir_arg': ('--workdir',)})
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=konsole_info), \
             mock.patch.object(agent_runner, 'is_flatpak', return_value=False), \
             mock.patch.object(agent_runner, '_which_on_host', return_value='/usr/bin/claude'):
            success, msg = agent_runner.run_headed_bare(tool, '/tmp/proj')

        assert success is True
        final_cmd, _ = _popen_calls()[0]
        assert final_cmd == ['/usr/bin/konsole', '--workdir', '/tmp/proj', '-e', 'claude']


class RunHeadedTest(unittest.TestCase):
    '''run_headed 重构（提取 _launch_in_terminal 共用）后原有行为不变。'''

    def setUp(self):
        self.popen_patch = mock.patch.object(agent_runner.subprocess, 'Popen')
        self.popen_mock = self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)

    def test_prompt_template_rendered(self):
        '''{prompt} 模板渲染路径：prompt / file / cwd 占位符均替换。'''
        tool = {
            'name': 'claude',
            'executable': 'claude',
            'headed_template': ['claude', '{prompt}'],
        }
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=GNOME_TERMINAL_INFO), \
             mock.patch.object(agent_runner, 'is_flatpak', return_value=False):
            success, msg = agent_runner.run_headed(tool, 'fix line 3', '/tmp/proj', '/tmp/proj/main.tex')

        assert success is True
        final_cmd, kwargs = _popen_calls()[0]
        assert final_cmd == ['/usr/bin/gnome-terminal', '--working-directory=/tmp/proj',
                             '--', 'claude', 'fix line 3']
        assert kwargs['cwd'] == '/tmp/proj'

    def test_clipboard_fallback_message(self):
        '''模板不含 {prompt}：走剪贴板兜底，成功消息含粘贴提示。'''
        tool = {
            'name': 'custom',
            'executable': 'custom',
            'headed_template': ['custom', '--flag', '{file}'],
        }
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=GNOME_TERMINAL_INFO), \
             mock.patch.object(agent_runner, 'is_flatpak', return_value=False):
            success, msg = agent_runner.run_headed(tool, 'the prompt', '/tmp/proj', '/tmp/proj/main.tex')

        assert success is True
        assert 'clipboard' in msg.lower()
        final_cmd, _ = _popen_calls()[0]
        assert final_cmd[-3:] == ['custom', '--flag', '/tmp/proj/main.tex']

    def test_failure_propagates(self):
        '''终端启动失败：run_headed 原样透传失败消息。'''
        tool = {
            'name': 'claude',
            'executable': 'claude',
            'headed_template': ['claude', '{prompt}'],
        }
        with mock.patch.object(agent_runner, 'detect_terminal', return_value=None):
            success, msg = agent_runner.run_headed(tool, 'p', '/tmp/proj', None)

        assert success is False
        assert 'terminal' in msg.lower()


if __name__ == '__main__':
    unittest.main()

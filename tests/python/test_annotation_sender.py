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

'''AnnotationSender 发送后批注移除测试。

契约：发送成功（内嵌会话 / 外部终端）→ 已发出的批注从模型移除；
发送失败 / 预览取消 → 保留供重试。mark 失效的批注不进入发送集合，
也不得被移除。
'''

import builtins
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

builtins._ = lambda s: s

from setzer.ai_fix import annotation_sender as sender_module
from setzer.ai_fix.annotation_sender import AnnotationSender


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get_value(self, section, key):
        return self.values.get(key)

    def set_value(self, section, key, value):
        self.values[key] = value

    def pickle(self):
        pass


class FakeModel:
    def __init__(self, annotations, invalid_ids=()):
        self.annotations = annotations
        self.invalid_ids = set(invalid_ids)
        self.removed = []

    def get_line_range(self, annotation):
        if annotation['id'] in self.invalid_ids:
            return None
        return (1, 2)

    def get_text(self, annotation):
        return 'snippet'

    def remove(self, annotation_id):
        self.removed.append(annotation_id)


class FakeDocument:
    def __init__(self, model):
        self.ai_annotations = model
        self.saved = 0

    def get_filename(self):
        return '/tmp/doc.tex'

    def get_dirname(self):
        return '/tmp'

    def save_to_disk(self):
        self.saved += 1
        return True


def make_sender(embedded=True, send_success=True):
    session = mock.Mock()
    session.send.return_value = (send_success, 'msg')
    workspace = mock.Mock()
    workspace.settings = FakeSettings({
        'ai_fix_enabled': True,
        'ai_fix_embedded_terminal': embedded,
        'ai_fix_trusted_dirs': ['/tmp'],
        'ai_annotation_presets': [],
        'ai_fix_active_tool': 'opencode',
        'ai_fix_tools': [{'name': 'opencode', 'executable': 'opencode'}],
    })
    workspace.get_agent_session.return_value = session
    sender = AnnotationSender(workspace)
    sender._toast = mock.Mock()
    return sender, session


class RemoveSentTest(unittest.TestCase):

    def test_remove_sent_removes_each_entry(self):
        sender, _session = make_sender()
        model = FakeModel([])
        document = FakeDocument(model)
        sender._remove_sent([(document, {'id': 'a'}), (document, {'id': 'b'})])
        self.assertEqual(model.removed, ['a', 'b'])

    def test_remove_sent_none_is_noop(self):
        sender, _session = make_sender()
        sender._remove_sent(None)  # 选区直发路径：无批注可移除


class ExecuteRemovalTest(unittest.TestCase):

    def test_embedded_success_removes_annotations(self):
        sender, session = make_sender(embedded=True, send_success=True)
        model = FakeModel([])
        document = FakeDocument(model)
        with mock.patch.object(sender_module.agent_session_module, 'VTE_AVAILABLE', True), \
                mock.patch.object(sender, '_tool_config', return_value={'name': 'opencode'}):
            sender._execute('prompt', '/tmp', document, [(document, {'id': 'a'})])
        session.send.assert_called_once()
        self.assertEqual(model.removed, ['a'])

    def test_embedded_failure_keeps_annotations(self):
        sender, _session = make_sender(embedded=True, send_success=False)
        model = FakeModel([])
        document = FakeDocument(model)
        with mock.patch.object(sender_module.agent_session_module, 'VTE_AVAILABLE', True), \
                mock.patch.object(sender, '_tool_config', return_value={'name': 'opencode'}):
            sender._execute('prompt', '/tmp', document, [(document, {'id': 'a'})])
        self.assertEqual(model.removed, [])

    def test_external_success_removes_annotations(self):
        sender, _session = make_sender(embedded=False)
        model = FakeModel([])
        document = FakeDocument(model)
        with mock.patch.object(sender_module.agent_session_module, 'VTE_AVAILABLE', False), \
                mock.patch.object(sender, '_tool_config', return_value={'name': 'opencode'}), \
                mock.patch.object(sender_module.agent_runner, 'run_headed',
                                  return_value=(True, 'ok')):
            sender._execute('prompt', '/tmp', document, [(document, {'id': 'a'})])
        self.assertEqual(model.removed, ['a'])

    def test_external_failure_keeps_annotations(self):
        sender, _session = make_sender(embedded=False)
        model = FakeModel([])
        document = FakeDocument(model)
        with mock.patch.object(sender_module.agent_session_module, 'VTE_AVAILABLE', False), \
                mock.patch.object(sender, '_tool_config', return_value={'name': 'opencode'}), \
                mock.patch.object(sender_module.agent_runner, 'run_headed',
                                  return_value=(False, 'fail')):
            sender._execute('prompt', '/tmp', document, [(document, {'id': 'a'})])
        self.assertEqual(model.removed, [])


class CopyPromptTest(unittest.TestCase):

    def test_copy_prompt_copies_without_sending_or_removing(self):
        '''复制 prompt：上剪贴板，但不发送、不保存文档、不移除批注。'''
        model = FakeModel([{'id': 'a', 'comment': '', 'preset': None}])
        document = FakeDocument(model)
        sender, session = make_sender()
        clipboard = mock.Mock()
        display = mock.Mock()
        display.get_clipboard.return_value = clipboard
        with mock.patch.object(sender_module, 'Gdk') as gdk, \
                mock.patch.object(sender_module, 'resolve_interpreter', return_value='pdflatex'):
            gdk.Display.get_default.return_value = display
            sender.copy_prompt([(document, {'id': 'a', 'comment': '', 'preset': None})])
        clipboard.set.assert_called_once()
        prompt = clipboard.set.call_args[0][0]
        self.assertIn('snippet', prompt)
        self.assertEqual(model.removed, [])
        self.assertEqual(document.saved, 0)
        session.send.assert_not_called()

    def test_copy_prompt_invalid_mark_noop(self):
        model = FakeModel([{'id': 'dead'}], invalid_ids=('dead',))
        document = FakeDocument(model)
        sender, _session = make_sender()
        sender.copy_prompt([(document, {'id': 'dead'})])
        self.assertEqual(model.removed, [])


class SendAnnotationsPlumbingTest(unittest.TestCase):

    def test_sent_entries_exclude_invalid_marks(self):
        '''mark 失效的批注不发送也不移除；有效批注随路传到 _send_data。'''
        model = FakeModel([{'id': 'ok'}, {'id': 'dead'}], invalid_ids=('dead',))
        document = FakeDocument(model)
        sender, _session = make_sender()
        with mock.patch.object(sender, '_send_data') as send_data:
            sender.send_annotations([(document, {'id': 'ok'}), (document, {'id': 'dead'})])
        send_data.assert_called_once()
        _data, _doc, sent_entries = send_data.call_args[0]
        self.assertEqual([ann['id'] for _d, ann in sent_entries], ['ok'])


if __name__ == '__main__':
    unittest.main()
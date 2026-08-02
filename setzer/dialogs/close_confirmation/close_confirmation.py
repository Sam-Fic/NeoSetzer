#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

import os.path


class CloseConfirmationDialog(object):

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.parameters = None

    def run(self, parameters, callback):
        if parameters['unsaved_document'] == None: return

        self.parameters = parameters
        self.callback = callback

        documents = parameters.get('documents', [])
        # 多文档批量路径：≥2 个未保存文档时弹批量对话框，避免逐个确认。
        # 单文档路径保留原对话框（含其响应映射 'discard'/'cancel'/'save'）。
        if len(documents) > 1:
            self.setup_batch(documents)
        else:
            self.setup(self.parameters['unsaved_document'])
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, document):
        self.view = Adw.AlertDialog(
            heading=_('Document "{document}" has unsaved changes.').format(document=document.get_displayname()),
            body=_('If you close without saving, these changes will be lost.'))
        # 按钮顺序遵循 GNOME HIG：Cancel（取消）最左、Discard（破坏性）居中、
        # Save（建议操作）最右。Adw.AlertDialog 按添加顺序从左到右排列。
        # 响应映射 (dialog_process_response) 按字符串 response_id 查表，与顺序无关，
        # 调换 add_response 顺序不影响下游 {discard:0, cancel:1, save:2} 的数字契约。
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('discard', _('Discard'))
        self.view.add_response('save', _('Save'))
        self.view.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        self.view.set_response_appearance('save', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('cancel')
        self.view.set_close_response('cancel')

    def setup_batch(self, documents):
        # body 用纯文本换行列出文档名（Adw.AlertDialog body 不渲染多行 markup 列表，
        # 但纯文本换行会正常显示）。文档名前缀 "•" 便于扫读。
        names = '\n'.join('• ' + d.get_displayname() for d in documents)
        self.view = Adw.AlertDialog(
            heading=_('You have unsaved changes in {n} documents.').format(n=len(documents)),
            body=_('The following documents have unsaved changes:\n\n{names}\n\n'
                   'If you close without saving, these changes will be lost.').format(names=names))
        # 与 setup 一致的 HIG 顺序：Cancel / Discard All / Save All。
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('discard_all', _('Discard All'))
        self.view.add_response('save_all', _('Save All'))
        self.view.set_response_appearance('discard_all', Adw.ResponseAppearance.DESTRUCTIVE)
        self.view.set_response_appearance('save_all', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('cancel')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        # 响应映射（保持与调用方 setzer_dev.py 的数字契约兼容）：
        # 单文档: discard -> 0, cancel -> 1, save -> 2
        # 批量:   discard_all -> 4, save_all -> 3
        # discard_all 用 4 而非 0，避免与单文档 discard 语义混淆
        # （单 discard 删一个继续递归，批量 discard 直接退出）。
        mapping = {
            'discard': 0, 'cancel': 1, 'save': 2,
            'discard_all': 4, 'save_all': 3,
        }
        self.parameters['response'] = mapping.get(response_id, 1)
        self.callback(self.parameters)

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

import os

from gi.repository import GLib

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.helpers.file_io import read_text_with_encoding


class AutosaveRecoveryDialog(object):
    '''启动时若检测到 ~/.config/setzer/autosave/ 下有残留临时文件，
    弹此对话框让用户选择恢复或丢弃。

    恢复策略：对每个 manifest 条目创建一个新 untitled 文档，把临时文件
    内容填入缓冲区并 set_modified(True)（用户随后可手动 Save As 覆盖原文件
    或另存）。原文件名仅用于显示在对话框列表中，不直接覆盖磁盘文件——
    避免在用户不知情下用旧自动保存内容覆盖可能已更新的源文件。'''

    def __init__(self, main_window, workspace):
        self.main_window = main_window
        self.workspace = workspace
        self.manifest = None
        self.autosave = None

    def run(self, manifest, autosave):
        if not manifest:
            return
        self.manifest = manifest
        self.autosave = autosave
        self.setup()
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self):
        # 构造文档列表（按时间戳倒序，最近编辑的在前）
        entries = sorted(
            self.manifest.items(),
            key=lambda kv: kv[1].get('timestamp', 0),
            reverse=True,
        )
        names = []
        for temp_path, info in entries:
            displayname = info.get('displayname') or os.path.basename(temp_path)
            ts = info.get('timestamp', 0)
            if ts:
                dt = GLib.DateTime.new_from_unix_local(ts)
                time_str = dt.format('%x %H:%M') if dt is not None else ''
            else:
                time_str = ''
            names.append('• {}  <span alpha="50%">{}</span>'.format(
                GLib.markup_escape_text(displayname), time_str))

        n = len(entries)
        self.view = Adw.AlertDialog(
            heading=_('Crash recovery'),
            body=_('Setzer did not exit cleanly last time. {n} unsaved document(s) '
                   'were recovered:').format(n=n))
        # 用 extra_child 显示文档列表（body 不支持多行 markup 列表）
        list_label = Gtk.Label(label='\n'.join(names))
        list_label.set_use_markup(True)
        list_label.set_halign(Gtk.Align.START)
        list_label.set_xalign(0.0)
        list_label.set_margin_top(6)
        list_label.set_margin_bottom(6)
        list_label.set_wrap(True)
        self.view.set_extra_child(list_label)

        self.view.add_response('discard_all', _('Discard All'))
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('restore_all', _('Restore All'))
        self.view.set_response_appearance('discard_all', Adw.ResponseAppearance.DESTRUCTIVE)
        self.view.set_response_appearance('restore_all', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('restore_all')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'restore_all':
            self.restore_all()
        elif response_id == 'discard_all':
            try:
                self.autosave.cleanup_all()
            except Exception as e:
                print(f'Warning: autosave.cleanup_all() failed during recovery discard: {e}')
        # 'cancel' 不处理，临时文件保留供下次启动再问

    def restore_all(self):
        '''把每个临时文件内容作为新 untitled 文档打开，然后清理临时文件。'''
        for temp_path, info in list(self.manifest.items()):
            try:
                text, encoding, has_bom = read_text_with_encoding(temp_path)
            except OSError:
                continue
            language = info.get('language', 'latex')
            # 创建新 untitled 文档并填入恢复内容
            if language == 'bibtex':
                document = self.workspace.create_bibtex_document()
            else:
                document = self.workspace.create_latex_document()
            self.workspace.add_document(document)
            # 设置从临时文件检测到的编码和 BOM 状态
            document.file_encoding = encoding
            document.has_bom = has_bom
            # 用临时文件内容替换缓冲区（irreversible_action 避免污染 undo 栈）
            document.source_buffer.begin_irreversible_action()
            document.source_buffer.set_text(text)
            document.source_buffer.end_irreversible_action()
            # 标记为已修改，使 close_confirmation 能捕获（用户需 Save As 才能落地）
            document.source_buffer.set_modified(True)
            # 尝试恢复原显示名（add_document 会赋 "Untitled Document N"，
            # 若原是 untitled 文档则覆盖回原名以保留可识别性）
            original_displayname = info.get('displayname')
            if original_displayname:
                document.set_displayname(original_displayname)
            # 删除已恢复的临时文件，避免下次启动重复恢复
            try:
                os.remove(temp_path)
            except OSError:
                pass
        # 清空 manifest（所有条目已恢复或跳过）
        try:
            os.remove(self.autosave.manifest_path)
        except OSError:
            pass
        # 切换到最后一个恢复的文档，让用户立刻看到内容
        if self.workspace.open_documents:
            self.workspace.set_active_document(self.workspace.open_documents[-1])

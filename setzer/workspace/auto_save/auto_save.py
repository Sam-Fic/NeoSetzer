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

'''自动保存（崩溃恢复模式）。

仿 auto_build.AutoBuild 模式：每个文档一个去抖定时器，监听
document.source_buffer 的 'changed' 信号（Document 已通过 on_change 转发为
自身 'changed' Observable 信号）。当用户停止编辑 delay 秒后，把缓冲区内容
写入 ~/.config/setzer/autosave/<hash>.tex，并维护 manifest.json 记录原始
文件名/显示名/时间戳。

清理时机：
  - 文档手动 save_to_disk 成功（监听 Document 的 'saved' 信号）→ 删除其临时文件
  - 文档关闭（document_removed）→ 删除其临时文件
  - 应用正常退出（save_state_and_quit）→ 清空整个 autosave 目录

崩溃恢复：正常退出会清理；若进程被 kill/崩溃，临时文件残留，下次启动
setzer_dev.py.activate 会通过 check_autosave_recovery 弹恢复对话框。
'''

import os
import os.path
import hashlib
import json
import time

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GObject, GLib

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.persistence import atomic_write_bytes, save_json


class AutoSave(object):

    AUTOSAVE_SUBDIR = 'autosave'

    def __init__(self, workspace):
        self.workspace = workspace
        self.settings = ServiceLocator.get_settings()
        # document -> GLib timeout source id
        self.timers = dict()
        self.pathname = os.path.join(ServiceLocator.get_config_folder(), self.AUTOSAVE_SUBDIR)
        os.makedirs(self.pathname, exist_ok=True)
        self.manifest_path = os.path.join(self.pathname, 'manifest.json')

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.settings.connect('settings_changed', self.on_settings_changed)

        # attach to documents that were opened before this controller existed
        for document in list(self.workspace.open_documents):
            self.on_new_document(self.workspace, document)

    # ---- 信号回调 ----
    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'auto_save_enabled' and not value:
            for document in list(self.timers.keys()):
                self.cancel_timer(document)

    def on_new_document(self, workspace, document):
        document.connect('changed', self.on_document_changed)
        document.connect('saved', self.on_document_saved)

    def on_document_removed(self, workspace, document):
        self.cancel_timer(document)
        # 文档关闭后其临时文件已无意义（用户主动放弃）→ 清理
        self.remove_temp_file(document)
        try:
            document.disconnect('changed', self.on_document_changed)
        except Exception:
            pass
        try:
            document.disconnect('saved', self.on_document_saved)
        except Exception:
            pass

    def on_document_changed(self, document):
        if not self.settings.get_value('preferences', 'auto_save_enabled'):
            return
        delay = self.settings.get_value('preferences', 'auto_save_delay')
        delay_ms = max(int(delay), 1) * 1000
        self.schedule_save(document, delay_ms)

    def on_document_saved(self, document):
        # 用户手动保存成功，临时文件已过时，删除以避免下次启动误恢复
        self.cancel_timer(document)
        self.remove_temp_file(document)

    # ---- 定时器 ----
    def schedule_save(self, document, delay_ms):
        self.cancel_timer(document)
        source_id = GObject.timeout_add(delay_ms, self.on_timer, document)
        self.timers[document] = source_id

    def cancel_timer(self, document):
        source_id = self.timers.pop(document, None)
        if source_id is not None:
            GLib.Source.remove(source_id)

    def on_timer(self, document):
        # one-shot timeout, drop the stored id right away
        self.timers.pop(document, None)

        if not self.settings.get_value('preferences', 'auto_save_enabled'):
            return False
        if document not in self.workspace.open_documents:
            return False
        # 仅当文档有未保存修改时才写临时文件（已保存且未改的无需备份）
        if not document.source_buffer.get_modified():
            return False
        self.write_temp_file(document)
        return False

    # ---- 临时文件管理 ----
    def get_temp_filename(self, document):
        filename = document.get_filename()
        displayname = document.get_displayname()
        key = filename if filename else ('untitled:' + displayname)
        # sha1 取前 16 位（16^16 ≈ 1.8e19，足够避免文档间碰撞；文件名安全无特殊字符）
        hash_str = hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]
        return os.path.join(self.pathname, hash_str + '.tex')

    def write_temp_file(self, document):
        text = document.get_all_text()
        if text is None:
            return
        # 将 GtkTextBuffer 中的 LF 转换回原始换行符格式
        line_ending = getattr(document, 'line_ending', '\n')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)
        temp_path = self.get_temp_filename(document)
        try:
            # 使用文档的原编码保存（保留 BOM 状态，fallback 到 utf-8）
            encoding = getattr(document, 'file_encoding', 'utf-8')
            has_bom = getattr(document, 'has_bom', False)
            try:
                encoded = text.encode(encoding)
                if has_bom:
                    encoded = self._prepend_bom_for_autosave(encoded, encoding)
            except (UnicodeEncodeError, LookupError):
                encoded = text.encode('utf-8', errors='replace')
            atomic_write_bytes(temp_path, encoded)
        except OSError:
            return
        self.update_manifest(document, temp_path)

    def remove_temp_file(self, document):
        temp_path = self.get_temp_filename(document)
        try:
            os.remove(temp_path)
        except OSError:
            pass
        self.remove_from_manifest(document)

    # ---- manifest ----
    def load_manifest(self):
        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_manifest(self, manifest):
        try:
            save_json(self.manifest_path, manifest, indent=2)
        except OSError:
            pass

    def update_manifest(self, document, temp_path):
        manifest = self.load_manifest()
        manifest[temp_path] = {
            'original_filename': document.get_filename(),
            'displayname': document.get_displayname(),
            'language': document.get_document_type(),
            'timestamp': time.time(),
        }
        self.save_manifest(manifest)

    def remove_from_manifest(self, document):
        temp_path = self.get_temp_filename(document)
        manifest = self.load_manifest()
        if temp_path in manifest:
            del manifest[temp_path]
            self.save_manifest(manifest)

    # ---- 退出清理 ----
    def cleanup_all(self):
        '''应用正常退出时调用，清空整个 autosave 目录。

        正常退出意味着用户已确认所有未保存文档的处理（保存/丢弃），
        残留的临时文件无恢复价值，全部删除以避免下次启动误触发恢复对话框。'''
        manifest = self.load_manifest()
        for temp_path in list(manifest.keys()):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        try:
            os.remove(self.manifest_path)
        except OSError:
            pass

@staticmethod
def _prepend_bom_for_autosave(encoded_bytes, encoding):
    '''为 autosave 文件添加 BOM（如果原文件有 BOM）。'''
    enc = encoding.lower().replace('-', '_')
    if enc in ('utf_8', 'utf8'):
        return b'\xef\xbb\xbf' + encoded_bytes
    if enc in ('utf_16_le', 'utf16_le', 'utf_16le'):
        return b'\xff\xfe' + encoded_bytes
    if enc in ('utf_16_be', 'utf16_be', 'utf_16be'):
        return b'\xfe\xff' + encoded_bytes
    return encoded_bytes

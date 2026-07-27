#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GObject, GLib, Adw

from setzer.app.service_locator import ServiceLocator


class AutoBuild(object):
    ''' Watches LaTeX documents for changes and triggers a save +
        build after a configurable delay once the user stops typing.

        A single instance lives at the workspace level and keeps a
        debounced timer per document. When the timer fires, the
        changed document is saved (if dirty) and the root or active
        LaTeX document is rebuilt. If a build is already running, the
        attempt is retried shortly afterwards so the latest edits are
        not lost. '''

    RETRY_INTERVAL_MS = 1000
    MAX_RETRIES = 10

    def __init__(self, workspace):
        self.workspace = workspace
        self.settings = ServiceLocator.get_settings()
        # maps document -> GLib timeout source id
        self.timers = dict()
        # maps document -> consecutive retry count (reset on new edit or successful build)
        self._retry_counts = dict()
        # 已对“未保存文档无法自动构建”提示过的文档集合，避免每次按键重复弹 toast。
        self._untitled_warned = set()

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.settings.connect('settings_changed', self.on_settings_changed)

        # attach to documents that were opened before this controller existed
        for document in list(self.workspace.open_documents):
            self.on_new_document(self.workspace, document)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'auto_build' and not value:
            for document in list(self.timers.keys()):
                self.cancel_timer(document)

    def on_new_document(self, workspace, document):
        if document.is_latex_document():
            document.connect('changed', self.on_document_changed)
            document.connect('filename_change', self.on_document_saved)

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            self.cancel_timer(document)
            self._retry_counts.pop(document, None)
            self._untitled_warned.discard(document)
            try:
                document.disconnect('changed', self.on_document_changed)
                document.disconnect('filename_change', self.on_document_saved)
            except Exception:
                pass

    def on_document_changed(self, document):
        if not self.settings.get_value('preferences', 'auto_build'):
            return
        if document.get_filename() == None:
            self._warn_untitled(document)
            return
        self._retry_counts.pop(document, None)
        delay = self.settings.get_value('preferences', 'auto_build_delay')
        delay_ms = max(int(delay), 1) * 1000
        self.schedule_build(document, delay_ms)

    def on_document_saved(self, document, filename=None):
        # 文档已保存后解除一次性提示屏蔽；若再次变为未命名（如“另存为”到
        # 内存），下次编辑会重新提示。
        self._untitled_warned.discard(document)

    def _warn_untitled(self, document):
        if document in self._untitled_warned:
            return
        self._untitled_warned.add(document)
        self._show_toast(_('Auto-build is not available for unsaved documents. Save the document to enable it.'), timeout=3)

    def _show_toast(self, text, timeout=3):
        main_window = ServiceLocator.get_main_window()
        if hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(text)
            toast.set_timeout(timeout)
            main_window.toast_overlay.add_toast(toast)

    def schedule_build(self, document, delay_ms):
        self.cancel_timer(document)
        source_id = GObject.timeout_add(delay_ms, self.on_timer, document)
        self.timers[document] = source_id

    def cancel_timer(self, document):
        source_id = self.timers.pop(document, None)
        if source_id != None:
            GLib.Source.remove(source_id)

    def on_timer(self, document):
        # this is a one-shot timeout, drop the stored id right away
        self.timers.pop(document, None)

        if not self.settings.get_value('preferences', 'auto_build'):
            return False
        if document not in self.workspace.open_documents:
            return False

        target = self.workspace.get_root_or_active_latex_document()
        if target == None:
            return False

        # if a build is currently running, retry shortly so the latest
        # edits get built once it finishes instead of being dropped.
        if target.build_system.get_build_state() in ('building_in_progress', 'building_to_stop'):
            count = self._retry_counts.get(document, 0) + 1
            # 首次因文档占用而排队时给出一次性提示，让用户知道编辑不会被丢弃
            # （见项 3：并发构建重试需有 UI 反馈，而非静默重试）。
            if count == 1:
                self._show_toast(_('Auto-build deferred — a build is already in progress.'), timeout=2)
            if count > self.MAX_RETRIES:
                self._retry_counts.pop(document, None)
                self._show_toast(_('Auto-build skipped — the document was busy building.'), timeout=3)
                return False
            self._retry_counts[document] = count
            self.schedule_build(document, self.RETRY_INTERVAL_MS)
            return False

        self._retry_counts.pop(document, None)

        # save the edited document if it has unsaved changes, then build.
        if document.source_buffer.get_modified():
            document.save_to_disk()

        active_document = self.workspace.get_active_document()
        if active_document == None:
            active_document = document
        # 标记本次构建为自动构建触发。build_log.update_items 据此结合
        # auto_build_autoshow_errors 设置决定是否弹出日志弹窗。
        target.build_system.is_auto_build = True
        target.build_system.build_and_forward_sync(active_document)
        return False

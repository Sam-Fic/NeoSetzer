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
from gi.repository import GObject, GLib

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

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            self.cancel_timer(document)
            self._retry_counts.pop(document, None)
            try:
                document.disconnect('changed', self.on_document_changed)
            except Exception:
                pass

    def on_document_changed(self, document):
        if not self.settings.get_value('preferences', 'auto_build'):
            return
        if document.get_filename() == None:
            return
        self._retry_counts.pop(document, None)
        delay = self.settings.get_value('preferences', 'auto_build_delay')
        delay_ms = max(int(delay), 1) * 1000
        self.schedule_build(document, delay_ms)

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
            if count > self.MAX_RETRIES:
                self._retry_counts.pop(document, None)
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

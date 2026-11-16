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

"""侧栏 AI 页控制器：批注列表 + Agent 终端。

批注跨所有打开文档收集、按添加顺序平铺；行内按钮 = 跳转源码 / 编辑 /
单发 / 删除。终端区由 AgentSession + AgentTerminalView 提供，弹出独立
窗口经 workspace.pop_out_agent_terminal / pop_in_agent_terminal。
"""

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Pango

from setzer.app.service_locator import ServiceLocator
from setzer.ai_fix.agent_terminal_viewgtk import AgentTerminalView
from setzer.document.ai_annotations.annotation_popover import AnnotationPopover
from setzer.workspace.sidebar.ai_page.ai_page_viewgtk import AiPageView


class AiPage(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.session = workspace.get_agent_session()
        self.sender = workspace.get_annotation_sender()
        self.terminal_view = AgentTerminalView(self.session)
        self.view = AiPageView(self.terminal_view)
        # 无障碍名；同时为 sidebar_viewgtk.PANEL_SWITCH_OPTIONS 的 _('AI Agent')
        # 提供字面量 msgid（xgettext 据此提取，切换器 tooltip 运行时查表共用）。
        self.view.update_property([Gtk.AccessibleProperty.LABEL], [_('AI Agent')])
        self.popover = AnnotationPopover()

        # 每文档信号连接记录：{document: (model, buffer_handler_id)}。
        # Observable.connect 不返回 id，断开时按回调本身 disconnect。
        self._annotation_handlers = dict()
        # 编辑去抖：buffer changed 后 400ms 合并刷新行号显示。
        self._refresh_idle_id = None

        self.workspace.connect('new_document', self.on_document_list_changed)
        self.workspace.connect('document_removed', self.on_document_list_changed)

        self.view.send_all_button.connect('clicked', self.on_send_all_clicked)
        self.view.preset_dropdown.connect('notify::selected', self.on_preset_selected)
        self.terminal_view.copy_button.connect('clicked', self.on_copy_clicked)
        self.terminal_view.new_session_button.connect('clicked', self.on_new_session_clicked)
        self.terminal_view.pop_button.connect('clicked', self.on_pop_clicked)

        self.rebuild_preset_dropdown()
        self.rebuild_list()

    # ---- 批注收集 ----

    def _all_entries(self):
        '''[(document, annotation), ...]，跨打开文档、按文档内添加顺序。'''
        entries = []
        for document in self.workspace.open_documents:
            model = getattr(document, 'ai_annotations', None)
            if model is None:
                continue
            for annotation in model.get_all():
                entries.append((document, annotation))
        return entries

    def on_document_list_changed(self, workspace, document):
        self._sync_annotation_signals()
        self.rebuild_list()

    def _sync_annotation_signals(self):
        '''为新文档补连 annotations_changed / buffer changed，移除已关文档。'''
        open_docs = set(self.workspace.open_documents)
        for document in list(self._annotation_handlers):
            if document not in open_docs:
                model, buf_handler = self._annotation_handlers.pop(document)
                model.disconnect('annotations_changed', self.on_annotations_changed)
                try:
                    document.source_buffer.disconnect(buf_handler)
                except (TypeError, KeyError):
                    pass
        for document in self.workspace.open_documents:
            if document in self._annotation_handlers:
                continue
            model = getattr(document, 'ai_annotations', None)
            if model is None:
                continue
            model.connect('annotations_changed', self.on_annotations_changed)
            buf_handler = document.source_buffer.connect('changed', self.on_buffer_changed)
            self._annotation_handlers[document] = (model, buf_handler)

    def on_annotations_changed(self, model):
        self.rebuild_list()

    def on_buffer_changed(self, buffer):
        '''编辑后行号会漂移：去抖刷新列表显示（发送时另行现算）。'''
        if self._refresh_idle_id is None:
            self._refresh_idle_id = GLib.timeout_add(400, self._deferred_rebuild)

    def _deferred_rebuild(self):
        self._refresh_idle_id = None
        self.rebuild_list()
        return False

    # ---- 列表 ----

    def rebuild_list(self):
        self._sync_annotation_signals()
        list_box = self.view.list_box
        while True:
            row = list_box.get_first_child()
            if row is None:
                break
            list_box.remove(row)

        entries = self._all_entries()
        for document, annotation in entries:
            list_box.append(self._build_row(document, annotation))
        self.view.set_has_annotations(bool(entries))

    def _build_row(self, document, annotation):
        row = Gtk.ListBoxRow()
        row.document = document
        row.annotation_id = annotation['id']
        row.set_activatable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        # 水平内边距对齐 Adw.ActionRow（document structure 行）的 12px。
        box.set_margin_start(12)
        box.set_margin_end(12)

        # 行 1：文件名 + 行区间 + 操作按钮
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = document.get_displayname() or os.path.basename(document.get_filename() or '')
        file_label = Gtk.Label(label=name)
        file_label.add_css_class('caption')
        file_label.set_xalign(0.0)
        file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        header.append(file_label)

        model = document.ai_annotations
        line_range = model.get_line_range(annotation)
        range_text = '{}–{}'.format(*line_range) if line_range else '?'
        lines_label = Gtk.Label(label=_('lines {range}').format(range=range_text))
        lines_label.add_css_class('caption')
        lines_label.add_css_class('dim-label')
        lines_label.set_hexpand(True)
        lines_label.set_xalign(0.0)
        header.append(lines_label)

        for icon, tooltip, callback in (
                ('go-jump-symbolic', _('Jump to source'), self.on_jump_clicked),
                ('document-edit-symbolic', _('Edit annotation'), self.on_edit_clicked),
                ('mail-send-symbolic', _('Send this annotation'), self.on_send_one_clicked),
                ('user-trash-symbolic', _('Delete annotation'), self.on_delete_clicked)):
            button = Gtk.Button(icon_name=icon)
            button.add_css_class('flat')
            button.set_tooltip_text(tooltip)
            button.connect('clicked', callback, row)
            header.append(button)
        box.append(header)

        # 行 2：选区摘录（单行省略）
        snippet = model.get_text(annotation).replace('\n', ' ⏎ ')
        snippet_label = Gtk.Label(label=snippet)
        snippet_label.add_css_class('dim-label')
        snippet_label.add_css_class('caption')
        snippet_label.set_xalign(0.0)
        snippet_label.set_ellipsize(Pango.EllipsizeMode.END)
        snippet_label.set_max_width_chars(36)
        box.append(snippet_label)

        # 行 3：预制提示词 + 用户批注
        preset = annotation.get('preset')
        comment = (annotation.get('comment') or '').strip()
        request_text = ' · '.join(part for part in (preset, comment) if part)
        if request_text:
            request_label = Gtk.Label(label=request_text)
            request_label.set_xalign(0.0)
            request_label.set_wrap(True)
            request_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            request_label.set_max_width_chars(36)
            box.append(request_label)

        row.set_child(box)
        return row

    # ---- 行操作 ----

    def _row_entry(self, row):
        document = row.document
        model = getattr(document, 'ai_annotations', None)
        if model is None:
            return None
        annotation = model.get_by_id(row.annotation_id)
        if annotation is None:
            return None
        return (document, annotation)

    def on_jump_clicked(self, button, row):
        entry = self._row_entry(row)
        if entry is None:
            return
        document, annotation = entry
        iters = document.ai_annotations.get_iters(annotation)
        if iters is None:
            return
        self.workspace.set_active_document(document)

        def scroll():
            document.source_buffer.place_cursor(iters[0])
            view = document.view.source_view
            view.scroll_to_iter(iters[0], 0.1, False, 0.0, 0.0)
            view.grab_focus()
            return False
        GLib.idle_add(scroll)

    def on_edit_clicked(self, button, row):
        entry = self._row_entry(row)
        if entry is None:
            return
        document, annotation = entry
        self.popover.set_presets(self._presets())
        self.popover.present_for(
            button, None,
            comment=annotation.get('comment', ''),
            preset=annotation.get('preset'),
            on_save_cb=lambda comment, preset: document.ai_annotations.update(
                annotation['id'], comment=comment, preset=preset))

    def on_send_one_clicked(self, button, row):
        entry = self._row_entry(row)
        if entry is None:
            return
        self.sender.send_annotations([entry])

    def on_delete_clicked(self, button, row):
        entry = self._row_entry(row)
        if entry is None:
            return
        document, annotation = entry
        document.ai_annotations.remove(annotation['id'])

    # ---- 工具栏 ----

    def _presets(self):
        return self.workspace.settings.get_value('preferences', 'ai_annotation_presets') or []

    def rebuild_preset_dropdown(self):
        labels = [_('Custom (no preset)')] + [p.get('name', '') for p in self._presets()]
        self.view.preset_dropdown.set_model(Gtk.StringList.new(labels))
        self.view.preset_dropdown.set_selected(0)

    def on_preset_selected(self, dropdown, pspec):
        selected = dropdown.get_selected()
        presets = self._presets()
        # 索引 0 = 自定义（None）；其余对应 presets[selected-1]。
        if selected <= 0 or selected > len(presets):
            self.sender.default_preset = None
        else:
            self.sender.default_preset = presets[selected - 1].get('name')

    def on_send_all_clicked(self, button):
        self.sender.send_annotations(self._all_entries())

    def on_copy_clicked(self, button):
        if self.session.copy_last_prompt():
            self._toast(_('Copied to clipboard'))
        else:
            self._toast(_('Nothing sent yet'))

    def on_new_session_clicked(self, button):
        document = self.workspace.get_active_document()
        cwd = document.get_dirname() if document is not None else None
        if not cwd:
            self._toast(_('Please save the document first'))
            return
        self.session.restart(cwd)
        self._toast(_('Started a new agent session'))

    def on_pop_clicked(self, button):
        if self.terminal_view._popped_out:
            self.workspace.pop_in_agent_terminal()
        else:
            self.workspace.pop_out_agent_terminal()

    def _toast(self, message):
        try:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'toast_overlay'):
                main_window.toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception:
            pass
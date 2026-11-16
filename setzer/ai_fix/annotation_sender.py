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

"""批注 / 选区直发的统一发送控制器（AI 页与 win action 共用）。

流程与 build_log AI fix 一致：
  1. 全局开关检查；
  2. 涉及文档 save_to_disk（缓冲区=磁盘，行号才精确）；
  3. 发送时刻从批注 marks 现算行区间，组装 prompt（精度契约）；
  4. 信任目录门：未信任 → PreviewDialog 预览确认；已信任 → 直发；
  5. 执行：VTE 可用且偏好开启 → AgentSession.send（同一会话接续）；
     否则回退 agent_runner.run_headed 外部终端。剪贴板兜底恒开。
"""

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk

from setzer.app.service_locator import ServiceLocator
from setzer.ai_fix import agent_runner
from setzer.ai_fix import agent_session as agent_session_module
from setzer.ai_fix import annotation_prompt_builder
from setzer.ai_fix.prompt_builder import resolve_interpreter


class AnnotationSender(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.settings = workspace.settings
        self._preview_dialog = None
        # 直发选区时的默认预制提示词（AI 页工具栏下拉同步）。
        self.default_preset = None

    # ---- 公开入口 ----

    def send_annotations(self, entries):
        '''批量发送批注。entries: [(document, annotation_dict), ...]。'''
        if not entries:
            self._toast(_('No annotations to send'))
            return
        if not self._check_enabled():
            return
        # 先保存所有涉及文档（去重），再提取行号——保证摘录与磁盘一致。
        documents = []
        for document, _ann in entries:
            if document not in documents:
                documents.append(document)
        for document in documents:
            if not self._save_document(document):
                return
        data = []
        for document, annotation in entries:
            model = getattr(document, 'ai_annotations', None)
            if model is None:
                continue
            line_range = model.get_line_range(annotation)
            if line_range is None:
                continue  # mark 已失效（如外部重载），跳过
            data.append({
                'filename': document.get_filename(),
                'start_line': line_range[0],
                'end_line': line_range[1],
                'text': model.get_text(annotation),
                'comment': annotation.get('comment', ''),
                'preset': annotation.get('preset'),
            })
        if not data:
            self._toast(_('No annotations to send'))
            return
        self._send_data(data, documents[0])

    def send_selection(self, document, comment='', preset=None):
        '''单条直发：当前选区作为临时批注发送（不落批注模型）。'''
        if document is None:
            self._toast(_('No active document'))
            return
        if not self._check_enabled():
            return
        bounds = document.source_buffer.get_selection_bounds()
        if not bounds:
            self._toast(_('Select some text first'))
            return
        if not self._save_document(document):
            return
        if preset is None:
            preset = self.default_preset
        start, end = bounds
        start_line = start.get_line()
        end_line = end.get_line()
        if end.get_line_offset() == 0 and end_line > start_line:
            end_line -= 1
        data = [{
            'filename': document.get_filename(),
            'start_line': start_line + 1,
            'end_line': end_line + 1,
            'text': document.source_buffer.get_text(start, end, False),
            'comment': comment,
            'preset': preset,
        }]
        self._send_data(data, document)

    # ---- 内部 ----

    def _check_enabled(self):
        if not self.settings.get_value('preferences', 'ai_fix_enabled'):
            self._toast(_('AI agent is disabled. Enable it in Preferences → General → AI Settings.'))
            return False
        return True

    def _save_document(self, document):
        if document.get_filename() is None:
            self._toast(_('Please save the document first'))
            return False
        return bool(document.save_to_disk())

    def _send_data(self, data, document):
        cwd = document.get_dirname()
        if not cwd:
            self._toast(_('Please save the document first'))
            return
        presets = self.settings.get_value('preferences', 'ai_annotation_presets')
        prompt = annotation_prompt_builder.build_prompt_for_annotations(
            data, cwd, interpreter=resolve_interpreter(document), presets=presets)

        # 信任门（与 build_log_dialog_controller 一致：realpath 规范化）。
        trusted_dirs = self.settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
        cwd_real = os.path.realpath(cwd)
        trusted_real = {os.path.realpath(d) for d in trusted_dirs}
        if cwd_real in trusted_real:
            self._execute(prompt, cwd, document)
            return

        agent_runner._ = _
        tool_config = self._tool_config()
        dialog = self._get_preview_dialog()
        dialog.present_for(
            parent=ServiceLocator.get_main_window(),
            prompt=prompt,
            tool_name=(tool_config or {}).get('name', 'agent'),
            cwd=cwd,
            on_send_cb=lambda edited_prompt, dont_ask: self._on_send_confirmed(
                edited_prompt, dont_ask, cwd, document),
        )

    def _on_send_confirmed(self, prompt, dont_ask, cwd, document):
        if dont_ask:
            trusted = self.settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
            cwd_real = os.path.realpath(cwd)
            trusted_real = {os.path.realpath(d) for d in trusted}
            if cwd_real not in trusted_real:
                trusted = list(trusted) + [cwd_real]
                self.settings.set_value('preferences', 'ai_fix_trusted_dirs', trusted)
                self.settings.pickle()
                self._toast(_('Added to trusted directories'))
        self._execute(prompt, cwd, document)

    def _tool_config(self):
        active = self.settings.get_value('preferences', 'ai_fix_active_tool')
        tools = self.settings.get_value('preferences', 'ai_fix_tools')
        return agent_runner.resolve_tool_config(tools, active)

    def _execute(self, prompt, cwd, document):
        tool_config = self._tool_config()
        if tool_config is None:
            self._toast(_('No agent tool configured. Add one in Preferences → General → AI Settings.'))
            return

        embedded = (agent_session_module.VTE_AVAILABLE
                    and self.settings.get_value('preferences', 'ai_fix_embedded_terminal'))
        if embedded:
            agent_session_module._ = _
            session = self.workspace.get_agent_session()
            success, msg = session.send(prompt, cwd)
            if success:
                # 让用户看到终端：切到侧栏 AI 页；已弹出则抬起独立窗口。
                self.workspace.set_show_ai_agent(True)
                window = getattr(self.workspace, 'agent_terminal_window', None)
                if window is not None and window.get_visible():
                    window.present()
            self._toast(msg)
            return

        # 回退：外部终端（每次新窗）+ 剪贴板兜底（恒开，不依赖模板）。
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(prompt)
        except Exception:
            pass
        agent_runner._ = _
        terminal_cmd = self.settings.get_value('preferences', 'ai_fix_terminal_cmd') or None
        success, msg = agent_runner.run_headed(
            tool_config, prompt, cwd, document.get_filename(), terminal_cmd=terminal_cmd)
        self._toast(msg)

    def _get_preview_dialog(self):
        if self._preview_dialog is None:
            from setzer.ai_fix.preview_dialog import PreviewDialog
            self._preview_dialog = PreviewDialog(ServiceLocator.get_main_window())
        return self._preview_dialog

    def _toast(self, message):
        if not message:
            return
        try:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'toast_overlay'):
                main_window.toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception:
            pass
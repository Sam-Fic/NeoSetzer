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

"""AI 修复「发送前预览/确认」单弹窗控制器。

**单弹窗即确认**：用户点「发送」即确认启动 Agent；点「取消」即放弃。
不再有第二个「是否启动」弹窗。设计依据：预览框本来就是给用户最后审阅
prompt 的机会，发送动作本身就是同意执行。

信任目录由调用方（build_log_dialog_controller）判断：
  * 若 cwd ∈ ai_fix_trusted_dirs → 完全跳过本弹窗，直接执行
  * 否则 present 本弹窗
本模块不直接读 settings，由调用方注入 trusted 状态。

调用方式：
    dialog = PreviewDialog(main_window)
    dialog.present_for(parent, prompt, tool_name, cwd,
                       on_send=lambda p, dont_ask: ...)
"""

import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk

from setzer.ai_fix.preview_dialog_viewgtk import PreviewDialogView
from setzer.app.service_locator import ServiceLocator


# 注意：模块顶层不允许调用 _()，因为 gettext.install 尚未执行
# （setzer.in:113 才注入 builtins._）。所有 _() 调用都在 __init__ /
# 方法内运行时求值，与 page_appearance / build_log_dialog_viewgtk 一致。


class PreviewDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = PreviewDialogView(main_window)

        # 当前 present 的回调，关闭时清空避免重复触发
        self._current_callback = None

        # 信号连接：发送、取消、关闭
        self.view.send_button.connect('clicked', self._on_send_clicked)
        self.view.cancel_button.connect('clicked', self._on_cancel_clicked)
        self.view.connect('closed', self._on_closed)

    def present_for(self, parent, prompt, tool_name, cwd, on_send_cb):
        '''打开弹窗，注入 prompt 与元信息；用户点发送时回调 on_send_cb。

        Args:
            parent: 父窗口/widget，用于 Adw.Dialog.present。
            prompt: str，已组装好的 prompt 预填到编辑区。
            tool_name: str，如 'opencode'。
            cwd: str，工作目录（用于副标题展示）。
            on_send_cb: callable (edited_prompt:str, dont_ask:bool) -> None。
                        dont_ask 为 True 时调用方应把 cwd 加入信任列表。
        '''
        self._current_callback = on_send_cb

        title = _('Send to {tool}').format(tool=tool_name)
        subtitle = cwd if cwd else _('(unsaved document)')
        self.view.set_header(title, subtitle)
        self.view.set_prompt(prompt)
        # 默认开关状态：默认关（让用户每次决定是否信任）
        self.view.dont_ask_switch.set_active(False)
        # 显示开关行（信任目录场景由调用方跳过 present，所以这里恒显示）
        self.view.set_dont_ask_visible(True)

        self.view.present(parent)

    def _on_send_clicked(self, button):
        '''用户点发送：读 prompt + 开关状态，回调，关闭弹窗。'''
        cb = self._current_callback
        prompt = self.view.get_prompt()
        dont_ask = self.view.is_dont_ask_checked()
        # 先清回调，避免 closed 信号再触发一次取消路径
        self._current_callback = None
        # 关闭弹窗（close 是异步的，会触发 closed 信号）
        self.view.close()
        if cb is not None:
            try:
                cb(prompt, dont_ask)
            except Exception as e:
                # 不静默吞异常——打印到 stderr 方便排查（如 set_value/pickle 失败）。
                # 不向用户弹错：发送是用户主动操作，异常时 Agent 没启动用户会注意到。
                import sys
                print(f'PreviewDialog send callback error: {e}', file=sys.stderr)

    def _on_cancel_clicked(self, button):
        '''取消：仅关闭，不回调。'''
        self._current_callback = None
        self.view.close()

    def _on_closed(self, dialog):
        '''弹窗被 Esc 或点关闭按钮关闭时的兜底：清回调。'''
        self._current_callback = None

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

"""内嵌 VTE 终端里的 Agent 会话管理器（单 workspace 单会话）。

会话接续的核心：Agent CLI 跑在 Setzer 持有的 Vte.Terminal 里，多次发送
通过 bracketed paste 直接注入运行中的 TUI（同一窗口同一会话）；子进程
退出（用户关掉终端窗口 / Agent 自行退出）后状态归零，下次发送自动开
新会话。

状态机：idle → starting → running → exited（或 end_session 回 idle）。
  - starting：TUI 启动中，prompt 入队，contents-changed 静默 600ms 判
    就绪（8s 上限兜底）后注入。
  - running：bracketed paste（ESC[200~ … ESC[201~）+ 延迟回车提交，
    opencode 等 TUI 将其作为多行粘贴处理，不会逐行误提交。

剪贴板兜底恒开：每次发送同时复制 prompt 到系统剪贴板。

VTE 为可选依赖（Windows / 未装 typelib 时 VTE_AVAILABLE=False），调用方
回退到 agent_runner.run_headed 外部终端路径。Flatpak 下用
flatpak-spawn --host 在 host 上跑 Agent（PTY 由 flatpak-spawn 转发）。
"""

import os
import signal

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Pango

try:
    # Vte-3.91 是 GTK4 绑定；Vte-2.91 是 GTK3 绑定，与已加载的 GTK4
    # 同进程冲突（ImportError: Requiring namespace 'Gtk' version '3.0'），
    # 故优先 3.91，2.91 仅作未加载 GTK4 环境的理论兜底。
    try:
        gi.require_version('Vte', '3.91')
    except ValueError:
        gi.require_version('Vte', '2.91')
    from gi.repository import Vte
    VTE_AVAILABLE = True
except (ValueError, ImportError):
    Vte = None
    VTE_AVAILABLE = False

from setzer.helpers.observable import Observable
from setzer.ai_fix import agent_runner


# TUI 就绪判定：输出静默多久视为启动完成 / 注入上限等待。
_READY_IDLE_MS = 600
_READY_CAP_MS = 8000
# bracketed paste 与回车之间的间隔：给 TUI 处理粘贴的时间。
_PASTE_SUBMIT_DELAY_MS = 80


class AgentSession(Observable):
    ''' owns one Vte.Terminal + one Agent CLI child process. '''

    def __init__(self, workspace):
        Observable.__init__(self)
        self.workspace = workspace
        self.settings = workspace.settings

        self.state = 'idle'
        self.session_cwd = None
        self.last_error = None
        self._child_pid = None
        self._generation = 0
        self._exited_handler = None
        self._pending = []
        self._ready_idle_id = None
        self._ready_cap_id = None
        self._submit_id = None

        self.terminal = None
        if VTE_AVAILABLE:
            self.terminal = Vte.Terminal()
            self.terminal.set_scrollback_lines(10000)
            self.terminal.set_mouse_autohide(True)
            self.terminal.set_encoding('UTF-8')
            font_string = self.settings.get_value('preferences', 'font_string')
            if font_string:
                self.terminal.set_font(Pango.FontDescription.from_string(font_string))
            self.terminal.connect('contents-changed', self._on_contents_changed)

    # ---- 状态 ----

    def _set_state(self, state):
        if self.state != state:
            self.state = state
            self.add_change_code('state_changed', state)

    def is_alive(self):
        return self.state in ('starting', 'running')

    def _tool_config(self):
        agent_runner._ = _
        active = self.settings.get_value('preferences', 'ai_fix_active_tool')
        tools = self.settings.get_value('preferences', 'ai_fix_tools')
        return agent_runner.resolve_tool_config(tools, active)

    # ---- 生命周期 ----

    def ensure_started(self, cwd):
        '''会话已活着则复用；否则在 cwd 开新会话。返回 (success, message)。'''
        if self.is_alive():
            return (True, None)
        return self.spawn(cwd)

    def spawn(self, cwd):
        '''在 cwd 裸启动 Agent CLI（交互 TUI）。返回 (success, message)。'''
        if not VTE_AVAILABLE or self.terminal is None:
            return (False, _('Embedded terminal is not available on this system.'))
        tool_config = self._tool_config()
        if tool_config is None:
            return (False, _('No agent tool configured. Add one in Preferences → General → AI Settings.'))
        executable = tool_config.get('executable')
        resolved = agent_runner._which_on_host(executable) if executable else None
        if resolved is None:
            return (False, _('{tool} is not available on this system. '
                             'Check the executable name in Preferences → General → AI Settings.')
                    .format(tool=tool_config.get('name', 'agent')))
        if not cwd:
            return (False, _('Please save the document first'))

        argv = []
        if agent_runner.is_flatpak():
            import shutil
            flatpak_spawn = shutil.which('flatpak-spawn')
            if flatpak_spawn:
                argv.extend([flatpak_spawn, '--host'])
        # VTE spawn_async 直接 execve，不搜 PATH：必须用 which 解析出的
        # 绝对路径（裸名会报 Failed to execve: No such file or directory）。
        argv.append(resolved)

        # SETSID：子进程独立会话/进程组，end_session 可 killpg 整组；
        # DO_NOT_REAP_CHILD：由 GLib.child_watch_add 收尸，child-exited 照常触发。
        spawn_flags = GLib.SpawnFlags.DO_NOT_REAP_CHILD
        setsid = getattr(GLib.SpawnFlags, 'SETSID', None)
        if setsid is not None:
            spawn_flags |= setsid

        self.session_cwd = cwd
        self.last_error = None
        # 代际隔离：restart 后旧子进程异步到达的 child-exited 不得踩新会话。
        self._generation += 1
        generation = self._generation
        if self._exited_handler is not None:
            self.terminal.disconnect(self._exited_handler)
        self._exited_handler = self.terminal.connect(
            'child-exited',
            lambda t, status: self._on_child_exited(t, status, generation))
        self._set_state('starting')
        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, cwd, argv, None, spawn_flags,
                None, None, -1, None, self._on_spawned)
        except Exception as e:
            self._set_state('idle')
            return (False, _('Failed to launch agent: {}').format(str(e)))
        # 就绪上限兜底：TUI 一直有输出（如动画）也能在 cap 后注入。
        self._ready_cap_id = GLib.timeout_add(_READY_CAP_MS, self._on_ready_cap)
        return (True, None)

    def _on_spawned(self, terminal, pid, error):
        if error is not None or pid is None or pid <= 0:
            self.last_error = str(error) if error is not None else 'spawn failed'
            self._cancel_ready_timers()
            self._pending.clear()
            self._set_state('idle')
            return
        self._child_pid = pid
        GLib.child_watch_add(pid, self._on_child_reaped)

    def _on_child_reaped(self, pid, status):
        # 收尸（DO_NOT_REAP_CHILD）；状态由 child-exited 驱动。
        if self._child_pid == pid:
            self._child_pid = None

    def _on_child_exited(self, terminal, status, generation):
        if generation != self._generation:
            return  # restart 遗留的旧子进程退出事件，忽略
        # 主动结束会话后已是 idle：异步 child-exited 不得把状态拉回
        # exited（否则关闭按钮第一下会闪黑屏、需点第二下）。
        ended = self.state == 'idle'
        self._child_pid = None
        self._cancel_ready_timers()
        self._pending.clear()
        if not ended:
            self._set_state('exited')

    def end_session(self):
        '''主动结束会话（关闭按钮 / 新会话按钮 / 关闭独立窗口）。
        杀子进程 + 清屏与滚回，恢复未打开状态；下次发送开新会话。'''
        self._kill_child()
        self._cancel_ready_timers()
        self._pending.clear()
        if self.terminal is not None:
            self.terminal.reset(True, True)
        self._set_state('idle')

    def restart(self, cwd):
        '''结束当前会话并在 cwd 开新会话。返回 (success, message)。'''
        self.end_session()
        return self.spawn(cwd)

    def _kill_child(self):
        pid = self._child_pid
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

    # ---- 就绪检测 ----

    def _on_contents_changed(self, terminal):
        if self.state != 'starting':
            return
        if self._ready_idle_id is not None:
            GLib.source_remove(self._ready_idle_id)
        self._ready_idle_id = GLib.timeout_add(_READY_IDLE_MS, self._on_ready_idle)

    def _on_ready_idle(self):
        self._ready_idle_id = None
        self._mark_running()
        return False

    def _on_ready_cap(self):
        self._ready_cap_id = None
        if self.state == 'starting':
            self._mark_running()
        return False

    def _cancel_ready_timers(self):
        if self._ready_idle_id is not None:
            GLib.source_remove(self._ready_idle_id)
            self._ready_idle_id = None
        if self._ready_cap_id is not None:
            GLib.source_remove(self._ready_cap_id)
            self._ready_cap_id = None

    def _mark_running(self):
        self._cancel_ready_timers()
        self._set_state('running')
        pending = self._pending
        self._pending = []
        for i, prompt in enumerate(pending):
            if i == 0:
                self._inject(prompt)
            else:
                GLib.timeout_add(300 * i, self._inject_deferred, prompt)

    def _inject_deferred(self, prompt):
        if self.state == 'running':
            self._inject(prompt)
        return False

    # ---- 发送 ----

    def send(self, prompt, cwd):
        '''把 prompt 注入会话（活着则接续，否则开新会话）。

        剪贴板兜底恒开。返回 (success, message) 供调用方 Toast。
        '''
        if not prompt:
            return (False, _('Nothing to send'))
        self._copy_to_clipboard(prompt)

        if not VTE_AVAILABLE or self.terminal is None:
            return (False, _('Embedded terminal is not available on this system.'))

        if self.state == 'running':
            self._inject(prompt)
        elif self.state == 'starting':
            self._pending.append(prompt)
        else:
            success, message = self.spawn(cwd)
            if not success:
                return (False, message)
            self._pending.append(prompt)

        tool_name = (self._tool_config() or {}).get('name', 'agent')
        return (True, _('Sent to {tool} (copied to clipboard as fallback).').format(tool=tool_name))

    def _inject(self, prompt):
        '''bracketed paste 多行注入 + 延迟回车提交。'''
        if self.terminal is None:
            return
        data = b'\x1b[200~' + prompt.encode('utf-8') + b'\x1b[201~'
        self.terminal.feed_child(data)
        if self._submit_id is not None:
            GLib.source_remove(self._submit_id)
        self._submit_id = GLib.timeout_add(_PASTE_SUBMIT_DELAY_MS, self._submit)

    def _submit(self):
        self._submit_id = None
        if self.terminal is not None and self.state == 'running':
            self.terminal.feed_child(b'\r')
        return False

    def _copy_to_clipboard(self, prompt):
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(prompt)
        except Exception:
            pass  # 剪贴板失败不阻止发送

    def shutdown(self):
        self._kill_child()
        self._cancel_ready_timers()
        if self._submit_id is not None:
            GLib.source_remove(self._submit_id)
            self._submit_id = None


def _(s):
    '''占位 i18n 钩子（与 agent_runner 同范式）：controller 侧注入 gettext。'''
    return s
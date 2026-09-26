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

'''chktex 实时检查（每文档一个实例，仅 LaTeX 文档构造）。

调度模式与拼写检查一致：buffer 变更后防抖，全文跑一次外部检查，
把结果画回编辑器；外部依赖缺失时功能静默停用（偏好页置灰提示）。

- 检查在后台线程运行 chktex 子进程，当前 buffer 全文经 stdin 喂入
  （无需先保存文档），输出经 decode_process_output 三级容错解码；
  解析与定位逻辑在 chktex_parser（纯逻辑，可离线测试）；
- generation 计数：结果应用时发现代数不符即作废——防抖窗口内的
  再次输入、shutdown、关闭开关都会使在途结果失效，新一轮检查
  紧随其后；
- 标记：对每条问题在其所在行定位违规文本区间，打 Pango.Underline.ERROR
  波浪线。颜色固定琥珀（与 build_diagnostics 的 WARNING_COLOR 同源，
  不跟随强调色/主题切换），与编译诊断的整行红/琥珀背景相区分：
  整行背景回答「编译结果哪里有问题」，波浪线回答「源码写法哪里可疑」；
- 行级消息 dict（messages）供 gutter 悬停提示复用，gutter 把 chktex
  消息与编译诊断一并显示。
'''

import os
import shutil
import subprocess
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, GLib, Pango

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.decode_output import decode_process_output
from setzer.document.chktex.chktex_parser import (
    build_chktex_args,
    build_line_messages,
    locate_span,
    parse_chktex_output,
)

# 用户停止输入后的防抖间隔（毫秒）。chktex 单文件耗时通常在几十毫秒级，
# 但子进程启动有固定开销，取比拼写检查更长的间隔。
_DEBOUNCE_MS = 800
# 子进程超时（秒）：chktex 卡死时不拖住后台线程堆积。
_PROCESS_TIMEOUT_S = 10

# 波浪线 tag 名（每文档 buffer 的 tag table 中仅一份）。
_TAG_NAME = 'chktex-warning'
# 琥珀色，与 build_diagnostics.WARNING_COLOR 一致（固定值，不随主题）。
_WARNING_RGBA = Gdk.RGBA(0.90, 0.55, 0.10, 1.0)


class ChktexLinter(object):

    def __init__(self, document):
        self.document = document
        self.buffer = document.source_buffer
        self.settings = ServiceLocator.get_settings()

        self.enabled = False
        # 最近一次生效的检查结果（供偏好页外将来扩展使用）。
        self.issues = ()
        # 行号(1-based) -> (消息, ...)，供 gutter 悬停提示使用。
        self.messages = dict()

        self._generation = 0
        self._debounce_id = None
        self._is_shutdown = False

        tag = self.buffer.get_tag_table().lookup(_TAG_NAME)
        if tag is None:
            tag = self.buffer.create_tag(_TAG_NAME)
        tag.set_property('underline', Pango.Underline.ERROR)
        tag.set_property('underline-rgba', _WARNING_RGBA)
        self.warning_tag = tag

        self.buffer.connect('changed', self.on_buffer_changed)
        self.settings.connect('settings_changed', self.on_settings_changed)

        # 应用当前设置；构造时文档通常尚无内容，空跑一次开销可忽略。
        self._apply_settings()

    # ------------------------------------------------------------------
    # 可用性与设置响应
    # ------------------------------------------------------------------

    @staticmethod
    def is_available():
        '''chktex 是否在 PATH 上。每次现查（which 开销可忽略），
        安装 chktex 后无需重启应用即可开启。'''
        return shutil.which('chktex') is not None

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'chktex_enabled':
            self._apply_settings()

    def _apply_settings(self):
        enabled = bool(self.settings.get_value('preferences', 'chktex_enabled')) \
            and self.is_available()
        if enabled == self.enabled:
            return
        self.enabled = enabled
        if enabled:
            self.schedule_recheck()
        else:
            self._cancel_work()
            self._clear_results()

    # ------------------------------------------------------------------
    # 检查调度：防抖 + 后台线程
    # ------------------------------------------------------------------

    def on_buffer_changed(self, buffer):
        if not self.enabled or self._is_shutdown:
            return
        if self._debounce_id is not None:
            GLib.Source.remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(_DEBOUNCE_MS, self._on_debounce_timeout)

    def _on_debounce_timeout(self):
        self._debounce_id = None
        self.schedule_recheck()
        return False

    def schedule_recheck(self):
        '''立即对当前全文调度一次检查。'''
        if self._is_shutdown or not self.enabled:
            return
        self._generation += 1
        text = self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)
        thread = threading.Thread(
            target=self._worker, args=(self._generation, text), daemon=True)
        thread.start()

    def _worker(self, generation, text):
        '''后台线程：运行 chktex 并解析输出，回主线程应用结果。'''
        try:
            kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_PROCESS_TIMEOUT_S)
            if os.name == 'nt':
                # Windows GUI 进程派生子进程默认弹控制台窗口，与构建
                # 系统同策略抑制（CREATE_NO_WINDOW）。
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                build_chktex_args(), input=text.encode('utf-8'), **kwargs)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return
        issues = parse_chktex_output(decode_process_output(result.stdout))
        GLib.idle_add(self._apply_results, generation, issues)

    def _apply_results(self, generation, issues):
        if self._is_shutdown or generation != self._generation:
            return False
        self.issues = issues
        self.messages = build_line_messages(issues)

        buf = self.buffer
        buf.remove_tag(self.warning_tag, buf.get_start_iter(), buf.get_end_iter())
        line_count = buf.get_line_count()
        for issue in issues:
            if issue.line < 1 or issue.line > line_count:
                continue
            line_text = self.document.get_line(issue.line - 1)
            if not line_text:
                continue
            line_start = buf.get_iter_at_line(issue.line - 1)[1].get_offset()
            span_start, span_end = locate_span(line_text, issue.column, issue.data)
            span_end = min(span_end, len(line_text))
            if span_start >= span_end:
                continue
            # TextIter 的 offset 与 locate_span 返回的字符偏移同为字符
            # 计数，可直接相加定位（同拼写检查，已实测确认）。
            buf.apply_tag(
                self.warning_tag,
                buf.get_iter_at_offset(line_start + span_start),
                buf.get_iter_at_offset(line_start + span_end))
        # 通知 gutter 重绘并刷新悬停提示（与编译诊断同一通知路径）。
        self.document.add_change_code('chktex_diagnostics_changed')
        return False

    # ------------------------------------------------------------------
    # 清理与生命周期
    # ------------------------------------------------------------------

    def _cancel_work(self):
        '''取消挂起的防抖回调并使在途结果失效（不清除已有标记）。'''
        self._generation += 1
        if self._debounce_id is not None:
            try:
                GLib.Source.remove(self._debounce_id)
            except (ValueError, RuntimeError):
                pass
            self._debounce_id = None

    def _clear_results(self):
        self.issues = ()
        self.messages = dict()
        buf = self.buffer
        buf.remove_tag(self.warning_tag, buf.get_start_iter(), buf.get_end_iter())
        self.document.add_change_code('chktex_diagnostics_changed')

    def shutdown(self):
        '''文档关闭时清理：取消回调、断开单例信号连接、清除标记。

        清标记不走 _clear_results()（它会发 chktex_diagnostics_changed
        通知）——文档销毁期间不值得让 gutter 为空结果再刷一轮。
        '''
        self._is_shutdown = True
        self._cancel_work()
        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        try:
            buf = self.buffer
            buf.remove_tag(self.warning_tag, buf.get_start_iter(), buf.get_end_iter())
        except Exception:
            pass

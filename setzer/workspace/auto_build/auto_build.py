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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import GObject, GLib, Adw

from setzer.app.service_locator import ServiceLocator
from setzer.settings.document_settings import DocumentSettings


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
    # 忙重试上限：对传统引擎是"放弃"的门槛；PhiTeX live 下只作为退避的分界
    # （超过之后拉长到 RETRY_INTERVAL_MS 继续排队，永不丢弃），见 on_timer。
    MAX_RETRIES = 10
    # 传统引擎的延迟下限：一次构建动辄一两秒，间隔再短只会让构建排队堆积。
    MIN_DELAY_MS = 500
    # PhiTeX 常驻 session 的延迟（毫秒）。实测从"写完文件"到"增量编译完 +
    # PDF 落盘"只要 15–19ms，整个下游约 70–90ms，所以不必沿用 500ms 地板；
    # 但也不取 0——每个按键都写盘会让 Poppler 重载和光栅化持续满载。见
    # _auto_build_values。
    PHITEX_DELAY_MS = 150
    # PhiTeX 下的构建忙重试间隔：见 on_timer 的说明。
    PHITEX_RETRY_INTERVAL_MS = 300

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

    def _auto_build_values(self, document):
        '''返回 (是否自动编译, 延迟毫秒)。

        延迟连同各自的下限在这里算完，调用处不再 max(..., 500)：上一版常数
        (0.5s) 和调用处的 500ms 地板正好相等、叠在一起，把常数改小会被静默
        夹回 500ms——降延迟改了没反应，还不报错。

        PhiTeX live 模式下这两项不是偏好而是机制：watch 只对磁盘上的变化反应，
        而"把缓冲区写到磁盘"正是本模块负责的——让用户关掉自动编译就等于关掉
        实时预览，所以这里覆盖他的设置（偏好页对应行同步置灰，避免出现"能点
        但不生效"）。

        判定走 build_system.is_phitex_live_build（带 1s 缓存），不在每次按键
        重复读全文与项目配置。
        '''
        if document.build_system.is_phitex_live_build():
            return True, self.PHITEX_DELAY_MS
        enabled = DocumentSettings.get_effective_value(document, self.settings, 'auto_build')
        delay = DocumentSettings.get_effective_value(document, self.settings, 'auto_build_delay')
        return enabled, max(int(delay * 1000), self.MIN_DELAY_MS)

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
        # 程序化读盘（会话恢复 / 懒加载 / 文件打开）触发的 buffer 'changed' 不算
        # 用户编辑——磁盘上的 PDF 通常已是最新，启动即重编毫无意义且拖慢启动。
        # 先于引擎判定返回，免得白读一遍全文。详见 Document._loading_from_disk。
        if getattr(document, '_loading_from_disk', False):
            return
        auto_build, delay_ms = self._auto_build_values(document)
        if not auto_build:
            return
        if document.get_filename() == None:
            self._warn_untitled(document)
            return
        self._retry_counts.pop(document, None)
        self.schedule_build(document, delay_ms)

    def schedule_build_for_reload(self, document):
        '''外部文件变更被重新读盘（自动静默重载或“文件已变更”对话框重载）
        后，主动触发一次 auto-build 倒计时，等价于用户真实编辑。

        普通编辑经由 buffer 'changed' 信号进入 on_document_changed；但重载
        读盘时 set_text 会让 _loading_from_disk 置为 True，使 on_document_changed
        提前返回，故重载完成后须由调用方显式唤醒本计时器。这里复用与真实编辑
        一致的设置/延迟判定，若 auto_build 关闭或文档未命名则静默不触发。'''
        auto_build, delay_ms = self._auto_build_values(document)
        if not auto_build:
            return
        if document.get_filename() == None:
            return
        self._retry_counts.pop(document, None)
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

    def cancel_pending_for_target(self, target):
        '''手动构建已把最新编辑构建出来后，取消所有尚未 fire 的
        auto-build 倒计时。

        on_timer 触发时统一构建 get_magic_root_or_active_latex_document()
        ——即与本次手动构建相同的 target——故任何 pending 倒计时再
        fire 都只是对同一 target 做一次冗余构建。手动构建已满足这些
        倒计时的目的，全部取消即可，避免「用户刚手动编译、倒计时到
        了又自动编一次」的极端情况。'''
        for document in list(self.timers.keys()):
            self.cancel_timer(document)

    def on_timer(self, document):
        # this is a one-shot timeout, drop the stored id right away
        self.timers.pop(document, None)

        if not self._auto_build_values(document)[0]:
            return False
        if document not in self.workspace.open_documents:
            return False

        target = self.workspace.get_magic_root_or_active_latex_document()
        if target == None:
            return False

        # 先排掉"僵尸构建态"：build_state 说正在构建、却没有任何 active_query
        # 拥有这次构建，说明 worker 早就没了。不先收回 idle，下面就会把它当
        # "文档忙"而推迟，PhiTeX 下表现为报错后实时编译彻底停摆。
        recover = getattr(target.build_system, 'recover_if_stalled', None)
        if recover is not None:
            recover()

        # if a build is currently running, retry shortly so the latest
        # edits get built once it finishes instead of being dropped.
        if target.build_system.get_build_state() in ('building_in_progress', 'building_to_stop'):
            phitex_live = bool(target.build_system.is_phitex_live_build())
            count = self._retry_counts.get(document, 0) + 1
            # 首次因文档占用而排队时给出一次性提示，让用户知道编辑不会被丢弃
            # （见项 3：并发构建重试需有 UI 反馈，而非静默重试）。count==1 只在
            # 每个"忙"片段开头出现，所以每次占用至多一条 toast。
            # PhiTeX live 下不发这条：自动构建被强制打开、每 150ms 一轮，
            # 用户没有"要不要排队"的选择，提示只是在打字时闪一条噪音。
            if count == 1 and not phitex_live:
                self._show_toast(_('Auto-build deferred — a build is already in progress.'), timeout=2)
            # 只有传统引擎才允许"放弃"：一次构建一两秒，排到第 11 次说明
            # 用户其实已经停手，丢弃这次编辑没有观感损失。PhiTeX 的语义相反
            # ——它就是要不停实时编译，被丢弃的那次正是用户刚敲下的字，所以
            # 下面绝不清空重试计数、绝不弹 "skipped"。
            if not phitex_live and count > self.MAX_RETRIES:
                self._retry_counts.pop(document, None)
                self._show_toast(_('Auto-build skipped — the document was busy building.'), timeout=3)
                return False
            self._retry_counts[document] = count
            # 重试间隔跟着构建节奏走：1s 是为"一次构建一两秒"设计的，PhiTeX
            # 增量只要 ~90ms，撞车基本只发生在冷启动那一次；拿 1s 去等一次
            # 90ms 的腾空，等于把预览白白拖慢一个数量级。取 300ms（约 2 倍
            # 节奏），10 次仍覆盖 ~3s，够一次冷启动。PhiTeX 下超过这个范围
            # （例如一次全量重编）就退避到 1s 继续排队，而不是放弃。
            if phitex_live:
                retry_ms = (self.PHITEX_RETRY_INTERVAL_MS
                            if count <= self.MAX_RETRIES
                            else self.RETRY_INTERVAL_MS)
            else:
                retry_ms = self.RETRY_INTERVAL_MS
            self.schedule_build(document, retry_ms)
            return False

        self._retry_counts.pop(document, None)

        # save the edited document if it has unsaved changes, then build.
        if document.source_buffer.get_modified():
            if not document.save_to_disk():
                # 保存失败，跳过构建（toast 已由 save_to_disk 弹出）
                return False

        active_document = self.workspace.get_active_document()
        if active_document == None:
            active_document = document
        # 标记本次构建为自动构建触发。build_log.update_items 据此结合
        # auto_build_autoshow_errors 设置决定是否弹出日志弹窗。
        target.build_system.is_auto_build = True
        target.build_system.build_and_forward_sync(active_document)
        return False

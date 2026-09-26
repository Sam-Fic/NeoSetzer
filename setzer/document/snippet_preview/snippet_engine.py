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

'''snippet 编译引擎（公式 hover 预览与 TikZ 侧栏预览共用，每文档一份）。

职责：把「包装好的完整 LaTeX 文档」编译成单页 PDF、渲染成裁剪贴图，并按
「渲染结果内容哈希」驱动三级数据源：内存 LRU → 磁盘 PDF 缓存 → 后台单遍
编译（引擎取用户的 latex_interpreter）。

线程模型：编译与 Poppler 渲染都在 daemon 线程完成（每个 snippet PDF
独立 Poppler.Document 实例）；结果一律经 GLib.idle_add 回主线程，
request() 从不同步调用回调，调用方无须担心「注册未完成回调先到」。
主线程只做缓存查询与回调分发，_waiting 无需加锁；仅 _running_key /
_pending 由工作线程读写，用 _lock 保护。

并发上限：同一时刻至多一个编译（Semaphore 语义由 _running_key 承担），
多出来的请求只保留**最新**一个在 _pending 槽——连打键盘时旧请求被丢弃，
不会排队成编译器队列。被丢弃的 key 会以 None 收尾，保证回调恰好一次。

取消作用域：request() 的 tag 用于把取消限制在同一前端内。公式 hover 在
每次按键时取消自己的请求（弹窗收起），而 TikZ 侧栏必须保留上一帧、不能
被 hover 的取消牵连，反之亦然。

失败原因：编译失败时回调只收到 None（契约不变，hover 前端无须关心原因），
但 TikZ 侧栏要把「File 'x.sty' not found」这类首条错误显示出来。原因经
get_delivery_failure_reason() 暴露，**仅在回调执行期间有效**——它描述的是
「本次投递」，回调返回后即失效，避免为短期诊断信息维护一份长期映射。
'''

import os
import shutil
import tempfile
import threading

from gi.repository import GLib

from setzer.app.service_locator import ServiceLocator
from setzer.document.snippet_preview.snippet_cache import SnippetCache
from setzer.document.snippet_preview.snippet_compiler import compile_snippet
from setzer.document.snippet_preview.snippet_wrapper import cache_key
from setzer.document.snippet_preview.render import (
    render_pdf_to_pixbuf,
    texture_from_pixbuf,
)


class SnippetEngine(object):

    def __init__(self, document, cache_subdir='snippet_preview_cache',
                 work_prefix='setzer-snippet-'):
        self.document = document
        self.settings = ServiceLocator.get_settings()
        config_folder = ServiceLocator.get_config_folder()
        self.cache = SnippetCache(os.path.join(config_folder, cache_subdir))
        # 会话级编译工作目录：刻意不放文档目录——临时 .tex/.pdf 会触发
        # 文档目录的文件监视与重建逻辑（外部 PDF 监视等）。
        self._work_dir = tempfile.mkdtemp(prefix=work_prefix)
        self._lock = threading.Lock()
        self._running_key = None     # 工作线程正在处理的 key
        self._pending = None         # 至多一个待编译 (key, wrapped, cwd)
        self._waiting = dict()       # request_id -> (key, tag, callback)，仅主线程
        self._next_request_id = 0
        # 本次投递的失败原因：仅 _deliver_pixbuf 调用回调期间非 None。
        self._delivery_failure_reason = None

    # ---------- 查询 ----------

    def get_engine_name(self):
        return self.settings.get_value('preferences', 'latex_interpreter')

    def is_engine_available(self):
        return shutil.which(self.get_engine_name()) is not None

    def get_delivery_failure_reason(self):
        '''本次投递的失败原因（'' = 未知）；仅在回调执行期间有效。

        取消/顶掉的请求返回 None 时原因为空——那不是编译错误，调用方
        不应把它显示成失败原因。
        '''
        return self._delivery_failure_reason or ''

    # ---------- 请求 ----------

    def request(self, wrapped_text, cwd=None, callback=None, tag=None):
        '''请求 wrapped_text 的贴图。

        callback(request_id, texture_or_None) 恰好调用一次，总是异步
        （idle 回主线程）：texture 为 None 表示不可预览（编译器缺失、
        编译失败）或请求已因取消/关闭作废。返回 request_id 供调用方比对；
        无法发起时返回 None（callback 不会被调）。

        cwd 为编译子进程的工作目录（None = 临时工作目录）：多文件项目的
        \\includegraphics 相对路径需要 root 文件所在目录才能解析。
        '''
        engine = self.get_engine_name()
        if shutil.which(engine) is None:
            return None
        key = cache_key(wrapped_text, engine, cwd)
        request_id = self._next_request_id
        self._next_request_id += 1
        self._waiting[request_id] = (key, tag, callback)
        texture = self.cache.get(key)
        if texture is not None:
            GLib.idle_add(self._deliver_texture, request_id, texture)
        else:
            self._schedule(key, wrapped_text, cwd, engine)
        return request_id

    def cancel_waiting(self, tag=None):
        '''让挂起的请求以 None 收尾（缓冲变更/功能关闭/文档关闭）。

        tag=None 取消全部；给定 tag 时只取消该前端自己的请求。
        '''
        if not self._waiting:
            return
        if tag is None:
            waiting = list(self._waiting.items())
        else:
            waiting = [(rid, entry) for rid, entry in self._waiting.items()
                       if entry[1] == tag]
        for rid, (_key, _tag, _callback) in waiting:
            del self._waiting[rid]
        for rid, (_key, _tag, callback) in waiting:
            callback(rid, None)

    # ---------- 编排 ----------

    def _schedule(self, key, wrapped_text, cwd, engine):
        '''主线程：启动或排队一次编译（至多一个在飞 + 一个待办）。'''
        with self._lock:
            if key == self._running_key:
                return  # 已在上：本请求挂表等待其结果
            if self._pending is not None:
                if self._pending[0] == key:
                    return  # 已排队：同上
                # 让位给更新的请求：被顶掉的 key 以 None 收尾（回调契约
                # 「恰好一次」），再由本次请求占据待办槽。
                evicted_key = self._pending[0]
                self._pending = (key, wrapped_text, cwd)
                GLib.idle_add(self._deliver_pixbuf, evicted_key, None)
                return
            if self._running_key is not None:
                self._pending = (key, wrapped_text, cwd)
                return
            self._running_key = key
        self._start_worker(key, wrapped_text, cwd, engine)

    def _start_worker(self, key, wrapped_text, cwd, engine):
        thread = threading.Thread(
            target=self._compile_worker,
            args=(key, wrapped_text, cwd, engine), daemon=True)
        thread.start()

    def _compile_worker(self, key, wrapped_text, cwd, engine):
        '''工作线程：编译 + 渲染，然后接续待办槽里的下一个请求。

        异常不允许逃逸（会静默挂掉线程）：任何失败都降级为「不可预览」。
        '''
        while True:
            pixbuf = None
            reason = ''
            try:
                if not self.cache.has_pdf(key):
                    diagnostics = dict()
                    pdf_path = compile_snippet(
                        wrapped_text, engine, self._work_dir, self.cache, key,
                        cwd=cwd, diagnostics=diagnostics)
                    reason = diagnostics.get('reason') or ''
                else:
                    pdf_path = self.cache.pdf_path(key)
                if pdf_path is not None:
                    pixbuf = render_pdf_to_pixbuf(pdf_path)
            except Exception:
                pixbuf = None
            GLib.idle_add(self._deliver_pixbuf, key, pixbuf, reason)

            with self._lock:
                if self._pending is None:
                    self._running_key = None
                    return
                key, wrapped_text, cwd = self._pending
                self._pending = None
                self._running_key = key

    # ---------- 主线程交付 ----------

    def _deliver_pixbuf(self, key, pixbuf, reason=''):
        '''主线程：Pixbuf → Texture，入缓存，分发给所有等待该 key 的请求。

        reason 仅在**本次**回调期间经 get_delivery_failure_reason() 可读，
        循环结束即清空：它对取消/顶掉的投递恒为空串，调用方据此区分
        「编译失败」与「请求作废」。
        '''
        texture = texture_from_pixbuf(pixbuf)
        if texture is not None:
            self.cache.put(key, texture)
        waiting = [(rid, entry) for rid, entry in self._waiting.items()
                   if entry[0] == key]
        for rid, _entry in waiting:
            del self._waiting[rid]
        self._delivery_failure_reason = reason if texture is None else ''
        try:
            for rid, (_key, _tag, callback) in waiting:
                callback(rid, texture)
        finally:
            self._delivery_failure_reason = None
        return False  # idle 一次性

    def _deliver_texture(self, request_id, texture):
        '''主线程：内存缓存命中的直接交付路径。'''
        entry = self._waiting.pop(request_id, None)
        if entry is not None:
            _key, _tag, callback = entry
            callback(request_id, texture)
        return False

    def shutdown(self):
        '''文档关闭清理：作废挂起请求、删除临时目录。

        正在飞的后台编译不取消（daemon 线程，结果经 idle 投递时 _waiting
        已空，自然丢弃；单次编译 <30s，不值得加终止协议）。
        '''
        self.cancel_waiting()
        shutil.rmtree(self._work_dir, ignore_errors=True)

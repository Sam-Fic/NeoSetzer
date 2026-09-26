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

'''公式 hover 预览控制器（文档级，仅 LaTeX 文档构造）。

职责：
- 暴露 parser 解析出的数学区域查询（symbols['math_regions']）；
- 按「渲染结果内容哈希」驱动三级数据源：内存 LRU → 磁盘 PDF 缓存 →
  后台单遍编译（引擎取用户的 latex_interpreter，走文档自身 preamble）；
- 失效管理：buffer 变更即让挂起请求以 None 收尾（弹窗层据此收起），
  设置开关即时生效。

线程模型：编译与 Poppler 渲染都在 daemon 线程完成（每个 snippet PDF
独立 Poppler.Document 实例——与 preview_page_renderer 的既有先例一致）；
结果一律经 GLib.idle_add 回主线程，request() 从不同步调用回调，调用方
（document_controller）无须担心「注册未完成回调先到」。主线程只做缓存
查询与回调分发，_waiting 无需加锁；仅 _pending_keys 由工作线程增删，用
锁保护。

贴图渲染：Poppler 整页渲染到 cairo ARGB32 surface（密度 RENDER_DENSITY
px/pt），numpy 按背景色裁掉页边白（ink bbox），经 GdkPixbuf 转为
Gdk.Texture。Pixbuf 在工作线程构建，Texture 延迟到主线程创建。
'''

import os
import shutil
import tempfile
import threading

import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Poppler', '0.18')
from gi.repository import Gdk, GLib, Poppler

import numpy

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.document.math_preview.math_region_finder import find_region_at
from setzer.document.math_preview.snippet_wrapper import build_snippet_document, cache_key
from setzer.document.math_preview.math_preview_cache import MathPreviewCache
from setzer.document.math_preview import math_preview_compiler

# 单个公式的源文本上限：超长环境（数百行的 align 块）编译慢、贴图巨大，
# hover 预览价值低，直接跳过。
_MAX_REGION_CHARS = 3000

# 驻留时长（ms）：指针在数学区域内停留这么久才弹窗——避免划过公式时
# 连环弹窗。由 document_controller 使用。
DWELL_MS = 450

# 渲染密度（像素/pt）：2x 保证 hidpi 下清晰，A4 页约 1190×1684 px。
_RENDER_DENSITY = 2.0

# 裁剪后四周保留的白边（渲染像素）。
_CROP_PADDING_PX = 8


class MathPreview(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.settings = ServiceLocator.get_settings()
        config_folder = ServiceLocator.get_config_folder()
        self.cache = MathPreviewCache(os.path.join(config_folder, 'math_preview_cache'))
        # 会话级编译工作目录：刻意不放文档目录——临时 .tex/.pdf 会触发
        # 文档目录的文件监视与重建逻辑（外部 PDF 监视等）。
        self._work_dir = tempfile.mkdtemp(prefix='setzer-math-preview-')
        self._lock = threading.Lock()
        self._pending_keys = set()   # 正在编译/渲染的 key（工作线程增删）
        self._waiting = dict()       # request_id -> (key, callback)，仅主线程
        self._next_request_id = 0
        self.settings.connect('settings_changed', self.on_settings_changed)
        self.document.source_buffer.connect('changed', self.on_buffer_changed)

    # ---------- 查询 ----------

    def get_region_at(self, offset):
        '''offset 所在数学区域（MathRegion）；不在任何区域内返回 None。'''
        symbols = getattr(self.document.parser, 'symbols', {})
        regions = symbols.get('math_regions')
        if not regions:
            return None
        return find_region_at(regions, offset)

    def is_engine_available(self):
        engine = self.settings.get_value('preferences', 'latex_interpreter')
        return shutil.which(engine) is not None

    def request(self, offset, callback):
        '''请求 offset 所在公式的贴图。

        callback(request_id, texture_or_None) 恰好调用一次，总是异步
        （idle 回主线程）：texture 为 None 表示不可预览（无区域、超长、
        编译器缺失、编译失败）或请求已因 buffer 变更/关闭作废。返回
        request_id 供调用方比对；无法发起时返回 None（callback 不会被调）。
        '''
        region = self.get_region_at(offset)
        if region is None:
            return None
        if region.end - region.start > _MAX_REGION_CHARS:
            return None
        engine = self.settings.get_value('preferences', 'latex_interpreter')
        if shutil.which(engine) is None:
            return None
        wrapped = build_snippet_document(region.raw, self._get_document_text())
        key = cache_key(wrapped, engine)
        request_id = self._next_request_id
        self._next_request_id += 1
        self._waiting[request_id] = (key, callback)
        texture = self.cache.get(key)
        if texture is not None:
            GLib.idle_add(self._deliver_texture, request_id, texture)
        else:
            self._start_compile(key, wrapped, engine)
        return request_id

    # ---------- 编排 ----------

    def _start_compile(self, key, wrapped, engine):
        with self._lock:
            if key in self._pending_keys:
                return  # 已有同 key 任务在飞：本请求挂表等待其结果
            self._pending_keys.add(key)
        thread = threading.Thread(
            target=self._compile_worker, args=(key, wrapped, engine), daemon=True)
        thread.start()

    def _compile_worker(self, key, wrapped, engine):
        pixbuf = None
        try:
            if not self.cache.has_pdf(key):
                pdf_path = math_preview_compiler.compile_snippet(
                    wrapped, engine, self._work_dir, self.cache, key)
            else:
                pdf_path = self.cache.pdf_path(key)
            if pdf_path is not None:
                pixbuf = self._render_pixbuf(pdf_path)
        except Exception:
            # 后台线程不允许异常逃逸（会静默挂掉线程）：任何失败都降级
            # 为「该公式不可预览」。
            pixbuf = None
        finally:
            with self._lock:
                self._pending_keys.discard(key)
        GLib.idle_add(self._deliver_pixbuf, key, pixbuf)

    def _render_pixbuf(self, pdf_path):
        '''工作线程：snippet PDF → 裁剪后的 GdkPixbuf（RGB，无 alpha）。'''
        pdf_document = Poppler.Document.new_from_file(GLib.filename_to_uri(pdf_path))
        if pdf_document.get_n_pages() < 1:
            return None
        page = pdf_document.get_page(0)
        page_width, page_height = page.get_size()
        surface_width = max(1, int(page_width * _RENDER_DENSITY))
        surface_height = max(1, int(page_height * _RENDER_DENSITY))
        surface = cairo.ImageSurface(cairo.Format.ARGB32, surface_width, surface_height)
        context = cairo.Context(surface)
        context.set_source_rgb(1, 1, 1)
        context.paint()
        context.scale(_RENDER_DENSITY, _RENDER_DENSITY)
        page.render(context)
        return self._crop_surface_to_pixbuf(surface, surface_width, surface_height)

    def _crop_surface_to_pixbuf(self, surface, surface_width, surface_height):
        '''按背景色裁掉页边白，返回裁剪区 GdkPixbuf；全白页返回 None。

        cairo ARGB32 在小端机器上字节序为 BGRA（premultiplied）；只需
        「与角落背景色不同」的判断，四通道一起比较即可，不解释语义。
        '''
        if surface.get_format() != cairo.Format.ARGB32:
            return None
        # stride 以字节计（行尾可能含对齐 padding）：每行 stride//4 个像素，
        # 有效的只有前 surface_width 个，padding 列不参与背景比对。
        stride = surface.get_stride()
        raw = numpy.frombuffer(surface.get_data(), dtype=numpy.uint8)
        raw = raw[:surface_height * stride].reshape(surface_height, stride // 4, 4)
        background = raw[0, 0].copy()
        ink = numpy.any(raw[:, :surface_width, :] != background, axis=2)
        rows = numpy.flatnonzero(ink.any(axis=1))
        cols = numpy.flatnonzero(ink.any(axis=0))
        if rows.size == 0 or cols.size == 0:
            return None
        top = max(0, int(rows[0]) - _CROP_PADDING_PX)
        bottom = min(surface_height, int(rows[-1]) + 1 + _CROP_PADDING_PX)
        left = max(0, int(cols[0]) - _CROP_PADDING_PX)
        right = min(surface_width, int(cols[-1]) + 1 + _CROP_PADDING_PX)
        return Gdk.pixbuf_get_from_surface(
            surface, left, top, right - left, bottom - top)

    # ---------- 主线程交付 ----------

    def _deliver_pixbuf(self, key, pixbuf):
        '''主线程：Pixbuf → Texture，入缓存，分发给所有等待该 key 的请求。'''
        texture = None
        if pixbuf is not None:
            try:
                texture = self._texture_from_pixbuf(pixbuf)
            except Exception:
                texture = None
        if texture is not None:
            self.cache.put(key, texture)
        waiting = [(rid, cb) for rid, (k, cb) in self._waiting.items() if k == key]
        for rid, _cb in waiting:
            del self._waiting[rid]
        for rid, cb in waiting:
            cb(rid, texture)
        return False  # idle 一次性

    @staticmethod
    def _texture_from_pixbuf(pixbuf):
        '''Pixbuf → Gdk.Texture。

        优先 Gdk.MemoryTexture（4.6+ 稳定 API）；Gdk.Texture.new_for_pixbuf
        在新版 GTK 已弃用（触发 DeprecationWarning），仅作老版本回退。
        pixbuf_get_from_surface 返回无 alpha 的 3 通道 RGB，对应
        MemoryFormat.R8G8B8；防御性兜底 4 通道形态。
        '''
        if hasattr(Gdk, 'MemoryTexture'):
            if pixbuf.get_n_channels() == 3 and not pixbuf.get_has_alpha():
                memory_format = Gdk.MemoryFormat.R8G8B8
            else:
                memory_format = Gdk.MemoryFormat.R8G8B8A8
            return Gdk.MemoryTexture.new(
                pixbuf.get_width(), pixbuf.get_height(), memory_format,
                GLib.Bytes.new(pixbuf.get_pixels()), pixbuf.get_rowstride())
        return Gdk.Texture.new_for_pixbuf(pixbuf)

    def _deliver_texture(self, request_id, texture):
        '''主线程：内存缓存命中的直接交付路径。'''
        entry = self._waiting.pop(request_id, None)
        if entry is not None:
            _key, callback = entry
            callback(request_id, texture)
        return False

    def _cancel_waiting(self):
        '''让所有挂起请求以 None 收尾（缓冲变更/关闭/功能关闭）。'''
        if not self._waiting:
            return
        waiting = list(self._waiting.items())
        self._waiting.clear()
        for rid, (_key, callback) in waiting:
            callback(rid, None)

    # ---------- 信号 ----------

    def on_buffer_changed(self, buffer):
        self._cancel_waiting()
        self.add_change_code('math_preview_invalidated')

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'preferences' and item == 'math_hover_preview' and not value:
            self._cancel_waiting()
            self.add_change_code('math_preview_invalidated')

    def shutdown(self):
        '''文档关闭清理：断开单例信号、作废挂起请求、删除临时目录。

        正在飞的后台编译不取消（daemon 线程，结果经 idle 投递时
        _waiting 已空，自然丢弃；单次编译 <30s，不值得加终止协议）。
        '''
        self._cancel_waiting()
        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        try:
            self.document.source_buffer.disconnect('changed', self.on_buffer_changed)
        except (TypeError, KeyError, AttributeError):
            pass
        shutil.rmtree(self._work_dir, ignore_errors=True)

    def _get_document_text(self):
        buffer = self.document.source_buffer
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

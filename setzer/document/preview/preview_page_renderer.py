#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
from gi.repository import GObject, Gdk, GLib
import cairo

import threading, queue
import time
import math
import numpy as np

from setzer.app.color_manager import ColorManager
from setzer.document.preview.magnifier_geometry import (
    MAGNIFIER_TILE_FACTOR,
    apply_magnifier_crop_transform,
    magnifier_tile_coverage,
)
from setzer.helpers.observable import Observable


class PreviewPageRenderer(Observable):

    def __init__(self, preview):
        Observable.__init__(self)
        self.preview = preview
        self.maximum_rendered_pixels = 20000000

        self.visible_pages_lock = threading.Lock()
        self.visible_pages = list()
        # visible_pages_additional 在 update_rendered_pages 中赋值。但后台线程
        # render_page_loop 在 is_active=True 时会访问它；activate() 先置
        # is_active=True 再调 update_rendered_pages，理论上存在竞态窗口。
        # 预初始化为空区间 [0, -1] 使线程访问时 is_visible 恒为 False，避免
        # AttributeError 导致渲染线程静默崩溃（线程异常不会冒泡到主线程）。
        self.visible_pages_additional = [0, -1]
        self.page_width = None
        self.pdf_version = None
        self.rendered_pages = dict()
        self.is_active_lock = threading.Lock()
        self.is_active = False

        self.preview.connect('position_changed', self.on_layout_or_position_changed)
        self.preview.connect('layout_changed', self.on_layout_or_position_changed)
        self.preview.connect('recolor_pdf_changed', self.on_recolor_pdf_changed)
        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.preview.document.settings.connect('settings_changed', self._settings_callback)

        self.page_render_count_lock = threading.Lock()
        self.page_render_count = dict()
        # 每页只跟踪最新目标，避免同一帧的 layout/position 通知反复使任务过期。
        self._pending_page_renders = {}
        self.render_queue = queue.Queue()
        self.render_queue_low_priority = queue.Queue()
        self.rendered_pages_queue = queue.Queue()

        # 放大镜页级瓦片缓存管线：光标所在页以镜头密度渲染一块固定尺寸
        # 的瓦片（边长 = MAGNIFIER_TILE_FACTOR × 镜面，像素量与缩放无关，
        # 密度永不需要设上限——任何缩放下放大倍率恒定）。瓦片锚定在光标
        # 处，光标漂移超出重建阈值才重新入队构建（后台线程，任务复用
        # render_queue 高优先级）；主线程每帧从瓦片纯 cairo 裁剪出镜面，
        # 与光标严格同步（不再有 stale 丢弃竞态）。瓦片未就绪或镜头窗口
        # 探出旧瓦片时，回退到 rendered_pages 整页纹理放大裁剪（内容位置
        # 与倍率均正确，仅略软）。通知链沿用 magnified_pages_queue + idle
        # → 'magnifier_result_ready'，结果条目带 kind='magnifier_cache'，
        # controller 收到后重新裁剪。
        # int/dict 赋值受 GIL 保护，跨线程读写无需额外同步；锁仅保证
        # 「判重 + 入队」的原子性。
        self.magnified_pages_queue = queue.Queue()
        self._magnifier_request_lock = threading.Lock()
        self._magnifier_latest_request_id = 0
        # 瓦片缓存条目：{'page', 'surface', 'density', 'pdf_version',
        # 'colors_key', 'anchor_x_pt', 'anchor_y_pt'} 或 None。渲染线程
        # 写入、主线程读取，单条目整替（GIL 下原子）。
        self._magnifier_page_cache = None
        # 在途构建任务（dict 身份比对判重 / 过期）；invalidate（取消 /
        # 上下文变化）把 pending 置 None 并排空队列，在途任务完成后因身份
        # 不一致被丢弃。
        self._magnifier_cache_pending = None

        # 后台线程惰性启动；结果只通过合并的一次性 idle 交付。
        self._render_thread_started = False
        self._render_thread = None
        self._result_delivery_lock = threading.Lock()
        self._rendered_pages_idle_id = None
        self._magnifier_result_idle_id = None
        self._shutting_down = False

    def on_layout_or_position_changed(self, notifying_object):
        if self.preview.layout != None:
            self.update_rendered_pages()
            self._request_rendered_pages_delivery()
        else:
            # layout 为 None 通常意味着 PDF 切换（preview.load_pdf 置 layout=None
            # 并发 layout_changed）。清空 page_render_count，使队列中所有积压
            # todo 失效（perf-12 问题 3：旧 PDF 高页号条目残留会导致
            # render_page_loop KeyError）。
            # rendered_pages 不再整体清空：旧版本纹理继续作占位显示
            # （见 update_rendered_pages 的驱逐策略），新版本渲染结果到达后逐页
            # 替换，构建成功后预览不再整屏白底等重绘。poppler 文档已不存在时
            # （无 PDF 可回退）内容不会再被绘制，直接清空。
            if self.preview.poppler_document is None:
                self.rendered_pages = dict()
            with self.page_render_count_lock:
                self.page_render_count = dict()
            self._pending_page_renders.clear()

    def on_recolor_pdf_changed(self, preview):
        self.update_rendered_pages()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'color_scheme':
            self.update_rendered_pages()

    def activate(self):
        with self._result_delivery_lock:
            if self._shutting_down:
                return
            with self.is_active_lock:
                self.is_active = True
        self._ensure_loops_started()
        self.update_rendered_pages()
        self._request_rendered_pages_delivery()

    def _ensure_loops_started(self):
        # 只启动渲染线程，不注册常驻结果轮询。
        with self._result_delivery_lock:
            if self._shutting_down or self._render_thread_started:
                return
            self._render_thread_started = True
            self._render_thread = threading.Thread(target=self.render_page_loop, daemon=True)
            self._render_thread.start()

    def invalidate_magnifier_requests(self):
        '''Discard active and queued lens work after a preview context change.

        同时丢弃页级缓存与在途构建任务：PDF / 布局（缩放、旋转）/ 反色的
        任何变化都会使旧缓存像素与密度失效。属性赋值兼容测试中的
        SimpleNamespace 桩。'''
        with self._magnifier_request_lock:
            self._magnifier_latest_request_id += 1
        self._magnifier_page_cache = None
        self._magnifier_cache_pending = None
        try:
            while True:
                self.magnified_pages_queue.get_nowait()
        except queue.Empty:
            pass

    def deactivate(self):
        with self.is_active_lock:
            self.is_active = False
        self.rendered_pages = dict()
        with self.visible_pages_lock:
            self.visible_pages = list()
        self.page_width = None
        self.pdf_version = None
        self._pending_page_renders.clear()
        # Preview hiding invalidates all in-flight lens surfaces as well.
        self.invalidate_magnifier_requests()

    def shutdown(self):
        '''禁止结果交付并取消 idle，唤醒后台线程退出而不阻塞 GTK 线程。'''
        with self._result_delivery_lock:
            self._shutting_down = True
            if self._rendered_pages_idle_id is not None:
                GLib.Source.remove(self._rendered_pages_idle_id)
                self._rendered_pages_idle_id = None
            if self._magnifier_result_idle_id is not None:
                GLib.Source.remove(self._magnifier_result_idle_id)
                self._magnifier_result_idle_id = None
        with self.is_active_lock:
            self.is_active = False
        self.rendered_pages = dict()
        with self.visible_pages_lock:
            self.visible_pages = list()
        # 清空渲染计数表，丢弃所有积压 todo（perf-12 问题 3：原 shutdown 不清，
        # PDF 切换后旧 PDF 高页号条目残留）。
        with self.page_render_count_lock:
            self.page_render_count = dict()
        self._pending_page_renders.clear()
        # 放大镜页级缓存（可达数十 MB）与在途构建任务一并释放。
        self._magnifier_page_cache = None
        self._magnifier_cache_pending = None
        # 禁止新结果入队后释放积压任务和 surface，再投递退出哨兵。
        for result_queue in (self.render_queue, self.render_queue_low_priority,
                             self.rendered_pages_queue, self.magnified_pages_queue):
            try:
                while True:
                    result_queue.get_nowait()
            except queue.Empty:
                pass
        self.render_queue.put(None)
        try:
            self.preview.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass

    def render_page_loop(self):
        while not self._shutting_down:
            with self.is_active_lock:
                is_active = self.is_active
            todo = None
            if is_active:
                # 取任务顺序：高优先级非阻塞 → 低优先级非阻塞 → 高优先级阻塞
                # 等待（50ms timeout）。高优先级永远优先；仅当两级队列都空时
                # 才阻塞等待新任务——任务入队即唤醒线程，延迟趋近 0。若高队列
                # 空而低队列有活（如滚动停滞后精修低分辨率占位），处理完当前
                # 任务后立即取走，不再有旧实现「高队列 get 等满 50ms 才看低
                # 队列」的停顿。timeout=0.05 保证 is_active 变 False 时能在
                # 50ms 内检测到并退出。shutdown 时投递哨兵 None 唤醒阻塞 get，
                # 让线程即时退出而非等满 50ms。
                try: todo = self.render_queue.get(block=False)
                except queue.Empty:
                    try: todo = self.render_queue_low_priority.get(block=False)
                    except queue.Empty:
                        try: todo = self.render_queue.get(block=True, timeout=0.05)
                        except queue.Empty:
                            todo = None
            else:
                time.sleep(0.05)
            # 哨兵 None（shutdown 投递）或 deactivate 期间积压的 None：跳过。
            if todo is None:
                continue
            # 放大镜缓存构建任务走独立的过期判定（任务身份 + generation）
            # 与结果投递路径，不读 page_render_count / visible_pages（那些
            # 键不存在）。
            if todo.get('kind') == 'magnifier_cache':
                self._process_magnifier_cache_todo(todo)
                continue
            with self.page_render_count_lock:
                # .get(-1) 哨兵避免 KeyError：on_layout_or_position_changed 在
                # PDF 切换时清空 page_render_count，若此时仍有 stale todo 在
                # 队列里被取出，直接访问会 KeyError。todo['render_count'] 恒为
                # ≥1 的正整数，与 -1 不等 → 被判为过时渲染任务并丢弃。
                render_count = self.page_render_count.get(todo['page_number'], -1)
            with self.visible_pages_lock:
                is_visible = (todo['page_number'] >= self.visible_pages_additional[0] and todo['page_number'] <= self.visible_pages_additional[1])
            if todo['render_count'] == render_count and is_visible:
                try:
                    # 与主线程 load_pdf 的竞态：通过有效性检查后文档仍可能被
                    # 替换（新文档页数更少时 get_page 返回 None）。线程异常不
                    # 会冒泡到主线程，且渲染线程崩溃后没有任何重启路径——预览
                    # 将永久空白，因此这里兜底吞掉并继续。
                    doc = self.preview.poppler_document
                    if doc is None:
                        raise RuntimeError('PDF no longer available')
                    page = doc.get_page(todo['page_number'])
                    if page is None:
                        raise RuntimeError('PDF page no longer available')
                    colors = todo['matching_theme_colors']
                    # draft 任务（快速滚动兜底）以 1/4 分辨率渲染：像素量约
                    # 1/16，产出快约一个数量级——滚动停不下来的页面先显示
                    # 略软的图像，滚动停滞后由全分辨率任务精修替换（见
                    # update_rendered_pages 的入队策略）。绘制端
                    # （draw_rendered_page）按 surface 实际尺寸缩放到页面矩形。
                    divisor = 4 if todo.get('draft', False) else 1
                    width = max(1, int(todo['page_width'] * todo['hidpi_factor'] / divisor))
                    height = max(1, int(todo['page_height'] * todo['hidpi_factor'] / divisor))
                    surface = cairo.ImageSurface(cairo.Format.ARGB32, width, height)
                    ctx = cairo.Context(surface)

                    ctx.set_source_rgba(1, 1, 1, 1)
                    ctx.rectangle(0, 0, width, height)
                    ctx.fill()

                    ctx.scale(todo['scale_factor'] * todo['hidpi_factor'] / divisor, todo['scale_factor'] * todo['hidpi_factor'] / divisor)
                    page.render(ctx)

                    if colors != None:
                        # 直接从 cairo surface 取数据到 numpy 做 alpha 提取 +
                        # Operator.IN 着色。原实现 4 次内存拷贝（12MB/页），现
                        # 2 次（np.frombuffer + .copy() → bytearray）。正确性：
                        # FORMAT_ARGB32 小端字节序为 BGRA，alpha 公式沿用原实现
                        # 的 B/G/R 加权和，最终视觉结果与旧 PIL 路径一致。
                        surface = self._apply_theme_recolor(surface, colors)

                    self._queue_rendered_page({'page_number': todo['page_number'], 'render_count': todo['render_count'], 'item': [surface, todo['page_width'], todo['page_height'], todo['pdf_version'], colors]})
                except Exception:
                    # 释放失败的精修请求，后续布局/滚动通知可重试；draft
                    # 失败不释放，因为同一请求的全分辨率任务仍在队列里。
                    if not todo.get('draft', False):
                        self._queue_rendered_page({
                            'page_number': todo['page_number'],
                            'render_count': todo['render_count'],
                            'item': None,
                        })
                    continue

    def _queue_rendered_page(self, result):
        with self._result_delivery_lock:
            if self._shutting_down:
                return
            self.rendered_pages_queue.put(result)
        self._request_rendered_pages_delivery()

    def _request_rendered_pages_delivery(self):
        with self._result_delivery_lock:
            if self._shutting_down or self._rendered_pages_idle_id is not None:
                return
            with self.is_active_lock:
                is_active = self.is_active
            if not is_active or self.preview.layout is None or self.rendered_pages_queue.empty():
                return
            self._rendered_pages_idle_id = GObject.idle_add(self._wake_rendered_pages_loop)

    def _wake_rendered_pages_loop(self):
        '''一次性交付；消费期间保持 pending，结束后补唤醒迟到的结果。'''
        try:
            self.rendered_pages_loop()
        finally:
            with self._result_delivery_lock:
                self._rendered_pages_idle_id = None
            # 与生产者使用同一把锁，清标志后再次检查队列，避免丢失唤醒。
            self._request_rendered_pages_delivery()
        return False

    def _apply_theme_recolor(self, surface, colors):
        '''对 ARGB32 surface 做「深色化」处理并返回新 surface。

        1) numpy 按 BGRA 字节序把亮度映射进 alpha（白底变透明）；
        2) Gdk 色 colors[0] 以 Operator.IN 着色（保留目标 alpha）。
        整页渲染与放大镜局部渲染共用；colors[1] 与旧实现一致地不参与着色。'''
        width = surface.get_width()
        height = surface.get_height()
        buf = surface.get_data()
        img_data = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4).copy()

        alpha = 255 - 0.3 * img_data[..., 0] - 0.6 * img_data[..., 1] - 0.1 * img_data[..., 2]
        img_data[..., 3] = alpha.astype(np.uint8)

        im_bytes = bytearray(img_data.tobytes())
        surface = cairo.ImageSurface.create_for_data(im_bytes, cairo.FORMAT_ARGB32, width, height)
        temp_ctx = cairo.Context(surface)

        Gdk.cairo_set_source_rgba(temp_ctx, colors[0])
        temp_ctx.set_operator(cairo.Operator.IN)
        temp_ctx.rectangle(0, 0, width, height)
        temp_ctx.fill()
        return surface

    # ---- 放大镜页级瓦片缓存 ----

    @staticmethod
    def _magnifier_colors_key(colors):
        '''反色配置的可哈希键（None 或 RGBA 分量元组），用于缓存有效性
        判定；Gdk.RGBA 每次 get_ui_color 返回新引用，不可按身份比较。'''
        if colors is None:
            return None
        return tuple((c.red, c.green, c.blue, c.alpha) for c in colors)

    def request_magnifier_page_cache(self, page_number, size_px, density,
                                     pdf_version, colors,
                                     anchor_x_pt, anchor_y_pt):
        '''请求（去重）以 (anchor_x_pt, anchor_y_pt) 为锚点构建镜头密度
        瓦片（主线程调用）。

        去重两级：
        1. 现有瓦片宽裕覆盖当前光标（光标离瓦片锚点 ≤ 重建阈值
           tile_half − 2×lens_half）→ 不需要构建。⚠ 不能用硬边界
           （tile_half − lens_half）判缓存命中：那会让重建推迟到镜头窗口
           贴到瓦片边缘才触发，过渡期镜头探出瓦片露白——重建必须在
           提前一个镜头窗口的阈值处触发，旧瓦片供帧到硬边界为止；
        2. 在途任务的锚点已覆盖当前光标（光标离在途锚点 ≤ 重建阈值）→
           不重复入队。连续快速移动时锚点每次替换都前移一个阈值距离，
           构建频率随移动速度有界（约每 2×重建阈值 pt 一次），不会逐帧
           替换饿死构建。

        任务以 dict 身份做过期判定——invalidate（取消 / PDF / 布局 /
        反色变化）把 pending 置 None 并排空队列，在途任务完成后因身份
        不一致被丢弃。任务放 render_queue 高优先级队列——放大镜必须尽快
        从软帧转清晰帧。'''
        if self.preview.poppler_document is None:
            return
        tile_half_pt, lens_half_pt, rebuild_pt = magnifier_tile_coverage(
            size_px, density)
        colors_key = self._magnifier_colors_key(colors)
        cache = self._magnifier_page_cache
        if (cache is not None and cache['page'] == page_number
                and cache['pdf_version'] == pdf_version
                and cache['density'] == density
                and cache['colors_key'] == colors_key
                and max(abs(anchor_x_pt - cache['anchor_x_pt']),
                        abs(anchor_y_pt - cache['anchor_y_pt']))
                <= rebuild_pt):
            return
        with self._magnifier_request_lock:
            pending = self._magnifier_cache_pending
            if (pending is not None
                    and pending['page_number'] == page_number
                    and pending['density'] == density
                    and pending['pdf_version'] == pdf_version
                    and pending['colors_key'] == colors_key
                    and max(abs(anchor_x_pt - pending['anchor_x_pt']),
                            abs(anchor_y_pt - pending['anchor_y_pt']))
                    <= rebuild_pt):
                return
            task = {
                'kind': 'magnifier_cache',
                'page_number': int(page_number),
                'size_px': max(1, int(size_px)),
                'density': float(density),
                'pdf_version': pdf_version,
                'matching_theme_colors': colors,
                'colors_key': colors_key,
                'anchor_x_pt': float(anchor_x_pt),
                'anchor_y_pt': float(anchor_y_pt),
            }
            self._magnifier_cache_pending = task
            self.render_queue.put(task)

    def _process_magnifier_cache_todo(self, todo):
        '''渲染线程内处理一个瓦片构建任务：身份过期即弃；否则以镜头密度
        渲染锚点周围的固定尺寸瓦片（白底、构建期反色）后存入缓存，并
        投递通知让主线程按当前光标重新裁剪。'''
        with self._magnifier_request_lock:
            if todo is not self._magnifier_cache_pending:
                # 已被 invalidate 清空或被更新的任务取代：丢弃。
                return

        try:
            doc = self.preview.poppler_document
            if doc is None:
                raise RuntimeError('PDF no longer available')
            page = doc.get_page(todo['page_number'])
            if page is None:
                raise RuntimeError('PDF page no longer available')

            density = todo['density']
            tile_px = MAGNIFIER_TILE_FACTOR * max(1, int(todo['size_px']))
            half_pt = tile_px / (2.0 * density)
            surface = cairo.ImageSurface(cairo.Format.ARGB32, tile_px, tile_px)
            ctx = cairo.Context(surface)

            ctx.set_source_rgba(1, 1, 1, 1)
            ctx.rectangle(0, 0, tile_px, tile_px)
            ctx.fill()

            # 密度含 hidpi 与 factor；page.render 内部自带的 y 翻转负责把
            # PDF 原生 y-up 内容落进与布局映射一致的 top-down 空间（同整页
            # 渲染路径）。瓦片存未旋转页面像素，旋转由裁剪端应用。锚点
            # 映射到瓦片中心（⚠ 不是原点——原点会让瓦片只含锚点右下方的
            # 内容，光标向左上移动即采样到瓦片外）；越出页面的部分保留
            # 白底，与屏幕纸面一致。
            ctx.scale(density, density)
            ctx.translate(-todo['anchor_x_pt'] + half_pt,
                          -todo['anchor_y_pt'] + half_pt)
            page.render(ctx)

            colors = todo['matching_theme_colors']
            if colors != None:
                surface = self._apply_theme_recolor(surface, colors)
        except Exception:
            # shutdown / PDF 切换窗口期文档可能已被替换或释放（或内存不足）：
            # 清 pending 允许下次重试，静默丢弃。
            with self._magnifier_request_lock:
                if self._magnifier_cache_pending is todo:
                    self._magnifier_cache_pending = None
            return

        self._magnifier_page_cache = {
            'page': todo['page_number'],
            'surface': surface,
            'density': density,
            'pdf_version': todo['pdf_version'],
            'colors_key': todo['colors_key'],
            'anchor_x_pt': todo['anchor_x_pt'],
            'anchor_y_pt': todo['anchor_y_pt'],
        }
        with self._magnifier_request_lock:
            if self._magnifier_cache_pending is todo:
                self._magnifier_cache_pending = None

        with self._result_delivery_lock:
            if self._shutting_down:
                return
            self.magnified_pages_queue.put({'kind': 'magnifier_cache'})
            if self._magnifier_result_idle_id is None:
                self._magnifier_result_idle_id = GObject.idle_add(self.on_magnifier_result_produced)

    def get_magnifier_surface(self, page_number, center_x_pt, center_y_pt,
                              out_css_size, hidpi_factor, density, rotation,
                              colors, pdf_version, scale_factor):
        '''主线程：产出当前光标处的镜面 ImageSurface（无可用内容返回 None）。

        瓦片命中（且镜头窗口仍完整落在瓦片内）→ 从瓦片 1:1 裁剪（清晰，
        恒定 MAGNIFICATION_FACTOR 倍放大）；瓦片未就绪或光标已漂到旧瓦片
        边缘（重建在途）→ 回退到 rendered_pages 的整页纹理放大裁剪——该
        纹理与屏幕上显示的是同一张图（draft 占位也一致），反色已烘入像素，
        内容位置与放大倍率均正确，仅略软。两条路径共用同一 density 换算，
        软→硬切换无取景/倍率跳变。无论命中哪条路径都确保瓦片构建任务已
        请求（去重见 request_magnifier_page_cache）。

        参数:
            page_number: 0-based 页号
            center_x_pt / center_y_pt: 裁剪中心（页面内 top-down PDF 点坐标，
                与布局映射 get_page_number_and_offsets_by_document_offsets
                的返回值同约定）
            out_css_size: 输出方形浮窗边长（css px）
            hidpi_factor: 设备像素比
            density: 镜头密度（= MAGNIFICATION_FACTOR × scale × hidpi）
            rotation: 预览旋转角（0/90/180/270），在裁剪时应用
            colors: 反色主题色元组或 None（仅用于构建任务；回退纹理已含反色）
            pdf_version: 内存 PDF 版本（瓦片 / 纹理有效性比对）
            scale_factor: 当前预览缩放（布局 css px / 页面 pt），用于回退
                纹理的密度换算
        '''
        if self.preview.poppler_document is None:
            return None
        size_px = max(1, int(out_css_size * hidpi_factor))
        colors_key = self._magnifier_colors_key(colors)
        tile_half_pt, lens_half_pt, _rebuild_pt = magnifier_tile_coverage(
            size_px, density)

        src = None
        source_density = 0.0
        # 裁剪中心：软路径源以页面左上为原点（q = p·d_src）；瓦片以锚点
        # 为中心（q = (p − anchor + H)·d_src，H = 瓦片半边长 pt），裁剪
        # 中心须换成「光标相对瓦片左上角」的坐标 c − anchor + H，否则
        # 内容整体偏移（曾把锚点放瓦片原点且漏加 H，双重错位）。
        crop_center_x, crop_center_y = center_x_pt, center_y_pt
        cache = self._magnifier_page_cache
        if (cache is not None and cache['page'] == page_number
                and cache['pdf_version'] == pdf_version
                and cache['density'] == density
                and cache['colors_key'] == colors_key
                and isinstance(cache['surface'], cairo.ImageSurface)
                and max(abs(center_x_pt - cache['anchor_x_pt']),
                        abs(center_y_pt - cache['anchor_y_pt']))
                <= tile_half_pt - lens_half_pt):
            # 瓦片仍完整覆盖镜头窗口。光标越过重建阈值但未越硬边界的
            # 过渡期也继续供帧（内容完整不露白），等待新瓦片构建完成。
            src = cache['surface']
            source_density = cache['density']
            crop_center_x = (center_x_pt - cache['anchor_x_pt'] + tile_half_pt)
            crop_center_y = (center_y_pt - cache['anchor_y_pt'] + tile_half_pt)
        else:
            item = self.rendered_pages.get(page_number)
            if (item is not None and item[3] == pdf_version
                    and isinstance(item[0], cairo.ImageSurface)
                    and int(item[1] or 0) > 0 and item[0].get_width() > 0):
                src = item[0]
                # item[1] 是渲染时的页面 css 宽（int，随缩放变化，= 页面
                # pt × scale）；纹理内容密度 = surface px / 页面 pt =
                # surface px × scale / css 宽。⚠ 不能漏乘 scale——曾漏乘
                # 导致软帧取样位置错（c/s 处）且倍率错（s·h 倍），与瓦片
                # 帧切换时表现为取景/倍率跳变（闪烁）。由 surface 实际宽
                # 反推可兼容 draft 占位（1/4 密度）。
                source_density = (item[0].get_width() * scale_factor
                                  / int(item[1]))

        self.request_magnifier_page_cache(
            page_number, size_px, density, pdf_version, colors,
            center_x_pt, center_y_pt)

        if src is None:
            return None
        return self._crop_magnifier_surface(src, source_density, density,
                                            rotation, crop_center_x,
                                            crop_center_y, size_px)

    def _crop_magnifier_surface(self, src, source_density, density, rotation,
                                center_x_pt, center_y_pt, size_px):
        '''从页级 surface 裁剪出镜面：白底 + crop transform + paint。

        采样区是源坐标下以 center 为中心、边长 size_px/density 的正方形
        （源 1 px = 1/density pt；center 的原点取决于源——整页纹理为页面
        左上，瓦片为其锚点）。旋转仅 90° 倍数，正方形旋转后仍轴对齐，
        paint 全图可精确覆盖。越出源范围的部分保留白底，与屏幕纸面一致。'''
        surface = cairo.ImageSurface(cairo.Format.ARGB32, size_px, size_px)
        ctx = cairo.Context(surface)
        ctx.set_source_rgba(1, 1, 1, 1)
        ctx.paint()
        apply_magnifier_crop_transform(ctx, size_px, density, source_density,
                                       rotation, center_x_pt, center_y_pt)
        ctx.set_source_surface(src, 0, 0)
        ctx.paint()
        return surface

    def on_magnifier_result_produced(self):
        '''主线程回调：发 change code 让 controller 消费 magnified_pages_queue。'''
        with self._result_delivery_lock:
            self._magnifier_result_idle_id = None
            if self._shutting_down:
                return False
        self.add_change_code('magnifier_result_ready')
        return False

    def rendered_pages_loop(self):
        '''消费当前可用结果，始终返回 False；不再作为重复定时器使用。'''
        with self._result_delivery_lock:
            if self._shutting_down:
                return False
        with self.is_active_lock:
            is_active = self.is_active
        if not is_active: return False

        # 渲染期间可能发生缩放、换色或 PDF 重载。只接收仍对应当前请求
        # 和布局的结果，避免动画中间帧覆盖最终尺寸的纹理。
        layout = self.preview.layout
        if layout is None:
            return False
        current_version = self.preview.pdf_version
        changed = False
        while self.rendered_pages_queue.empty() == False:
            try: todo = self.rendered_pages_queue.get(block=False)
            except queue.Empty: pass
            else:
                page_number = todo['page_number']
                item = todo['item']
                with self.page_render_count_lock:
                    current_count = self.page_render_count.get(page_number)
                if todo['render_count'] != current_count:
                    continue
                if item is None:
                    self._pending_page_renders.pop(page_number, None)
                    continue
                if (item[3] != current_version
                        or item[1] != int(layout.page_width_original)
                        or item[2] != int(layout.page_height_original)
                        or not self.visible_pages_additional[0] <= page_number <= self.visible_pages_additional[1]):
                    continue
                # 原子替换同一页的 surface；旧图一直保留到这里，没有超时白屏。
                self.rendered_pages[page_number] = item
                if item[0].get_width() >= item[1] * layout.hidpi_factor:
                    self._pending_page_renders.pop(page_number, None)
                changed = True
        if changed:
            self.add_change_code('rendered_pages_changed')
        return False

    def update_rendered_pages(self):
        with self.is_active_lock:
            is_active = self.is_active
        if not is_active: return
        if self.preview.layout == None: return

        hidpi_factor = self.preview.layout.hidpi_factor
        # Textures are always rendered at the un-rotated page dimensions.
        page_width = int(self.preview.layout.page_width_original)
        page_height = int(self.preview.layout.page_height_original)
        # The number of pages that fit vertically depends on the displayed
        # (possibly rotated) page height.
        displayed_page_height = int(self.preview.layout.page_height)

        offset = self.preview.view.content.scrolling_offset_y
        current_page = self.preview.layout.get_page_by_offset(offset) - 1

        visible_pages = [current_page, min(current_page + math.floor(self.preview.view.get_allocated_height() / displayed_page_height) + 1, self.preview.poppler_document.get_n_pages() - 1)]

        max_additional_pages = max(math.floor(self.maximum_rendered_pixels / (page_width * page_height * hidpi_factor * hidpi_factor) - visible_pages[1] + visible_pages[0]), 0)
        visible_pages_additional = [max(int(visible_pages[0] - max_additional_pages / 2), 0), min(int(visible_pages[1] + max_additional_pages / 2), self.preview.poppler_document.get_n_pages() - 1)]

        # 缓存版本号 = 内存 Poppler 文档的版本（preview.pdf_version），而非
        # 磁盘 mtime：编译期间编译器改写磁盘 PDF（mtime 一直变），但内存文档
        # 不变，已渲染页仍然有效。若按 mtime 失效，编译中滚动/缩放会清空缓存
        # 导致预览白屏（只剩页面底色），直到构建结束才恢复。
        pdf_version = self.preview.pdf_version
        with self.visible_pages_lock:
            self.visible_pages = visible_pages
            self.visible_pages_additional = visible_pages_additional
        self.page_width = page_width
        self.pdf_version = pdf_version

        if self.preview.recolor_pdf:
            colors = (ColorManager.get_ui_color('view_fg_color'), ColorManager.get_ui_color('view_bg_color'))
        else:
            colors = None

        changed = False
        # colors_changed 判定：stored[4] 与 colors 同为 None → 不变；
        # 仅一方为 None → 变；两者皆非 None → 比较 RGBA.equal。
        # 原实现每分支都重复 self.rendered_pages[page_number] 字典查找（5+ 次/页），
        # 缓存到 page_data 后每次循环只查一次。滚动/缩放时此循环每次都跑。
        for page_number in list(self.rendered_pages):
            page_data = self.rendered_pages[page_number]
            stored_colors = page_data[4]
            if stored_colors is None or colors is None:
                colors_changed = (stored_colors is not colors)
            elif not stored_colors[0].equal(colors[0]) or not stored_colors[1].equal(colors[1]):
                colors_changed = True
            else:
                colors_changed = False

            # 尺寸/版本变化只请求精修，不驱逐仍可显示的纹理。侧栏动画每帧
            # 都会改变 fit-to-width 的页面尺寸，先删旧图会让异步渲染的等待期
            # 只剩白底。绘制端将旧 surface 缩放到当前布局，新结果就绪后替换。
            # 范围外页面和不匹配的配色仍驱逐，不另建一份旧图缓存。
            if colors_changed or page_number < visible_pages_additional[0] or page_number > visible_pages_additional[1]:
                del(self.rendered_pages[page_number])
                changed = True
        if changed:
            self.add_change_code('rendered_pages_changed')

        scale_factor = self.preview.layout.scale_factor

        # 仅遍历需要渲染的页面区间，而非整本 PDF。原实现 range(0, n_pages)
        # 对每个页号做两次字典查找 + 条件判断，对 100+ 页 PDF 而言每次滚动 /
        # 缩放都会做百次无谓迭代（区间外的页既不在 render_queue 也不在
        # render_queue_low_priority，循环体跳过仍要付 Python 字节码代价）。
        # 改为只扫 [visible_pages_additional[0], visible_pages_additional[1]]：
        # 这是 render_queue_low_priority 的入队区间，render_queue（高优先级）
        # 的 [visible_pages[0], visible_pages[1]] 必然包含其中。
        n_pages = self.preview.poppler_document.get_n_pages()
        lo = max(visible_pages_additional[0], 0)
        hi = min(visible_pages_additional[1], n_pages - 1)
        # 缓存局部引用：render_queue / render_queue_low_priority / rendered_pages
        # 在循环中每次 self.xxx 属性查找都要经 __dict__ 哈希；提到局部变量后
        # 走 LOAD_FAST。visible_pages 恒为 list（L203 赋值），原 `!= None`
        # 判断恒真，移除。rendered_pages 用 .get() 替代 `in` + `[]` 两次查找。
        render_queue = self.render_queue
        render_queue_low_priority = self.render_queue_low_priority
        rendered_pages = self.rendered_pages
        page_render_count = self.page_render_count
        vp_lo, vp_hi = visible_pages[0], visible_pages[1]
        color_key = None if colors is None else tuple(
            (c.red, c.green, c.blue, c.alpha) for c in colors)
        target = (page_width, page_height, scale_factor, hidpi_factor, pdf_version, color_key)
        # 滚出缓存区的未完成任务也失效；重新滚入时可以正常重新排队。
        for page_number in list(self._pending_page_renders):
            if not lo <= page_number <= hi:
                del self._pending_page_renders[page_number]
                with self.page_render_count_lock:
                    page_render_count[page_number] = page_render_count.get(page_number, 0) + 1
        # 全分辨率纹理的设备像素宽，用于识别低分辨率 draft 占位。
        full_device_width = page_width * hidpi_factor
        for page_number in range(lo, hi + 1):
            page_data = rendered_pages.get(page_number)
            # 版本不匹配的占位条目（旧 PDF 纹理）同样触发重绘：保留显示但
            # 必须排队产出新版本结果来替换。低分辨率 draft 占位（快速滚动
            # 时产出）内容已对但偏软，也要排队做全分辨率精修。
            needs_render = page_data is None or page_data[1] != page_width or page_data[2] != page_height or page_data[3] != pdf_version
            if not needs_render and page_data[0].get_width() < full_device_width:
                needs_render = True
            if not needs_render:
                if self._pending_page_renders.pop(page_number, None) is not None:
                    with self.page_render_count_lock:
                        page_render_count[page_number] = page_render_count.get(page_number, 0) + 1
                continue
            if needs_render:
                is_visible = vp_lo <= page_number <= vp_hi
                pending = self._pending_page_renders.get(page_number)
                # 已在渲染相同目标时不重复排队。预取页进入视口时允许提升优先级。
                if pending is not None and pending[0] == target and (pending[1] or not is_visible):
                    continue
                self._pending_page_renders[page_number] = (target, is_visible)
                with self.page_render_count_lock:
                    try:
                        page_render_count[page_number] += 1
                    except KeyError:
                        page_render_count[page_number] = 1

                    # 视口内完全没有纹理的页：先入队一个低分辨率 draft 任务
                    # （高优先级，排在同页全分辨率任务之前）。快速滚动滚入未
                    # 缓存页面时，全分辨率渲染要几十毫秒一页，期间页面纯白；
                    # draft 约 1/16 像素量，能抢先把「略软但有内容」的图像送
                    # 上屏幕，滚动停滞后全分辨率精修替换。
                    if page_data is None and page_number >= vp_lo and page_number <= vp_hi:
                        draft_task = dict()
                        draft_task['page_number'] = page_number
                        draft_task['render_count'] = page_render_count[page_number]
                        draft_task['scale_factor'] = scale_factor
                        draft_task['hidpi_factor'] = hidpi_factor
                        draft_task['page_width'] = page_width
                        draft_task['page_height'] = page_height
                        draft_task['pdf_version'] = pdf_version
                        draft_task['matching_theme_colors'] = colors
                        draft_task['draft'] = True
                        render_queue.put(draft_task)

                    render_task = dict()
                    render_task['page_number'] = page_number
                    render_task['render_count'] = page_render_count[page_number]
                    render_task['scale_factor'] = scale_factor
                    render_task['hidpi_factor'] = hidpi_factor
                    render_task['page_width'] = page_width
                    render_task['page_height'] = page_height
                    render_task['pdf_version'] = pdf_version
                    render_task['matching_theme_colors'] = colors

                    if page_number >= vp_lo and page_number <= vp_hi:
                        # 视口内、完全没有纹理的页：快速兜底已由 draft 任务
                        # 承担，全分辨率走低优先级——快速滚动中不与其他页的
                        # draft 抢渲染线程，滚动停下后自动精修。其余（占位
                        # 替换、精修）仍按原行为高优先级。
                        if page_data is None:
                            render_queue_low_priority.put(render_task)
                        else:
                            render_queue.put(render_task)
                    else:
                        render_queue_low_priority.put(render_task)

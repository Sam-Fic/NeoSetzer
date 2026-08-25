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
from setzer.document.preview.magnifier_geometry import apply_magnifier_transform
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
        self.pdf_date = None
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
        self.render_queue = queue.Queue()
        self.render_queue_low_priority = queue.Queue()
        self.rendered_pages_queue = queue.Queue()

        # 放大镜局部渲染管线：任务复用 render_queue（高优先级，跟手要求），
        # 结果走独立队列，避免混入整页结果被 rendered_pages_loop 当成整页
        # 缓存消费。过期判定与整页不同——不用 page_render_count，而用单调
        # 递增的 request_id：光标移动中连续入队多个请求，只有 id 等于最新值
        # 的结果会被采用，旧结果在渲染线程内即被丢弃（省渲染）或主线程丢弃。
        # int 赋值受 GIL 保护，跨线程读写无需额外同步；锁仅保证「取号 +
        # 入队」的原子性，使 id 单调与队列顺序一致。
        self.magnified_pages_queue = queue.Queue()
        self._magnifier_request_lock = threading.Lock()
        self._magnifier_latest_request_id = 0

        # 惰性启动：后台渲染线程与 50ms 轮询定时器推迟到首次 activate() 才创建。
        # 原实现在 __init__ 立即启动，导致每新建一个 LaTeX 文档（即便尚无 PDF、
        # 预览不可见）就常驻一个线程 + 一个 50ms 定时器，且文档关闭后不释放。
        self._render_thread_started = False
        self._rendered_pages_timeout_id = None
        # 渲染线程句柄。shutdown 时通过 join 等待线程退出，否则线程闭包
        # render_page_loop（绑定方法）持有 self → preview → document 引用链，
        # 整个文档对象图（含 GtkSource.Buffer、parser 符号表等）无法被 GC，
        # 长会话反复开/关文档导致内存随会话时长线性增长。详见
        # perf-10 问题 1 / perf-12 问题 1。
        self._render_thread = None
        self._shutting_down = False

    def on_layout_or_position_changed(self, notifying_object):
        if self.preview.layout != None:
            self.update_rendered_pages()
        else:
            self.rendered_pages = dict()
            # layout 为 None 通常意味着 PDF 切换（preview.load_pdf 置 layout=None
            # 并发 layout_changed）。清空 page_render_count，避免旧 PDF 的高页号
            # 条目残留（perf-12 问题 3）：若新 PDF 页数少于旧 PDF，多出的高页号
            # 渲染计数会永久残留，且 render_page_loop 可能 KeyError（todo 入队
            # 后 poppler_document 被替换，render_count 字典中已无该页号）。
            with self.page_render_count_lock:
                self.page_render_count = dict()

    def on_recolor_pdf_changed(self, preview):
        self.update_rendered_pages()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'color_scheme':
            self.update_rendered_pages()

    def activate(self):
        with self.is_active_lock:
            self.is_active = True
        self._ensure_loops_started()
        self.update_rendered_pages()

    def _ensure_loops_started(self):
        # 首次激活时才启动后台线程与轮询定时器，避免对从不显示预览的文档
        # （如新建未保存的空文档）也常驻线程/定时器。
        if not self._render_thread_started:
            self._render_thread_started = True
            self._render_thread = threading.Thread(target=self.render_page_loop, daemon=True)
            self._render_thread.start()
        if self._rendered_pages_timeout_id is None:
            self._rendered_pages_timeout_id = GObject.timeout_add(50, self.rendered_pages_loop)

    def invalidate_magnifier_requests(self):
        '''Discard active and queued lens work after a preview context change.'''
        with self._magnifier_request_lock:
            self._magnifier_latest_request_id += 1
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
        self.pdf_date = None
        # Preview hiding invalidates all in-flight lens surfaces as well.
        self.invalidate_magnifier_requests()

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用：移除轮询定时器、
        置 is_active=False、唤醒并 join 后台渲染线程、断开 settings 单例信号。

        关键点（perf-10 问题 1 / perf-12 问题 1）：原实现仅置 is_active=False，
        render_page_loop 检测到后进入 time.sleep(0.05) 永久空转循环，**从不退出**。
        因线程入口是绑定方法 self.render_page_loop，闭包持 self → preview →
        document 整条引用链，文档对象（含 GtkSource.Buffer 全文文本、parser
        符号表、Gtk widget 树）无法被 GC，长会话内存随开/关文档数线性增长。

        修复：用 _shutting_down 标志让循环退出，向 render_queue 投递哨兵 None
        唤醒阻塞在 get(timeout=0.05) 上的线程（避免最长 50ms 等待），再 join(2s)
        确保线程真的退出后才返回。daemon=True 是兜底，防止极端情况下死锁。'''
        self._shutting_down = True
        if self._rendered_pages_timeout_id is not None:
            GLib.Source.remove(self._rendered_pages_timeout_id)
            self._rendered_pages_timeout_id = None
        with self.is_active_lock:
            self.is_active = False
        self.rendered_pages = dict()
        with self.visible_pages_lock:
            self.visible_pages = list()
        # 清空渲染计数表，丢弃所有积压 todo（perf-12 问题 3：原 shutdown 不清，
        # PDF 切换后旧 PDF 高页号条目残留）。
        with self.page_render_count_lock:
            self.page_render_count = dict()
        # 清空队列，避免 join 期间线程继续处理已失效的 todo（poppler_document
        # 已可能被释放）。投递哨兵 None 唤醒阻塞 get。
        try:
            while True:
                self.render_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self.render_queue_low_priority.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self.magnified_pages_queue.get_nowait()
        except queue.Empty:
            pass
        self.render_queue.put(None)
        if self._render_thread is not None and self._render_thread.is_alive():
            self._render_thread.join(timeout=2.0)
            if self._render_thread.is_alive():
                print('Warning: PreviewPageRenderer render thread did not exit within 2s')
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
                # 阻塞式 get + 50ms timeout 替代原「非阻塞 get + time.sleep(0.05)」：
                # 原实现在队列空时每 50ms 轮询一次，高优先级渲染任务入队后最多
                # 等 50ms 才被取走；改用阻塞 get 后任务入队即唤醒线程，延迟趋近 0。
                # timeout=0.05 保证 is_active 变 False 时能在 50ms 内检测到并退出。
                # shutdown 时投递哨兵 None 唤醒阻塞 get，让线程即时退出而非
                # 等满 50ms。
                try: todo = self.render_queue.get(block=True, timeout=0.05)
                except queue.Empty:
                    try: todo = self.render_queue_low_priority.get(block=False)
                    except queue.Empty:
                        todo = None
            else:
                time.sleep(0.05)
            # 哨兵 None（shutdown 投递）或 deactivate 期间积压的 None：跳过。
            if todo is None:
                continue
            # 放大镜任务走独立的过期判定（request_id）与结果投递路径，
            # 不读 page_render_count / visible_pages（那些键不存在）。
            if todo.get('kind') == 'magnifier':
                self._process_magnifier_todo(todo)
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
                colors = todo['matching_theme_colors']
                width = todo['page_width'] * todo['hidpi_factor']
                height = todo['page_height'] * todo['hidpi_factor']
                surface = cairo.ImageSurface(cairo.Format.ARGB32, width, height)
                ctx = cairo.Context(surface)

                ctx.set_source_rgba(1, 1, 1, 1)
                ctx.rectangle(0, 0, width, height)
                ctx.fill()

                ctx.scale(todo['scale_factor'] * todo['hidpi_factor'], todo['scale_factor'] * todo['hidpi_factor'])
                page = self.preview.poppler_document.get_page(todo['page_number'])
                page.render(ctx)

                if colors != None:
                    # 直接从 cairo surface 取数据到 numpy 做 alpha 提取 +
                    # Operator.IN 着色。原实现 4 次内存拷贝（12MB/页），现
                    # 2 次（np.frombuffer + .copy() → bytearray）。正确性：
                    # FORMAT_ARGB32 小端字节序为 BGRA，alpha 公式沿用原实现
                    # 的 B/G/R 加权和，最终视觉结果与旧 PIL 路径一致。
                    surface = self._apply_theme_recolor(surface, colors)

                self.rendered_pages_queue.put({'page_number': todo['page_number'], 'item': [surface, todo['page_width'], todo['page_height'], todo['pdf_date'], colors]})

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

    # ---- 放大镜局部渲染 ----

    def request_magnifier_render(self, page_number, center_x_pt, center_y_pt,
                                 out_css_size, hidpi_factor, density, rotation, colors):
        '''入队一个放大镜局部渲染任务（主线程调用），返回请求 id。

        任务放 render_queue 高优先级队列——放大镜必须跟手，不能排在整页
        渲染之后。返回的 request_id 由调用方保存，用于在结果到达时比对；
        过期任务在渲染线程内即被丢弃（见 _process_magnifier_todo）。

        参数:
            page_number: 0-based 页号
            center_x_pt / center_y_pt: 裁剪中心（页面内 top-down PDF 点坐标，
                与布局映射 get_page_number_and_offsets_by_document_offsets
                的返回值同约定）
            out_css_size: 输出方形浮窗边长（css px）
            hidpi_factor: 设备像素比
            density: 渲染密度（设备 px / PDF 点，= factor × scale × hidpi）
            rotation: 预览旋转角（0/90/180/270）
            colors: 反色主题色元组或 None
        '''
        if self.preview.poppler_document == None:
            return None
        with self._magnifier_request_lock:
            self._magnifier_latest_request_id += 1
            request_id = self._magnifier_latest_request_id
            task = {
                'kind': 'magnifier',
                'request_id': request_id,
                'page_number': int(page_number),
                'center_x_pt': float(center_x_pt),
                'center_y_pt': float(center_y_pt),
                'out_css_size': max(1, int(out_css_size)),
                'hidpi_factor': hidpi_factor,
                'density': float(density),
                'rotation': rotation,
                'matching_theme_colors': colors,
            }
            self.render_queue.put(task)
        return request_id

    def _process_magnifier_todo(self, todo):
        '''渲染线程内处理一个放大镜任务：过期即弃，否则局部渲染后投递到
        magnified_pages_queue 并通过 idle 回调通知主线程。'''
        with self._magnifier_request_lock:
            if todo['request_id'] != self._magnifier_latest_request_id:
                return

        try:
            page = self.preview.poppler_document.get_page(todo['page_number'])
        except Exception:
            # shutdown / PDF 切换窗口期文档可能已被替换或释放：静默丢弃。
            return

        size_px = todo['out_css_size'] * todo['hidpi_factor']
        surface = cairo.ImageSurface(cairo.Format.ARGB32, int(size_px), int(size_px))
        ctx = cairo.Context(surface)

        density = todo['density']
        half_pt = size_px / (2 * density)
        cx = todo['center_x_pt']
        cy = todo['center_y_pt']

        # 变换链见 apply_magnifier_transform 文档：裁剪中心对到 surface
        # 中心、密度为整页渲染的 factor 倍；page.render 内部自带的 y 翻转
        # 负责把 PDF 原生 y-up 内容落进与布局映射一致的 top-down 空间，
        # 这里绝不能再手动翻（双重翻转 = 镜像）。旋转仅 90° 倍数，轴对齐
        # 裁剪区经旋转仍轴对齐，top-down 白底矩形可精确覆盖可见区域。
        apply_magnifier_transform(ctx, size_px, density, todo['rotation'], cx, cy)

        ctx.set_source_rgba(1, 1, 1, 1)
        ctx.rectangle(cx - half_pt, cy - half_pt, 2 * half_pt, 2 * half_pt)
        ctx.fill()

        page.render(ctx)

        colors = todo['matching_theme_colors']
        if colors != None:
            surface = self._apply_theme_recolor(surface, colors)

        self.magnified_pages_queue.put({
            'kind': 'magnifier',
            'request_id': todo['request_id'],
            'surface': surface,
        })
        # 跨线程唤醒主线程消费结果（g_idle_add_full 线程安全）。一次性回调：
        # 返回 False 自毁；shutdown 后置守卫避免触碰已失效的信号连接。
        GObject.idle_add(self.on_magnifier_result_produced)

    def on_magnifier_result_produced(self):
        '''主线程回调：发 change code 让 controller 消费 magnified_pages_queue。'''
        if self._shutting_down:
            return False
        self.add_change_code('magnifier_result_ready')
        return False

    def rendered_pages_loop(self):
        with self.is_active_lock:
            is_active = self.is_active
        if not is_active: return True

        changed = False
        while self.rendered_pages_queue.empty() == False:
            try: todo = self.rendered_pages_queue.get(block=False)
            except queue.Empty: pass
            else:
                try:
                    del(self.rendered_pages[todo['page_number']])
                except KeyError: pass
                self.rendered_pages[todo['page_number']] = todo['item']
                changed = True
        if changed:
            self.add_change_code('rendered_pages_changed')
        return True

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

        pdf_date = self.preview.get_pdf_date()
        with self.visible_pages_lock:
            self.visible_pages = visible_pages
            self.visible_pages_additional = visible_pages_additional
        self.page_width = page_width
        self.pdf_date = pdf_date

        if self.preview.recolor_pdf:
            colors = (ColorManager.get_ui_color('view_fg_color'), ColorManager.get_ui_color('view_bg_color'))
        else:
            colors = None

        changed = False
        # colors_changed 判定：stored[3] 与 colors 同为 None → 不变；
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

            if page_data[3] != pdf_date or colors_changed or page_number < visible_pages_additional[0] or page_number > visible_pages_additional[1]:
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
        for page_number in range(lo, hi + 1):
            page_data = rendered_pages.get(page_number)
            if page_data is None or page_data[1] != page_width or page_data[2] != pdf_date:
                with self.page_render_count_lock:
                    try:
                        page_render_count[page_number] += 1
                    except KeyError:
                        page_render_count[page_number] = 1

                    render_task = dict()
                    render_task['page_number'] = page_number
                    render_task['render_count'] = page_render_count[page_number]
                    render_task['scale_factor'] = scale_factor
                    render_task['hidpi_factor'] = hidpi_factor
                    render_task['page_width'] = page_width
                    render_task['page_height'] = page_height
                    render_task['pdf_date'] = pdf_date
                    render_task['matching_theme_colors'] = colors

                    if page_number >= vp_lo and page_number <= vp_hi:
                        render_queue.put(render_task)
                    else:
                        render_queue_low_priority.put(render_task)

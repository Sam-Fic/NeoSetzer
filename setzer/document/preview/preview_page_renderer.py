#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

import _thread as thread, queue
import time
import math
import numpy as np

from setzer.app.color_manager import ColorManager
from setzer.helpers.observable import Observable


class PreviewPageRenderer(Observable):

    def __init__(self, preview):
        Observable.__init__(self)
        self.preview = preview
        self.maximum_rendered_pixels = 20000000

        self.visible_pages_lock = thread.allocate_lock()
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
        self.is_active_lock = thread.allocate_lock()
        self.is_active = False

        self.preview.connect('position_changed', self.on_layout_or_position_changed)
        self.preview.connect('layout_changed', self.on_layout_or_position_changed)
        self.preview.connect('recolor_pdf_changed', self.on_recolor_pdf_changed)
        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.preview.document.settings.connect('settings_changed', self._settings_callback)

        self.page_render_count_lock = thread.allocate_lock()
        self.page_render_count = dict()
        self.render_queue = queue.Queue()
        self.render_queue_low_priority = queue.Queue()
        self.rendered_pages_queue = queue.Queue()

        # 惰性启动：后台渲染线程与 50ms 轮询定时器推迟到首次 activate() 才创建。
        # 原实现在 __init__ 立即启动，导致每新建一个 LaTeX 文档（即便尚无 PDF、
        # 预览不可见）就常驻一个线程 + 一个 50ms 定时器，且文档关闭后不释放。
        self._render_thread_started = False
        self._rendered_pages_timeout_id = None
        self._shutting_down = False

    def on_layout_or_position_changed(self, notifying_object):
        if self.preview.layout != None:
            self.update_rendered_pages()
        else:
            self.rendered_pages = dict()

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
            thread.start_new_thread(self.render_page_loop, ())
        if self._rendered_pages_timeout_id is None:
            self._rendered_pages_timeout_id = GObject.timeout_add(50, self.rendered_pages_loop)

    def deactivate(self):
        with self.is_active_lock:
            self.is_active = False
        self.rendered_pages = dict()
        with self.visible_pages_lock:
            self.visible_pages = list()
        self.page_width = None
        self.pdf_date = None

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用：移除轮询定时器并
        置 is_active=False。后台线程检测到 is_active=False 后会进入
        time.sleep(0.05) 空转，不再占 CPU；其随进程退出自然结束。
        同时断开 settings 单例信号连接，防止持有引用导致文档无法 GC。'''
        self._shutting_down = True
        if self._rendered_pages_timeout_id is not None:
            GLib.Source.remove(self._rendered_pages_timeout_id)
            self._rendered_pages_timeout_id = None
        with self.is_active_lock:
            self.is_active = False
        self.rendered_pages = dict()
        with self.visible_pages_lock:
            self.visible_pages = list()
        try:
            self.preview.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass

    def render_page_loop(self):
        while True:
            with self.is_active_lock:
                is_active = self.is_active
            todo = None
            if is_active:
                # 阻塞式 get + 50ms timeout 替代原「非阻塞 get + time.sleep(0.05)」：
                # 原实现在队列空时每 50ms 轮询一次，高优先级渲染任务入队后最多
                # 等 50ms 才被取走；改用阻塞 get 后任务入队即唤醒线程，延迟趋近 0。
                # timeout=0.05 保证 is_active 变 False 时能在 50ms 内检测到并退出。
                try: todo = self.render_queue.get(block=True, timeout=0.05)
                except queue.Empty:
                    try: todo = self.render_queue_low_priority.get(block=False)
                    except queue.Empty:
                        todo = None
            else:
                time.sleep(0.05)
            if todo != None:
                with self.page_render_count_lock:
                    render_count = self.page_render_count[todo['page_number']]
                with self.visible_pages_lock:
                    is_visible = (todo['page_number'] >= self.visible_pages_additional[0] and todo['page_number'] <= self.visible_pages_additional[1])
                if todo['render_count'] == render_count and is_visible:
                    colors = todo['matching_theme_colors']
                    width = todo['page_width'] * todo['hidpi_factor']
                    height = todo['page_height'] * 2
                    surface = cairo.ImageSurface(cairo.Format.ARGB32, width, height)
                    ctx = cairo.Context(surface)

                    ctx.set_source_rgba(1, 1, 1, 1)
                    ctx.rectangle(0, 0, width, height)
                    ctx.fill()

                    ctx.scale(todo['scale_factor'] * todo['hidpi_factor'], todo['scale_factor'] * todo['hidpi_factor'])
                    page = self.preview.poppler_document.get_page(todo['page_number'])
                    page.render(ctx)

                    if colors != None:
                        # 直接从 cairo surface 取数据到 numpy，跳过 PIL Image 中转。
                        # 原实现 4 次内存拷贝（12MB/页）：
                        #   1. np.array(pil_img)：PIL → numpy（拷贝像素）
                        #   2. np.ubyte(img_data)：numpy → 新数组（Image.fromarray 需要）
                        #   3. pil_img.tobytes('raw', 'BGRa')：numpy → BGRa 字节（拷贝+重排）
                        #   4. bytearray(...)：bytes → bytearray（create_for_data 需可变）
                        # 优化后 2 次：np.frombuffer + .copy()（修改 alpha 需可写）→
                        # bytearray(tobytes)。半内存、半 CPU 开销。
                        #
                        # 正确性：cairo FORMAT_ARGB32 在小端机器上字节序为 BGRA
                        # （byte0=B, byte1=G, byte2=R, byte3=A）。原 PIL 路径用
                        # Image.frombuffer("RGBA",...) 误把 B 当 R、R 当 B，但
                        # 后续 cairo.Operator.IN 用 colors[0] 覆盖全部 RGB 像素，
                        # 故中间 RGB 内容不影响最终视觉结果——只有 alpha 值重要。
                        # 此处保持与原实现相同的 alpha 公式（用 byte0/1/2 即
                        # B/G/R 通道），最终 alpha 值与原实现完全一致。
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

                    self.rendered_pages_queue.put({'page_number': todo['page_number'], 'item': [surface, todo['page_width'], todo['pdf_date'], colors]})

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
        page_width = int(self.preview.layout.page_width)
        page_height = int(self.preview.layout.page_height)

        offset = self.preview.view.content.scrolling_offset_y
        current_page = self.preview.layout.get_page_by_offset(offset) - 1

        visible_pages = [current_page, min(current_page + math.floor(self.preview.view.get_allocated_height() / page_height) + 1, self.preview.poppler_document.get_n_pages() - 1)]

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
            stored_colors = page_data[3]
            if stored_colors is None or colors is None:
                colors_changed = (stored_colors is not colors)
            elif not stored_colors[0].equal(colors[0]) or not stored_colors[1].equal(colors[1]):
                colors_changed = True
            else:
                colors_changed = False

            if page_data[2] != pdf_date or colors_changed or page_number < visible_pages_additional[0] or page_number > visible_pages_additional[1]:
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



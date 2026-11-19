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
gi.require_version('Poppler', '0.18')
gi.require_version('Gtk', '4.0')
from gi.repository import Poppler
from gi.repository import GLib
from gi.repository import Gio
from gi.repository import Gdk

import os.path
import math
import threading
import unicodedata

import cairo

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.sync_debug import sync_debug as _sync_debug
from setzer.helpers.synctex_line_repair import has_matchable_text, match_pdf_line
from setzer.project.build_configuration import ProjectBuildConfiguration

import setzer.document.preview.preview_viewgtk as preview_view
import setzer.document.preview.preview_layouter as preview_layouter
import setzer.document.preview.preview_presenter as preview_presenter
import setzer.document.preview.preview_controller as preview_controller
import setzer.document.preview.preview_page_renderer as preview_page_renderer
import setzer.document.preview.preview_links_parser as preview_links_parser
import setzer.document.preview.preview_zoom_manager as preview_zoom_manager
import setzer.document.preview.context_menu.context_menu as context_menu
from setzer.helpers.observable import Observable
from setzer.document.preview.external_pdf_monitor import (
    ExternalPdfChangeTracker,
    ExternalPdfState,
)

# 选区高亮墨迹测量的栅格分辨率（points → 像素）：2x 足以分辨降部
# 字母的 ~2pt descent 空间；每页只渲染一次，缓存复用。
_TEXT_INK_SCALE = 2.0
# 墨迹判定阈值：任一通道低于此值即算墨迹（覆盖彩色文本）。
_TEXT_INK_THRESHOLD = 200
# 栅格缓存页数上限（A4@2x 约 8MB/页，只保留最近选中的几页）。
_TEXT_INK_RASTER_MAX_PAGES = 4


class Preview(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document

        self.pdf_filename = None
        # 外部编译器可能原子替换同一路径的 PDF。目录监控与状态协调器
        # 仅在成功加载过一个版本后才提示，避免初次生成 PDF 时出现误报。
        self._external_pdf_tracker = ExternalPdfChangeTracker()
        self._external_pdf_monitor = None
        self._external_pdf_monitor_directory = None
        self._external_pdf_debounce_id = None
        self._external_pdf_state = ExternalPdfState.CURRENT
        self._external_pdf_debounce_ms = 400
        # 构建期间抑制外部 PDF 监控：编译器会向同一 PDF 路径增量写入，此时
        # 的监控事件属于本次构建自身而非外部变更。构建结束（idle）后由
        # parse_result → load_pdf 接受新版本，监控恢复正常响应，从而保证
        # 编译全程预览区一直显示上一次编译好的 PDF。
        self._suppress_monitor_for_build = False
        # 构建失败但保留旧 PDF 时为 True：预览面板据此显示「构建失败，显示的是
        # 上一次成功的 PDF」横幅，避免用户误以为构建成功。由 build_system 置 True，
        # set_pdf_filename / reset_pdf_data 置 False。
        self.pdf_is_stale = False
        self.recolor_pdf = self.document.settings.get_value('preferences', 'recolor_pdf')
        # 放大镜开关（预览工具栏切换按钮，preferences/use_magnifier）。
        self.use_magnifier = self.document.settings.get_value('preferences', 'use_magnifier')

        # ---- 文字选择（普通光标模式，放大镜关闭时左键拖动框选）----
        # text_selection: {'anchor': (cx,cy), 'head': (cx,cy)}，画布坐标。
        # regions: page → 精修后的高亮矩形列表（未旋转 points，top-down，
        # 浮点字形框），供 presenter 按 scale_factor 缩放后精确绘制。
        # rects: page → Poppler.Rectangle（未旋转 points，top-down），供
        # 松开后提取文本。text: 完成后缓存，Ctrl+C 再复制到剪贴板。
        self.text_selection = None
        self.text_selection_dragging = False
        self.text_selection_regions = dict()
        self.text_selection_rects = dict()
        self.text_selection_text = None
        # get_text_layout() 字形框缓存：page → 浮点矩形列表（points，top-down）。
        # 拖动选择时每个 motion 事件都要精修高亮矩形，整页字形列表开销不小，
        # 以页为粒度缓存；load_pdf 换文档时整体清空。
        self._text_layout_cache = dict()
        # 高亮墨迹精修缓存：字形框是行高盒（ascent+descent），无降部字母
        # 时底边比实际墨迹低 ~2pt，高亮条看起来偏下。用低分辨率栅格的
        # 逐列墨迹轮廓把选中字形框收缩到实际墨迹范围。raster 每页一次，
        # profile 每字形行一次；load_pdf 换文档时整体清空。
        self._text_ink_raster_cache = dict()
        self._text_ink_profile_cache = dict()

        # ---- 块选择（无拖动单击吸附：整图 / 公式 / 浮动体整块）----
        # block_selection: {'page', 'rect': (x1,y1,x2,y2) 未旋转 points
        # top-down, 'kind': 'image'|'block'}；与 text_selection 互斥。
        self.block_selection = None
        self._block_cache = dict()
        self._block_request_id = 0

        self.poppler_document = None
        # 内存 PDF 文档版本号：仅在成功加载新 Poppler 文档时递增。页面渲染
        # 缓存（PreviewPageRenderer）用它做失效判断——编译期间磁盘上的 PDF
        # 被编译器改写（mtime 变化），但内存文档不变，缓存必须保持有效，
        # 预览才能在编译全程稳定显示上一次编译好的 PDF。get_pdf_date() 的
        # 磁盘 mtime 只用于会话恢复持久化，不能作为渲染缓存版本。
        self.pdf_version = 0
        self.page_width = None
        self.page_height = None
        self.page_sizes = []
        self.page_heights = None
        self.layout = None

        self.rotation = 0
        self._search_query = ''
        self._search_page = 0

        self.visible_synctex_rectangles = list()
        self.visible_synctex_rectangles_time = None

        # ---- AI 编辑 diff（PhiTeX session compare）----
        # diff_result: 最近一次 compare 的结果 dict {'rectangles': [...],
        # 'changes': [...], 'pdf': diff PDF 绝对路径, 'pages': N,
        # 'change_count': N}。矩形坐标属于 diff PDF（latexdiff 标记文档），
        # 只在 diff 视图里绘制——正式 PDF 的页面上没有对应物（插入会把后续
        # 内容推移到别的页），这是画在 diff 视图而非正式 PDF 的根本原因。
        self.diff_result = None
        # True = 预览正以覆盖方式显示 diff PDF。不动 pdf_filename / 外部监控 /
        # 构建状态：构建管道对"预览正在看什么"无感知，新构建结果到达即自动
        # 回到正式 PDF。
        self.showing_diff_pdf = False
        # 当前生效的 diff 矩形（dict 列表，synctex 惯例 + kind）：仅 diff
        # 视图非空。layouter 重建布局时按它重放。
        self.diff_rectangles = list()
        # 导航器所指的改动下标（0-based，对应 diff_rectangles；None = 尚未
        # 定位）。进入 diff 视图落第一处，点击高亮会同步到被点的那处。
        self.diff_change_index = None
        # 进入 diff 视图时布局可能还没建好（页尺寸变了会作废 layout）：
        # 记下待办，布局建立后补一次定位。
        self._pending_diff_jump = False

        self.view = preview_view.PreviewView()
        self.layouter = preview_layouter.PreviewLayouter(self, self.view)
        self.zoom_manager = preview_zoom_manager.PreviewZoomManager(self, self.view)
        self.page_renderer = preview_page_renderer.PreviewPageRenderer(self)
        self.controller = preview_controller.PreviewController(self, self.view)
        self.links_parser = preview_links_parser.PreviewLinksParser(self)
        self.presenter = preview_presenter.PreviewPresenter(self, self.page_renderer, self.view)
        self.context_menu = context_menu.ContextMenu(self, self.view)

        self.document.connect('filename_change', self.on_filename_change)
        self.document.connect('pdf_updated', self.on_pdf_updated)
        self.document.build_system.connect('build_state_change', self.on_build_state_change)

        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.document.settings.connect('settings_changed', self._settings_callback)

        # 自举：Preview 可能是在文档已有文件名之后才构造的（见方法注释）。
        self._initialize_pdf_from_filename()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.recolor_pdf = value
            self.add_change_code('recolor_pdf_changed')
            self.view.drawing_area.queue_draw()
        elif item == 'use_magnifier':
            self.use_magnifier = value
            # 关闭时取消激活中的放大镜、恢复普通光标；开启时刷新悬停光标。
            self.controller.on_magnifier_setting_changed()

    def on_filename_change(self, document, filename=None):
        if filename != None:
            pdf_filename = self._existing_pdf_for_source(filename)
            if pdf_filename != None:
                self.set_pdf_filename(pdf_filename)
        self.load_pdf()

    @staticmethod
    def _existing_pdf_for_source(filename):
        '''源码对应的、磁盘上确实存在的构建产物路径；没有返回 None。

        首选 `<tex 名字>.pdf` 旁边（默认输出位置）。项目在
        `.neosetzer/build.json` 里配了自定义输出目录时，产物不在 .tex 旁边，
        只在输出目录里（与构建端 BuilderBuild.get_output_filename 同一规则）
        ——不查这一步，带输出目录的项目每次重新打开文档预览都是空白页，哪怕
        上一轮编译的 PDF 还在。
        '''
        pdf_filename = os.path.splitext(filename)[0] + '.pdf'
        if os.path.exists(pdf_filename):
            return pdf_filename
        try:
            configuration = ProjectBuildConfiguration.discover(filename)
            if configuration is None:
                return None
            output_directory = configuration.effective_path(
                configuration.load().get('output_directory'))
        except Exception:
            # 配置损坏 / 不可读只是少了一个候选位置，不该影响预览。
            return None
        if not output_directory:
            return None
        candidate = os.path.join(
            output_directory,
            os.path.splitext(os.path.basename(filename))[0] + '.pdf')
        return candidate if os.path.exists(candidate) else None

    def on_pdf_updated(self, document):
        # 新构建产出 → diff 视图的整页内容已被替换，旧的 diff 结果不再对应
        # 眼前的 PDF：先落回正式 PDF 再加载。若这次构建属于新一轮 AI 编辑，
        # AiDiff 随后会送来新的 diff_result，横幅再亮起来。
        was_showing_diff = self.showing_diff_pdf
        self.showing_diff_pdf = False
        self.diff_rectangles = list()
        self.load_pdf()
        if was_showing_diff:
            # 通知面板把档位从 diff 拨回 pdf：这次构建不一定带来新 diff
            # （用户手动 F5 也会走这里），档位不能悬在"亮着但内容是正式 PDF"。
            self.add_change_code('diff_state_changed')

    def _initialize_pdf_from_filename(self):
        '''构造期按文档当前文件名自举一次 PDF 加载。

        正常打开流程里 Preview 早于 set_filename 构造（Document() 之后才
        set_filename），加载靠随后的 filename_change 信号；但以下路径反过来
        ——文件名先就位、Preview 后补挂：

        * 会话恢复的非活跃文档：create_document_from_filename 先 set_filename
          且 attach_preview=False，等用户切到该标签页时才由
          _attach_latex_toolchain 构造 Preview。那次 filename_change 早已
          发过，没人再叫它读盘。
        * 同理，apply_pending_latex_state 里 save_date 守卫命中（.tex 比
          状态新）时整段预览状态恢复被跳过，路径更不会被填。

        结果就是：PDF 明明躺在磁盘上（以前编译过），切到该标签页预览区却是
        "No preview available"。这里等价地再走一次 on_filename_change，
        有现成 PDF 就显示，没有才留空白页。
        '''
        filename = self.document.get_filename()
        if filename is not None:
            self.on_filename_change(self.document, filename)

    def set_pdf_filename(self, pdf_filename):
        if pdf_filename != self.pdf_filename:
            self.pdf_filename = pdf_filename
        # 仅在路径实际改变时替换目录 monitor；同一路径的内部构建要等
        # Poppler 成功打开新版本后再更新已接受签名，不能提前吞掉外部变更。
        if self._external_pdf_tracker.set_pdf_filename(pdf_filename):
            self._stop_external_pdf_monitor()
            self._set_external_pdf_state(ExternalPdfState.CURRENT)
        # 新 PDF 产出（构建成功或文档打开时发现已有 PDF）：清除 stale 标记。
        # 即使 filename 相同（重建同一文件），只要构建产出了新 PDF 就不算 stale。
        if self.pdf_is_stale:
            self.set_pdf_is_stale(False)

    def set_pdf_is_stale(self, stale):
        '''标记预览显示的 PDF 是否来自之前成功的构建（当前构建失败未产出 PDF）。

        build_system.parse_result 在构建未产出 PDF 但旧 PDF 仍显示时置 True；
        set_pdf_filename（新 PDF 产出）与 reset_pdf_data（无 PDF）置 False。
        预览面板据此显示/隐藏「构建失败，显示的是上一次成功的 PDF」横幅。
        仅在状态变化时发 change_code，避免无谓的通知。
        '''
        if self.pdf_is_stale != stale:
            self.pdf_is_stale = stale
            self.add_change_code('pdf_stale_changed')

    def _set_external_pdf_state(self, state):
        '''Publish persistent external-PDF state only when it actually changes.'''

        if self._external_pdf_state != state:
            self._external_pdf_state = state
            self.add_change_code('external_pdf_state_changed', state)

    def _clear_external_pdf_debounce(self):
        if self._external_pdf_debounce_id is not None:
            try:
                GLib.source_remove(self._external_pdf_debounce_id)
            except (TypeError, ValueError):
                pass
            self._external_pdf_debounce_id = None

    def _stop_external_pdf_monitor(self):
        self._clear_external_pdf_debounce()
        if self._external_pdf_monitor is not None:
            try:
                self._external_pdf_monitor.cancel()
            except (AttributeError, TypeError):
                pass
        self._external_pdf_monitor = None
        self._external_pdf_monitor_directory = None

    def _ensure_external_pdf_monitor(self):
        '''Monitor the parent directory so atomic PDF replacement is observed.'''

        directory = self._external_pdf_tracker.directory
        if directory is None or not os.path.isdir(directory):
            return
        if self._external_pdf_monitor is not None and self._external_pdf_monitor_directory == directory:
            return
        self._stop_external_pdf_monitor()
        try:
            directory_file = Gio.File.new_for_path(directory)
            self._external_pdf_monitor = directory_file.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._external_pdf_monitor.connect('changed', self._on_external_pdf_file_changed)
            self._external_pdf_monitor_directory = directory
        except Exception:
            # File monitoring is a best-effort enhancement. Some sandboxes and
            # virtual filesystems do not support it; retain normal preview use.
            self._external_pdf_monitor = None
            self._external_pdf_monitor_directory = None

    def _pdf_is_owned_by_live_build(self):
        '''本文档的 PDF 是否由 PhiTeX 常驻 session 持续重写。

        常驻 watch 每轮增量构建都会经 --exact 把整份 PDF 重新落盘，会话自己
        也还有补齐性的写盘。也就是说在这份文件上，唯一的写者就是我们的构建
        链——目录监控看到的每一次变化都是自己的产物，报成"外部更改"是假警报，
        而点 Reload 只是与正在跑的 watch 抢同一份文件。

        实测触发方式：抑制窗口只覆盖 building_in_progress → idle 这一段，而
        监控的 400ms 去抖是异步的，两者之间只要有一条缝（或文档因会话恢复与
        命令行各开了一次、存在第二个 Preview 在跟同一路径），横幅就会在打字
        过程中反复弹合——正是"PhiTeX 频繁编译把自家写盘误判成外部更改"。
        '''
        document = getattr(self, 'document', None)
        build_system = getattr(document, 'build_system', None)
        if build_system is None:
            return False
        try:
            return bool(build_system.is_phitex_live_build())
        except Exception:
            return False

    def _absorb_own_build_pdf_event(self):
        '''把一次"自家构建产物"的监控事件吃掉，并把已接受签名追平。

        追平这一步不能省：live 期间我们不接受磁盘上的新版本（预览只在自己的
        构建结果里加载），若签名一直停在旧值，等用户换引擎/关掉会话之后，第
        一条监控事件就会拿这个很旧的签名去比，立刻弹一次假横幅。
        '''
        self._external_pdf_tracker.accept_current_file()
        self._set_external_pdf_state(ExternalPdfState.CURRENT)

    def _on_external_pdf_file_changed(self, monitor, file, other_file, event_type):
        if not self._external_pdf_tracker.matches_event_files(file, other_file):
            return
        # 构建进行中：事件来自编译器自身的写入，忽略，保持显示旧 PDF。
        if self._suppress_monitor_for_build:
            return
        if self._pdf_is_owned_by_live_build():
            self._absorb_own_build_pdf_event()
            return
        self._clear_external_pdf_debounce()
        self._external_pdf_debounce_id = GLib.timeout_add(
            self._external_pdf_debounce_ms, self._on_external_pdf_debounced)

    def _on_external_pdf_debounced(self):
        self._external_pdf_debounce_id = None
        # 构建开始瞬间仍可能挂着未触发的 debounce（build_state_change 已清除，
        # 此处是兜底），同样丢弃。
        if self._suppress_monitor_for_build:
            return False
        # live 模式下同理：debounce 是 400ms 前挂上的，期间可能已经从"非 live"
        # 切了过来，或者那时抑制还没生效。
        if self._pdf_is_owned_by_live_build():
            self._absorb_own_build_pdf_event()
            return False
        state = self._external_pdf_tracker.inspect_disk_change()
        self._set_external_pdf_state(state)
        return False

    def on_build_state_change(self, build_system, state):
        '''构建开始时挂起外部 PDF 监控，结束后恢复。

        编译器写 PDF 的过程会被目录监控误判为"外部修改"，导致编译中途弹横幅、
        甚至把写了一半的 PDF 重载进预览。挂起后编译全程预览区稳定显示上一次
        的 PDF，构建结束时 parse_result → load_pdf 加载并接受新版本，监控在
        idle 后恢复对外部修改的正常响应。building_to_stop 期间 worker 仍在
        退出，保持抑制直到 idle。'''
        if state == 'building_in_progress':
            self._suppress_monitor_for_build = True
            self._clear_external_pdf_debounce()
        elif state == 'idle':
            self._suppress_monitor_for_build = False

    def reload_external_pdf(self):
        '''Safely reload a PDF after the user accepts the persistent banner.'''

        if self.pdf_filename is None:
            return False
        # 构建进行中不重载：磁盘上的 PDF 正在被编译器改写，保持显示上一版，
        # 待构建结束由 parse_result 统一加载新 PDF。
        if self._suppress_monitor_for_build:
            return False
        return self.load_pdf(external_reload=True)

    # ---- 文字选择（普通光标模式，放大镜关闭时左键拖动框选）----

    def begin_text_selection(self, doc_x, doc_y):
        '''普通光标模式下左键按下：以 (doc_x, doc_y) 为锚点开始框选。

        替换上一个（仍显示中的）选择。区域立即重算（首帧只有锚点，
        通常得到空区域）。'''
        self.text_selection = {'anchor': (doc_x, doc_y), 'head': (doc_x, doc_y)}
        self.text_selection_dragging = True
        self.text_selection_text = None
        self.block_selection = None
        self._recompute_text_selection_regions()

    def update_text_selection(self, doc_x, doc_y):
        '''拖动中更新 head 并重算各页高亮区域。仅拖动期间有效。'''
        if not self.text_selection_dragging or self.text_selection is None:
            return
        self.text_selection['head'] = (doc_x, doc_y)
        self._recompute_text_selection_regions()

    def finish_text_selection(self):
        '''松开左键：提取选中文本，写入 X11/Wayland 主选择（primary）。

        选中高亮保留在屏幕上（与 Evince 一致），文本缓存在
        text_selection_text 供 Ctrl+C 复制到剪贴板。返回所选文本。'''
        if self.text_selection is None or not self.text_selection_dragging:
            return ''
        self.text_selection_dragging = False
        self.text_selection_text = self._extract_selected_text()
        text = self.text_selection_text
        if text:
            try:
                Gdk.Display.get_default().get_primary_clipboard().set_content(
                    Gdk.ContentProvider.new_for_value(text))
            except Exception:
                # 主选择不可用（个别后端）不影响选择本身。
                pass
        return text

    def clear_text_selection(self):
        '''清除选择与高亮（Esc、新 PDF / 布局变化时调用）。块选择一并清。'''
        if (self.text_selection is None and not self.text_selection_regions
                and self.block_selection is None):
            return
        self.text_selection = None
        self.text_selection_dragging = False
        self.text_selection_regions = dict()
        self.text_selection_rects = dict()
        self.text_selection_text = None
        self.block_selection = None
        self.view.drawing_area.queue_draw()

    def clear_block_selection(self):
        '''仅清块选择高亮。'''
        if self.block_selection is None:
            return
        self.block_selection = None
        self.view.drawing_area.queue_draw()

    def page_point_from_doc_offsets(self, doc_x, doc_y):
        '''画布坐标 → (page 0-based, x, y 未旋转 PDF points)；页隙/越界 None。'''
        if self.layout is None or self.poppler_document is None:
            return None
        window_width = self.view.get_viewport_width()
        data = self.layout.get_page_number_and_offsets_by_document_offsets(
            doc_x, doc_y, window_width)
        if data is None:
            return None
        page, x, y = data
        if page < 0 or page >= self.poppler_document.get_n_pages():
            return None
        return page, x, y

    @staticmethod
    def _image_rect_at(poppler_page, x, y):
        '''点击点命中的整图矩形（top-down points，headless 探针确认约定）。'''
        try:
            mappings = poppler_page.get_image_mapping()
        except Exception:
            return None
        for mapping in mappings or []:
            area = mapping.area
            if area.x1 <= x <= area.x2 and area.y1 <= y <= area.y2:
                return (area.x1, area.y1, area.x2, area.y2)
        return None

    def snap_block_at(self, doc_x, doc_y):
        '''无拖动单击：块吸附。整图走 get_image_mapping（同步、内存）；
        否则后台线程反查源码块 + 正向逐行并集矩形。均无命中不产生选择。'''
        hit = self.page_point_from_doc_offsets(doc_x, doc_y)
        if hit is None:
            return
        page, x, y = hit
        poppler_page = self.poppler_document.get_page(page)
        image_rect = self._image_rect_at(poppler_page, x, y)
        if image_rect is not None:
            self.block_selection = {'page': page, 'rect': image_rect, 'kind': 'image'}
            self.view.drawing_area.queue_draw()
            return
        key = (self.pdf_version, page, round(x, 1), round(y, 1))
        cached = self._block_cache.get(key)
        if cached is not None:
            self.block_selection = dict(cached, page=page)
            self.view.drawing_area.queue_draw()
            return
        tex_filename = self.document.get_filename()
        if tex_filename is None or self.pdf_filename is None:
            return
        # 影子 synctex 新鲜度门控：活动 gz 是影子产物但源码已改（指纹不
        # 匹配）时不产生块选择，避免拿陈旧定位数据高亮错块。
        build_system = getattr(self.document, 'build_system', None)
        if build_system is not None and not build_system.synctex_is_fresh():
            return
        buffer = self.document.source_buffer
        active_lines = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), True).splitlines(keepends=True)
        # 主线程预提取本页整图矩形（线程里不读 poppler），供块矩形
        # 并入整图与空间降级搜索用。
        try:
            image_rects = [(m.area.x1, m.area.y1, m.area.x2, m.area.y2)
                           for m in (poppler_page.get_image_mapping() or [])]
        except Exception:
            image_rects = []
        # 字形盒主线程预提取：表格/显示数学的 synctex 盒不可靠，
        # 块吸附视觉矩形用字形聚行连通扩展（见 pdf_annotation_mapper）。
        try:
            ok, glyphs = poppler_page.get_text_layout()
            glyph_rects = [(g.x1, g.y1, g.x2, g.y2) for g in glyphs] if ok else []
        except Exception:
            glyph_rects = []
        self._block_request_id += 1
        request_id = self._block_request_id
        pdf_filename = self.pdf_filename
        from setzer.ai_fix import pdf_annotation_mapper

        def _run():
            result = pdf_annotation_mapper.resolve_block(
                self.document, pdf_filename, page, x, y, active_lines=active_lines,
                image_rects=image_rects, glyph_rects=glyph_rects)
            GLib.idle_add(self._on_block_resolved, request_id, key, page, result)

        threading.Thread(target=_run, daemon=True).start()

    def _on_block_resolved(self, request_id, key, page, result):
        if request_id != self._block_request_id:
            return False
        if result is None:
            return False
        # 缓存完整解析结果（含源码块信息）：重复点击免反查，且批注
        # 映射直接复用与高亮框严格一致的块。kind 归一为 'block'
        # （resolve_block 返回的 'environment'/'displaymath' 是源码分类，
        # payload 契约只认 'block'/'image'/'text'）。
        result = dict(result, kind='block')
        self._block_cache[key] = result
        self.block_selection = dict(result, page=page)
        self.view.drawing_area.queue_draw()
        return False

    def get_selection_payload(self):
        '''当前选择（块优先）的纯数据 payload，供 PDF→源码映射入口使用。'''
        if self.block_selection is not None:
            block = self.block_selection
            x1, y1, x2, y2 = block['rect']
            payload = {'kind': block['kind'], 'page': block['page'],
                       'rect': block['rect'], 'center': ((x1 + x2) / 2, (y1 + y2) / 2)}
            # 块吸附已解析出源码块时随 payload 携带，映射端免二次反查。
            for field in ('filename', 'start_line', 'end_line'):
                if block.get(field) is not None:
                    payload[field] = block[field]
            return payload
        if self.text_selection is not None and self.text_selection_rects:
            return {'kind': 'text', 'rects': dict(self.text_selection_rects),
                    'text': self.text_selection_text or ''}
        return None

    def _extract_selected_text(self):
        '''按页序拼接各页选中文本。poppler 的 get_selected_text 对换行
        与词间空格已做处理，页与页之间补换行。'''
        doc = self.poppler_document
        if doc is None:
            return ''
        parts = []
        for page in sorted(self.text_selection_rects):
            text = doc.get_page(page).get_selected_text(
                Poppler.SelectionStyle.GLYPH, self.text_selection_rects[page])
            if text:
                parts.append(text)
        return '\n'.join(parts)

    def _recompute_text_selection_regions(self):
        '''按当前框选矩形重算每页的高亮区域与 poppler 矩形。

        坐标约定（poppler 26.01 无头实验确认）：get_selected_region /
        get_selected_text 的输入矩形与输出区域都是 top-down（顶左原点），
        输出区域在 device 空间（points × scale）。get_text_layout 输出
        同为 top-down points。链接 API 的 y-up 约定与此不同，勿混用。

        画布 → 页面局部：先对每页显示盒（margin..margin+page_width ×
        page_top..page_top+page_heights[i]）求交，得到显示坐标的两个角，
        再逆旋转到未旋转 css 空间、除以 scale_factor 得 points。rotation
        均为 90° 的倍数，轴对齐矩形逆旋转后仍轴对齐，取角点 min/max 即可。

        regions 存的是经 _refine_selection_region 精修的浮点字形框
        （points，绘制时 presenter 乘 scale_factor）；rects 保留 poppler
        原始输入矩形供 get_selected_text 提取文本。'''
        self.text_selection_regions = dict()
        self.text_selection_rects = dict()
        layout = self.layout
        doc = self.poppler_document
        if self.text_selection is None or layout is None or doc is None:
            return
        ax, ay = self.text_selection['anchor']
        hx, hy = self.text_selection['head']
        x_min, x_max = min(ax, hx), max(ax, hx)
        y_min, y_max = min(ay, hy), max(ay, hy)
        if x_max - x_min < 2 and y_max - y_min < 2:
            # 原地点击（无拖动）：无选择内容。
            return
        window_width = self.view.content.width
        margin = layout.get_horizontal_margin(window_width)
        scale = layout.scale_factor
        first = max(0, layout.get_page_by_offset(y_min) - 1)
        last = min(max(0, layout.get_page_by_offset(y_max) - 1), doc.get_n_pages() - 1)
        for page in range(first, last + 1):
            page_top = layout.get_page_top(page)
            page_h = layout.get_page_height(page)
            if page_top is None or page_h is None:
                continue
            iy_min = max(y_min, page_top)
            iy_max = min(y_max, page_top + page_h)
            if iy_max - iy_min < 1:
                continue
            ix_min = max(x_min, margin)
            ix_max = min(x_max, margin + layout.page_width)
            if ix_max - ix_min < 1:
                continue
            corners = ((ix_min - margin, iy_min - page_top),
                       (ix_max - margin, iy_max - page_top))
            points = []
            for dx, dy in corners:
                ux, uy = self._displayed_to_unrotated(dx, dy, layout)
                points.append((ux / scale, uy / scale))
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            rect = Poppler.Rectangle()
            rect.x1, rect.y1 = min(xs), min(ys)
            rect.x2, rect.y2 = max(xs), max(ys)
            # rotation 传 0：矩形已换算到未旋转页面空间。
            region = doc.get_page(page).get_selected_region(scale, 0, rect)
            if region.num_rectangles() > 0:
                self.text_selection_rects[page] = rect
                self.text_selection_regions[page] = self._refine_selection_region(
                    page, region, scale)

    def _refine_selection_region(self, page, region, scale):
        '''把 get_selected_region 的整数 Region 精修为浮点字形框列表。

        get_selected_region 返回 cairo.Region（整数 css 矩形，floor/ceil
        取整 + GLYPH 样式自带的行高填充），直接绘制时高亮与文字有 1~3px
        的错位。这里用 get_text_layout() 的浮点字形框（与 page.render
        同一套文本引擎，可精确对齐）重算：取中心落在 Region 矩形内的
        字形，返回它们的包围盒并集（points，top-down，浮点）。

        选择语义（哪些字形被选中）与文本提取（get_selected_text 用
        text_selection_rects）保持 poppler 原样，只改绘制的矩形。
        get_text_layout 不可用时回退为 Region 整数矩形（仍可用，只是
        恢复原 ~1px 取整误差）。'''
        glyphs = self._get_text_layout_glyphs(page)
        if glyphs is None:
            fallback = []
            for i in range(region.num_rectangles()):
                r = region.get_rectangle(i)
                fallback.append((r.x / scale, r.y / scale,
                                 (r.x + r.width) / scale, (r.y + r.height) / scale))
            return fallback
        refined = []
        for i in range(region.num_rectangles()):
            r = region.get_rectangle(i)
            x1, y1 = r.x / scale, r.y / scale
            x2, y2 = (r.x + r.width) / scale, (r.y + r.height) / scale
            for g in glyphs:
                # 中心点判定：字形中心落在 Region 矩形内才算选中。
                cx = (g.x1 + g.x2) / 2.0
                cy = (g.y1 + g.y2) / 2.0
                if x1 <= cx <= x2 and y1 <= cy <= y2:
                    refined.append((g.x1, g.y1, g.x2, g.y2))
        return self._tighten_selection_boxes(page, glyphs, refined)

    def _tighten_selection_boxes(self, page, glyphs, boxes):
        '''把选中字形框按行收缩到实际渲染墨迹的垂直范围（只缩不扩）。

        get_text_layout 的字形框是行高盒（ascent+descent，整行同高）：
        选中内容无降部字母（p/g/j/y/q 等）时底边比墨迹低 ~2pt，高亮条
        看起来偏下；含降部时又对齐——这就是「偶尔对不齐」的来源。
        这里用页面栅格的逐列墨迹轮廓求选中区域的真实墨迹上下缘，
        同一字形行（y 范围相同）统一收缩，空格与字母保持同高。

        只缩不扩：墨迹超出字形框的情形（大括号/上标）维持原框，
        避免把相邻行的墨迹误并入；扫描严格限在字形行 y 范围内，
        行间隙很小（实测 ~1pt）也不会越界。无墨迹（空格）或测量
        失败时保留原框。'''
        if not boxes:
            return boxes
        groups = dict()
        for b in boxes:
            key = (round(b[1], 1), round(b[3], 1))
            groups.setdefault(key, []).append(b)
        result = []
        for group in groups.values():
            y1 = min(b[1] for b in group)
            y2 = max(b[3] for b in group)
            profiles = self._get_line_ink_profiles(page, glyphs, y1, y2)
            if profiles is None:
                result.extend(group)
                continue
            px1, tops, bottoms = profiles
            s = _TEXT_INK_SCALE
            ink_top = ink_bot = None
            for b in group:
                c1 = max(0, int(math.floor(b[0] * s)) - px1)
                c2 = min(len(tops), int(math.ceil(b[2] * s)) - px1)
                for i in range(c1, c2):
                    t = tops[i]
                    if t is not None:
                        ink_top = t if ink_top is None else min(ink_top, t)
                        bt = bottoms[i]
                        ink_bot = bt if ink_bot is None else max(ink_bot, bt)
            if ink_top is None:
                result.extend(group)
                continue
            ny1 = max(y1, ink_top)
            ny2 = min(y2, ink_bot)
            if ny2 - ny1 < 0.5:
                # 收缩后过矮（测量异常）：保留原框。
                result.extend(group)
                continue
            result.extend((b[0], ny1, b[2], ny2) for b in group)
        return result

    def _get_line_ink_profiles(self, page, glyphs, y1, y2):
        '''字形行 (y1, y2) 的逐列墨迹轮廓（points，top-down）。

        返回 (px1, tops, bottoms)：tops[i]/bottoms[i] 为第 px1+i 像素列
        在行 y 范围内最上/最下墨迹像素的上/下缘（points），无墨迹为
        None。x 跨度取整页同 y 范围字形的并集（拖动中稳定，缓存友好）。
        测量失败返回 None。'''
        key = (page, round(y1, 1), round(y2, 1))
        if key in self._text_ink_profile_cache:
            return self._text_ink_profile_cache[key]
        profiles = None
        raster = self._get_selection_raster(page)
        if raster is not None:
            data, stride, width, height = raster
            s = _TEXT_INK_SCALE
            gx1 = gx2 = None
            ky1, ky2 = key[1], key[2]
            for g in glyphs:
                if round(g.y1, 1) == ky1 and round(g.y2, 1) == ky2:
                    gx1 = g.x1 if gx1 is None else min(gx1, g.x1)
                    gx2 = g.x2 if gx2 is None else max(gx2, g.x2)
            if gx1 is not None:
                px1 = max(0, int(math.floor(gx1 * s)))
                px2 = min(width, int(math.ceil(gx2 * s)))
                py1 = max(0, int(math.floor(y1 * s)))
                py2 = min(height, int(math.ceil(y2 * s)))
                if px2 > px1 and py2 > py1:
                    n = px2 - px1
                    tops = [None] * n
                    bottoms = [None] * n
                    threshold = _TEXT_INK_THRESHOLD
                    for py in range(py1, py2):
                        row = data[py * stride: py * stride + width * 4]
                        for px in range(px1, px2):
                            b, g, r, a = row[px * 4: px * 4 + 4]
                            if r < threshold or g < threshold or b < threshold:
                                i = px - px1
                                if tops[i] is None:
                                    tops[i] = py / s
                                bottoms[i] = (py + 1) / s
                    profiles = (px1, tops, bottoms)
        self._text_ink_profile_cache[key] = profiles
        return profiles

    def _get_selection_raster(self, page):
        '''白底未旋转页面栅格（_TEXT_INK_SCALE 倍），供墨迹测量。

        返回 (data, stride, width, height)；渲染失败返回 None。只缓存
        最近 _TEXT_INK_RASTER_MAX_PAGES 页（每页约 8MB），超出时淘汰
        最早的一页；profile 缓存不受影响（已建好的轮廓无需栅格）。'''
        if page in self._text_ink_raster_cache:
            return self._text_ink_raster_cache[page]
        raster = None
        try:
            poppler_page = self.poppler_document.get_page(page)
            pw, ph = poppler_page.get_size()
            s = _TEXT_INK_SCALE
            surface = cairo.ImageSurface(cairo.Format.ARGB32,
                                         max(1, int(pw * s)), max(1, int(ph * s)))
            ctx = cairo.Context(surface)
            ctx.scale(s, s)
            ctx.set_source_rgb(1, 1, 1)
            ctx.paint()
            poppler_page.render(ctx)
            surface.flush()
            raster = (surface.get_data(), surface.get_stride(),
                      surface.get_width(), surface.get_height())
        except Exception:
            raster = None
        self._text_ink_raster_cache[page] = raster
        while len(self._text_ink_raster_cache) > _TEXT_INK_RASTER_MAX_PAGES:
            oldest = next(iter(self._text_ink_raster_cache))
            del self._text_ink_raster_cache[oldest]
        return raster

    def _get_text_layout_glyphs(self, page):
        '''取（带缓存的）整页字形浮点矩形列表，失败返回 None。

        本机 poppler（26.x）无头实验确认 get_text_layout 返回 top-down
        points（页顶左原点，y 向下），与 get_selected_region 的输出
        约定一致。'''
        if page in self._text_layout_cache:
            return self._text_layout_cache[page]
        try:
            ok, glyphs = self.poppler_document.get_page(page).get_text_layout()
            if not ok:
                glyphs = None
        except Exception:
            glyphs = None
        self._text_layout_cache[page] = glyphs
        return glyphs

    def _displayed_to_unrotated(self, dx, dy, layout):
        '''页面局部显示坐标（css, y-down）→ 未旋转页面坐标（css, y-down）。

        与 draw 的旋转 transform / layouter 坐标反算同一套数学：绕显示
        盒中心与未旋转盒中心互转。rotation=0 直接原样返回。'''
        rotation = layout.rotation
        if rotation == 0:
            return dx, dy
        theta = math.radians(rotation)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        cx, cy = layout.page_width / 2.0, layout.page_height / 2.0
        ocx, ocy = layout.page_width_original / 2.0, layout.page_height_original / 2.0
        dx -= cx
        dy -= cy
        return (ocx + dx * cos_t + dy * sin_t, ocy - dx * sin_t + dy * cos_t)

    def get_pdf_date(self):
        if self.pdf_filename != None:
            try:
                return os.path.getmtime(self.pdf_filename)
            except OSError:
                # the file may have been removed after a failed build;
                # the in-memory document (if any) is kept for display.
                return None
        else:
            return None

    def load_pdf(self, external_reload=False, display_path=None):
        '''加载 PDF 到预览。

        display_path 非空时以"覆盖显示"加载该文件（diff PDF 视图）：替换
        poppler_document 与页面几何，但**不碰** pdf_filename、外部 PDF 监控
        和构建状态——构建管道对预览正在看什么保持无感知，监视器继续守着
        正式 PDF 的路径。
        '''
        is_override = display_path is not None
        display_target = display_path if display_path is not None else self.pdf_filename
        new_document = None
        # 重建前的页面几何，用于判断能否沿用现有 layout（见下方说明）。
        page_sizes_before = self.page_sizes
        if display_target is not None:
            try:
                new_document = Poppler.Document.new_from_file(GLib.filename_to_uri(display_target))
            except Exception:
                new_document = None

        if new_document != None:
            # a new PDF was loaded successfully -- replace the old one.
            self.poppler_document = new_document
            # 新内存文档 → 递增版本号，使渲染缓存整体失效重建。
            self.pdf_version += 1
            # 字形框缓存绑定旧文档，必须一并清空。
            self._text_layout_cache = dict()
            self._text_ink_raster_cache = dict()
            self._text_ink_profile_cache = dict()
            self._block_cache = dict()
            self.block_selection = None
            self.page_sizes = []
            for i in range(self.poppler_document.get_n_pages()):
                size = self.poppler_document.get_page(i).get_size()
                self.page_sizes.append((size.width, size.height))
            self.page_width, self.page_height = self.page_sizes[0]
            self.page_heights = [height for width, height in self.page_sizes]
            self.update_vertical_margin()
            # layout 只在几何真的变了时才作废。增量重编译（PhiTeX 一次编辑后
            # 页数与页面尺寸不变）是常态，原先每次构建都置 None：draw 见
            # layout is None 会"建完布局就直接返回、这一帧什么都不画"，于是每
            # 次编译预览区都闪一下白——构建慢时隔一两秒看不出来，构建只要十几
            # 毫秒时就是持续闪烁。保留旧 layout 对象同时也保住了滚动位置。
            if self.layout is not None and page_sizes_before != self.page_sizes:
                self.layout = None
            # 新 PDF 的页码 / 几何与旧选择不再对应，清除选择。
            self.clear_text_selection()
            # Only a successfully opened Poppler document is accepted as the
            # current disk version. This also suppresses monitor events caused
            # by NeoSetzer's own successful builds.
            # 覆盖显示（diff PDF）不进这套 tracker/monitor 协调：监视器必须
            # 继续守着正式 PDF 的路径，diff PDF 只是一张"借来展示"的页面。
            if not is_override:
                self._external_pdf_tracker.set_pdf_filename(self.pdf_filename)
                self._external_pdf_tracker.accept_current_file()
                self._ensure_external_pdf_monitor()
                self._set_external_pdf_state(ExternalPdfState.CURRENT)
            self.add_change_code('pdf_changed')
            self.add_change_code('layout_changed')
            return True
        elif is_override:
            # diff PDF 打不开（被清理/损坏）：留在当前显示，只发状态码让
            # 横幅端处理。绝不能走下面的 reset_pdf_data——那会把正式 PDF 的
            # 状态整个清掉。
            self.add_change_code('diff_view_load_failed')
            return False
        elif self.poppler_document == None:
            # nothing new and nothing old to fall back on -- show the
            # blank slate.
            self.reset_pdf_data()
        else:
            # the new PDF could not be loaded (e.g. it is still being
            # written or the build failed). Keep showing the previously
            # rendered PDF so the preview does not flicker to blank.
            # 不再静默：通知上层显示 toast + 错误图标，让用户知道构建
            # 实际失败而非误以为成功。
            if external_reload:
                self._set_external_pdf_state(self._external_pdf_tracker.record_reload_failure())
            else:
                self.add_change_code('pdf_load_failed')
        return False

    def reset_pdf_data(self):
        self._stop_external_pdf_monitor()
        self._external_pdf_tracker.clear()
        self._set_external_pdf_state(ExternalPdfState.CURRENT)
        self.pdf_filename = None
        self.poppler_document = None
        self.pdf_version += 1
        self.page_width = None
        self.page_height = None
        self.page_sizes = []
        self.page_heights = None
        self.layout = None
        self.clear_text_selection()
        if self.pdf_is_stale:
            self.set_pdf_is_stale(False)
        # PDF 没了，diff 结果与其视图一并作废。
        self.diff_result = None
        self.showing_diff_pdf = False
        self.diff_rectangles = list()
        self.diff_change_index = None
        self._pending_diff_jump = False
        self.add_change_code('pdf_changed')
        self.add_change_code('layout_changed')
        self.add_change_code('diff_state_changed')

    def get_page_height(self, page):
        '''第 page 页的 PDF（未旋转）高，0-based。无数据或越界返回 self.page_height
        （首页值，保留旧 API 行为）。'''
        if self.page_heights is None or page < 0 or page >= len(self.page_heights):
            return self.page_height
        return self.page_heights[page]

    def setup_layout_and_zoom_levels(self):
        self.layout = self.layouter.create_layout()
        # 必须在布局建立之后才更新动态缩放级别：fit_to_text_width 等模式依赖
        # 布局（页面尺寸、vertical_margin）与视口宽度来推导级别并居中。若在
        # create_layout 之前调用，会因 layout 为 None 而提前返回，导致恢复的
        # 缩放模式（及文字水平居中）无法生效。
        self.zoom_manager.update_dynamic_zoom_levels()
        # 兜底：仅当 update_dynamic 之后级别仍为空（极端情况，如视口宽度 < 300
        # 导致其提前返回）才退回 fit_to_width。必须放在 update_dynamic 之后，
        # 否则会先用默认值把 update_document 从磁盘恢复的 fit_to_text_width
        # 等模式覆盖掉（恢复时只设了 zoom_mode、未设 zoom_level，会被误判成
        # “尚未设置过缩放”而强制 reset 成 fit_to_width）。
        if self.zoom_manager.get_zoom_level() == None:
            # 仅当 update_dynamic 因极端情况（如首帧视口宽度 < 300 提前返回）
            # 仍未确定级别时，设一个安全默认级别；注意保留 zoom_mode，不要调用
            # set_zoom_fit_to_width()（它会把已恢复的 fit_to_text_width 等模式
            # 覆盖成 fit_to_width）。视口宽度就绪后 on_size_change 会按 zoom_mode 重算。
            self.zoom_manager.set_zoom_level(1.0)
        # 进入 diff 视图时布局尚未建好而挂起的定位，现在补上。
        self._apply_pending_diff_jump()
        self.add_change_code('layout_changed')

    def update_vertical_margin(self):
        # 均匀采样最多 10 页（含首页/末页），取每页最小 x1 的中位数作为
        # 垂直边距。原实现仅扫前 3 页取全局最小值——若前 3 页是标题页/目录页
        # （边距与正文不同，如全宽标题或居中文字），边距会被错误计算。
        # 中位数对异常页更鲁棒：单个标题页的偏小/偏大 x1 不会左右结果。
        n_pages = self.poppler_document.get_n_pages()
        if n_pages == 0:
            self.vertical_margin = self.page_width - 20
            return

        max_samples = 10
        if n_pages <= max_samples:
            page_indices = range(n_pages)
        else:
            # 均匀采样：含首页(index 0)和末页(index n-1)
            page_indices = [int(i * (n_pages - 1) / (max_samples - 1)) for i in range(max_samples)]

        per_page_mins = []
        for page_number in page_indices:
            page = self.poppler_document.get_page(page_number)
            layout = page.get_text_layout()
            page_min = self.page_width
            for rect in layout[1]:
                if rect.x1 < page_min:
                    page_min = rect.x1
            # 仅收集有文本的页面（page_min 被更新过），跳过空白页
            if page_min < self.page_width:
                per_page_mins.append(page_min)

        if len(per_page_mins) > 0:
            per_page_mins.sort()
            # 中位数：偶数个时取下中位数（margin 精度到 pt 级，两中值差异可忽略）
            current_min = per_page_mins[(len(per_page_mins) - 1) // 2]
        else:
            current_min = self.page_width
        current_min -= 20
        self.vertical_margin = current_min

    def scroll_to_position(self, x, y):
        if self.layout == None: return

        self.view.content.scroll_to_position([x, y])

    def scroll_dest_on_screen(self, dest):
        if self.layout == None: return
        if dest == None: return

        # Poppler.Dest.page_num 是 1-based（poppler-glib 在 find_dest 与
        # action.goto_dest.dest 上都做了 +1，用 pdftotext 页码交叉验证过），
        # 而本类与 layout 的页面 API 全是 0-based。不减 1 会把目标页当成
        # 下一页跳（末页链接则因越界直接不动作）。
        page_number = dest.page_num - 1
        content = self.view.content
        if self.rotation == 0:
            left = dest.left * self.layout.scale_factor
            top = dest.top * self.layout.scale_factor
            # width 原代码未定义直接使用，导致 NameError 崩溃。
            # Poppler.Dest 有 right/left 属性，但 XYZ 型链接目标通常只设
            # left/top，right 默认 0。用 max(right-left, 0) 安全取宽。
            width = max((dest.right - dest.left) * self.layout.scale_factor, 0)
            x = max(min(left, content.scrolling_offset_x), left + width - content.width + 18)
            # per-page 几何：y = 页顶 + 页面内 top-down 偏移。dest.top 是
            # PDF y-up（自页底向上量），页面内偏移 = page_height_px - top。
            # 旧等高公式 (h + gap) * page_num_1based - top - gap + padding
            # 展开后正是 page_top + (h - top)，per-page 改写不得再补 gap 项。
            page_top = self.layout.get_page_top(page_number)
            page_h_px = self.layout.get_page_height(page_number)
            if page_top is None or page_h_px is None:
                return
            y = page_top + (page_h_px - top)
            self.view.content.scroll_to_position([x, y])
        else:
            # dest coords are PDF (y-up). Convert the target to displayed canvas
            # coordinates via the rotation transform, then scroll there.
            page_h_pdf = self.get_page_height(page_number)
            top_down_top = page_h_pdf - dest.top
            pos = self.original_to_canvas(page_number, dest.left, top_down_top)
            if pos is None: return
            x_canvas, y_canvas = pos
            pos_r = self.original_to_canvas(page_number, dest.right, page_h_pdf - dest.bottom)
            width = max(abs(pos_r[0] - x_canvas), 0) if pos_r is not None else 0
            x = max(min(x_canvas, content.scrolling_offset_x), x_canvas + width - content.width + 18)
            self.view.content.scroll_to_position([x, y_canvas])

    def original_to_canvas(self, page_number, x_pt, y_pt):
        '''Map an original (un-rotated) page point in PDF points (y-down, page
        top-left origin) to canvas coordinates, honoring the current rotation.
        '''
        layout = self.layout
        if layout is None: return None
        rotation = self.rotation
        scale = layout.scale_factor
        if rotation == 0:
            bx = x_pt * scale
            by = y_pt * scale
        else:
            disp_w = layout.page_width
            disp_h = layout.page_height
            orig_w = layout.page_width_original
            orig_h = layout.page_height_original
            ocx = orig_w / 2.0
            ocy = orig_h / 2.0
            cx = disp_w / 2.0
            cy = disp_h / 2.0
            theta = math.radians(rotation)
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            ox = x_pt * scale
            oy = y_pt * scale
            dx = ox - ocx
            dy = oy - ocy
            bx = cx + dx * cos_t - dy * sin_t
            by = cy + dx * sin_t + dy * cos_t
        margin = layout.get_horizontal_margin(self.view.get_viewport_width())
        x_canvas = margin + bx
        # per-page 几何：用 get_page_top 取代 "(page_height + gap) * page"。
        page_top = layout.get_page_top(page_number)
        if page_top is None:
            return None
        y_canvas = page_top + by
        return (x_canvas, y_canvas)

    def update_position(self):
        if self.layout == None: return

        self.add_change_code('position_changed')

    # --- View toggles used by the context menu --------------------------------

    def rotate(self, delta):
        self.rotation = (self.rotation + delta) % 360
        if self.layout is not None:
            self.layout = self.layouter.create_layout()
            self.add_change_code('layout_changed')
        self.view.drawing_area.queue_draw()

    def toggle_recolor(self):
        self.recolor_pdf = not self.recolor_pdf
        try:
            self.document.settings.set_value('preferences', 'recolor_pdf', self.recolor_pdf)
        except Exception:
            pass
        self.add_change_code('recolor_pdf_changed')
        self.view.drawing_area.queue_draw()

    def open_link(self, link):
        self.controller.open_link(link)

    def get_context_at(self, x_offset, y_offset):
        if self.layout is None or self.poppler_document is None:
            return (None, None)
        window_width = self.view.get_viewport_width()
        layout = self.layout
        n_pages = self.poppler_document.get_n_pages()
        # per-page 几何：用 layout.get_page_by_offset 转 1-based，转回 0-based。
        # offset 落在顶部 vertical_padding 时返回第 1 页 → 0；下方 clamp 到 n-1。
        page_number = layout.get_page_by_offset(y_offset) - 1
        if page_number < 0: page_number = 0
        if page_number >= n_pages: page_number = n_pages - 1

        data = layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data is not None:
            _, x_pt, y_pt = data
        else:
            # 点击在 gap / padding 区：layout 方法返回 None，自己算。
            # per-page：用 get_page_top 取代旧公式。
            h_margin = layout.get_horizontal_margin(window_width)
            page_top = layout.get_page_top(page_number)
            page_h_px = layout.get_page_height(page_number)
            if page_top is None or page_h_px is None:
                x_pt = (x_offset - h_margin) / layout.scale_factor
                y_pt = 0.0
            else:
                x_pt = (x_offset - h_margin) / layout.scale_factor
                y_pt = max(0.0, min(y_offset - page_top, page_h_px)) / layout.scale_factor

        links = self.links_parser.get_links_for_page(page_number)
        # per-page：x_pt / y_pt 是 un-rotated PDF coords；反转为左下原点
        # (x, y) 需用 get_page_width / get_page_height（这里 width 仍
        # 假设统一，per-page width 是未来工作）。
        x_off = self.page_width - x_pt
        page_h_pdf = self.get_page_height(page_number)
        y_off = page_h_pdf - y_pt
        link = None
        for l in links:
            if x_off > l[0].x1 and x_off < l[0].x2 and y_off > l[0].y1 and y_off < l[0].y2:
                link = l
                break
        return (page_number, link)

    # --- Context-menu text / image / print actions ----------------------------

    def copy_page_text(self, page_number):
        self.presenter.copy_page_text(page_number)

    def copy_page_image(self, page_number):
        self.presenter.copy_page_image(page_number)

    def save_page_image(self, page_number):
        self.presenter.save_page_image(page_number)

    def print_pdf(self):
        self.presenter.print_pdf()

    # --- PDF search ------------------------------------------------------------

    def search(self, query, forward=True, start_page=None):
        if self.poppler_document is None: return False
        query = query.strip()
        if query == '': return False
        n = self.poppler_document.get_n_pages()
        if start_page is None:
            start_page = 0 if (self._search_query != query) else self._search_page
        result = self._search_from(query, start_page, forward)
        if result is None:
            result = self._search_from(query, 0 if forward else n - 1, forward)
        if result is None:
            return False
        self._search_query = query
        self._search_page = result[0]
        self._highlight_search(result[0], result[1])
        return True

    def _search_from(self, query, start_page, forward):
        n = self.poppler_document.get_n_pages()
        for i in range(n):
            p = (start_page + (i if forward else -i)) % n
            page = self.poppler_document.get_page(p)
            try:
                rect = page.find_text(query)
            except AttributeError:
                rect = page.search_text(query, 0)
            if rect is not None:
                return (p, rect)
        return None

    def _highlight_search(self, page, rect):
        h = rect.x1
        # per-page：rect.y2 是 PDF y-up（从底），top-down y = page_height - y2
        top = self.get_page_height(page) - rect.y2
        width = max(rect.x2 - rect.x1, 0)
        height = max(rect.y2 - rect.y1, 0)
        if width <= 0 or height <= 0:
            return
        self.set_synctex_rectangles([{'page': page + 1, 'h': h, 'v': top, 'width': width, 'height': height}])

    def show_synctex_rectangles(self, rectangles):
        '''把矩形交给绘制层并起淡出循环，不做任何滚动。'''
        self.visible_synctex_rectangles = rectangles
        self.layouter.update_synctex_rectangles(self.layout)
        self.visible_synctex_rectangles_time = GLib.get_monotonic_time() / 1_000_000
        if len(rectangles) > 0:
            self.presenter.start_fade_loop()

    def set_synctex_rectangles(self, rectangles, source_text=None, phitex=False):
        if self.layout == None: return

        # 源码行文字反查 PDF 行两个用途：phitex 的 synctex 行号不可靠（见
        # synctex_line_repair），矩形可能落在完全不相干的位置；align 等多行
        # 环境 synctex 会返回多行的盒（实测两行 align 返回 24 个矩形混杂
        # 两行），高亮/滚动落在第一行而非光标行。页面上的文字是对的，
        # 用源码行文字筛出正确行；单盒且非 phitex 时不动（synctex 通常是对的），
        # 匹配不到就保持 synctex 的结果。
        if source_text and (len(rectangles) > 1 or phitex):
            matched = self._find_rectangle_for_source_text(source_text, rectangles)
            if matched is not None:
                _sync_debug('正向同步按源码行文本纠正矩形: page %s' % matched['page'])
                rectangles = [matched]

        self.show_synctex_rectangles(rectangles)

        if len(rectangles) > 0 and not self._scroll_to_rectangle(rectangles[0]):
            return
        # 通知独立窗口（若预览已 detach）present 自身，让用户看到正向跳转结果。
        # 仅发 change_code，不直接 import 窗口类——保持 model 与 UI 解耦。
        self.add_change_code('synctex_forward')

    def _get_pdf_text_lines(self, page_index):
        '''Return [(text, (x1, y1, x2, y2)), ...] for a page, top-down points.

        get_text_layout() 的矩形与 get_text() 的字符一一对应（与
        _get_pdf_line_offset 同一约定），按其中的换行符切行即可拿到每行的
        文字与包围盒，无需额外猜测行边界。
        '''
        try:
            poppler_page = self.poppler_document.get_page(page_index)
            ok, rects = poppler_page.get_text_layout()
            page_text = poppler_page.get_text()
        except Exception:
            return list()
        if not ok or not rects or len(rects) != len(page_text):
            return list()

        lines = list()
        chars = list()
        box = [float('inf'), float('inf'), float('-inf'), float('-inf')]
        for char, rect in zip(page_text, rects):
            if char in ('\n', '\r'):
                if chars:
                    lines.append((''.join(chars), tuple(box)))
                chars = list()
                box = [float('inf'), float('inf'), float('-inf'), float('-inf')]
                continue
            if rect.x2 <= rect.x1 and rect.y2 <= rect.y1:
                continue
            chars.append(char)
            box[0] = min(box[0], rect.x1)
            box[1] = min(box[1], rect.y1)
            box[2] = max(box[2], rect.x2)
            box[3] = max(box[3], rect.y2)
        if chars:
            lines.append((''.join(chars), tuple(box)))
        return lines

    def _find_rectangle_for_source_text(self, source_text, rectangles):
        '''用源码行文字在 PDF 里找对应文本行，返回 synctex 格式矩形。

        synctex 给的页通常是对的，所以先只查这些页；phitex 也可能把一段
        文字归到另一页的节点上（实测代码清单被指到第 10 页），那一页找不到
        时再顺序扫全文档，命中即停。找到后转成与 synctex 一致的约定：
        v 是包围框底边距页顶的距离（_scroll_to_rectangle 会再减 height）。
        '''
        if not has_matchable_text(source_text):
            return None

        candidate_pages = list()
        for rect in rectangles:
            page = rect.get('page')
            if isinstance(page, int) and page not in candidate_pages:
                candidate_pages.append(page)

        try:
            page_count = self.poppler_document.get_n_pages()
        except Exception:
            page_count = 0

        pages = [page for page in candidate_pages if 1 <= page <= page_count]
        pages += [page for page in range(1, page_count + 1)
                  if page not in pages]

        for page in pages:
            lines = self._get_pdf_text_lines(page - 1)
            if not lines:
                continue
            index = match_pdf_line(source_text, [text for text, _ in lines])
            if index is None:
                continue
            x1, y1, x2, y2 = lines[index][1]
            return {'page': page,
                    'h': x1,
                    'v': y2,
                    'width': max(x2 - x1, 1.0),
                    'height': max(y2 - y1, 1.0)}
        return None

    def _scroll_to_rectangle(self, position):
        '''把矩形滚进视野，成功返回 True（页号越界则不动）。

        坐标换算：SyncTeX 的 v 是包围框**下边缘**距页顶的距离（y 轴向上），
        所以 cairo 的 top 要减掉高度，再乘 scale_factor 得到画布坐标；左右各留
        18px 让文字不被贴边切掉。y 只在下缘高出视口 30% 时才动，免得一个小框
        把整页顶出屏幕。
        '''
        content = self.view.content
        sf = self.layout.scale_factor
        left = position['h'] * sf
        top = (position['v'] - position['height']) * sf
        width = position['width'] * sf
        height = position['height'] * sf

        # per-page：page 1-based → 0-based，用 get_page_top。
        page_top = self.layout.get_page_top(position['page'] - 1)
        if page_top is None:
            return False

        x = max(min(left - 18, content.scrolling_offset_x), left + width - content.width + 18)
        y = page_top + max(0, top - height / 2 - content.height * 0.3)
        content.scroll_to_position([x, y])
        return True

    def flash_changed_rectangles(self, rectangles):
        '''增量编译后闪一下改动处：默认不跳页，只有改动全在视口外才跳。

        这条路径每半秒就会走一次，和正向同步的"跳过去"是两种手感：照
        set_synctex_rectangles 那样无条件滚到第一个矩形，等于每次停顿输入都把
        用户视野拽回改动点——慢编译器一小时跳一次看不出来，18ms 增量编译下就
        是没法用的体验。但完全不平移也不对：改动落在视口外时高亮画在看不见的
        地方，反馈等于零。所以判一次可见性，只在看不见时跳。

        任一改动页在视口内就不跳：多处改动时用户看见其中一处闪动即可，全部
        往第一处拽反而会盖掉其余的位置线索。
        '''
        if self.layout is None or not rectangles:
            return False
        self.show_synctex_rectangles(rectangles)

        content = self.view.content
        # get_page_by_offset 与 rectangle['page'] 都是 1-based。
        top_page = self.layout.get_page_by_offset(content.scrolling_offset_y)
        bottom_page = self.layout.get_page_by_offset(
            content.scrolling_offset_y + content.height)
        if not any(top_page <= r['page'] <= bottom_page for r in rectangles):
            self._scroll_to_rectangle(rectangles[0])
        return True

    # ---- AI 编辑 diff（PhiTeX session compare）----

    def set_diff_result(self, result):
        '''AiDiff 交付 compare 结果（None = 清除，如 AI 没改或结果失效）。

        只存状态 + 发 diff_state_changed：正式 PDF 上不画 diff 矩形（坐标
        属于 diff PDF），横幅由 presenter 端按状态刷新。若用户正停留在
        diff 视图而被清除（文档重置/结果失效），先落回正式 PDF。
        '''
        if result is None:
            self.diff_result = None
            self.diff_rectangles = list()
            self.diff_change_index = None
            self._pending_diff_jump = False
            if self.showing_diff_pdf:
                self.showing_diff_pdf = False
                self.load_pdf()
            self.add_change_code('diff_state_changed')
            return
        self.diff_result = result
        self.add_change_code('diff_state_changed')

    def show_diff_pdf(self):
        '''以覆盖方式加载 diff PDF，并把 diff 矩形推给 layouter 绘制。

        由预览面板的 diff 档驱动（见 PreviewPanelPresenter.set_mode）。'''
        result = self.diff_result
        pdf_path = (result or {}).get('pdf')
        if not pdf_path or not os.path.isfile(pdf_path):
            return
        if not self.load_pdf(display_path=pdf_path):
            return
        self.showing_diff_pdf = True
        self.diff_rectangles = result.get('rectangles') or list()
        if self.layout is not None:
            self.layouter.update_diff_rectangles(self.layout)
        # 进入 diff 视图即落到当前/第一处改动：toast 说"有 N 处"之后，
        # 用户切过来不该还停在原 PDF 的滚动位置，靠肉眼找颜色。
        total = len(self.diff_rectangles)
        if total:
            index = self.diff_change_index
            if not isinstance(index, int) or not 0 <= index < total:
                index = 0
            self.diff_change_index = index
            if not self._scroll_to_rectangle(self.diff_rectangles[index]):
                self._pending_diff_jump = True
        self.add_change_code('diff_position_changed')
        self.add_change_code('diff_state_changed')

    def show_main_pdf(self):
        '''从 diff 视图回到正式 PDF（重新从磁盘加载，清除 diff 矩形）。'''
        if not self.showing_diff_pdf:
            return
        self.showing_diff_pdf = False
        self.diff_rectangles = list()
        self.load_pdf()
        self.add_change_code('diff_state_changed')

    def get_diff_change_by_n(self, change_n):
        '''按 compare 的 n 字段反查 change dict（点击弹窗用）。'''
        for change in (self.diff_result or {}).get('changes') or []:
            if isinstance(change, dict) and change.get('n') == change_n:
                return change
        return None

    def jump_to_diff_change(self, index):
        '''跳到第 index 处改动（0-based，diff_rectangles 的下标）。

        复用 synctex 正向同步的 _scroll_to_rectangle：它已处理 per-page 页顶、
        缩放因子与"只在下缘高出视口 30% 时动"的滚动策略。布局尚未建立
        （diff PDF 页尺寸与正式 PDF 不同会作废 layout）时记待办，等
        setup_layout_and_zoom_levels 建好布局再补跳。'''
        total = len(self.diff_rectangles)
        if total == 0:
            return False
        try:
            index = int(index)
        except (TypeError, ValueError):
            return False
        index = max(0, min(index, total - 1))
        if index != self.diff_change_index:
            self.diff_change_index = index
            self.add_change_code('diff_position_changed')
        if self._scroll_to_rectangle(self.diff_rectangles[index]):
            return True
        if self.layout is None:
            self._pending_diff_jump = True
        return False

    def step_diff_change(self, delta):
        '''上一处/下一处（delta = ±1）。不循环：审阅有终点。'''
        total = len(self.diff_rectangles)
        if total == 0:
            return False
        current = self.diff_change_index
        if not isinstance(current, int):
            current = 0
        return self.jump_to_diff_change(max(0, min(current + delta, total - 1)))

    def sync_diff_change_from_click(self, change_n):
        '''点击高亮矩形时把导航器拨到对应那处（不滚动——用户已经在那儿）。

        change_n 找不到（矩形与 changes 理论上同源，找不到只可能是数据
        异常）时保持导航器不动。'''
        for index, rectangle in enumerate(self.diff_rectangles):
            if rectangle.get('n') == change_n:
                if self.diff_change_index != index:
                    self.diff_change_index = index
                    self.add_change_code('diff_position_changed')
                return index
        return None

    def _apply_pending_diff_jump(self):
        '''布局建好后补做进入 diff 视图时那一次定位（见 jump_to_diff_change）。'''
        if not self._pending_diff_jump:
            return
        self._pending_diff_jump = False
        index = self.diff_change_index
        if isinstance(index, int) and 0 <= index < len(self.diff_rectangles):
            self._scroll_to_rectangle(self.diff_rectangles[index])

    @property
    def diff_result_usable(self):
        '''diff 结果是否存在**且可看**（diff PDF 没排出来的失败结果不算）。

        档位显隐、能否进入 diff 模式都以此为准：失败结果仍保留在
        diff_result 里（错误信息不丢），只是不作为可切换的内容。'''
        result = self.diff_result
        return result is not None and not result.get('failed')

    def end_diff_review(self):
        '''手动结束本轮 AI 审阅（工具栏结束按钮）。

        清结果（档位/结束按钮随之消失、diff 视图落回正式 PDF），并把
        AiDiff 解除武装 + 停 JOB-diff。与"切走档位"的语义区分：切走只是
        去看别的内容，结果保留；这里是明确说"这轮我看完了"。'''
        self.diff_result = None
        self.diff_rectangles = list()
        self.diff_change_index = None
        self._pending_diff_jump = False
        if self.showing_diff_pdf:
            self.showing_diff_pdf = False
            self.load_pdf()
        self.add_change_code('diff_state_changed')
        build_system = getattr(self.document, 'build_system', None)
        ai_diff = getattr(build_system, 'ai_diff', None)
        if ai_diff is not None:
            ai_diff.end_review()

    def init_backward_sync(self, x_offset, y_offset):
        if self.layout == None:
            _sync_debug('init_backward_sync: layout is None，不反查')
            return False

        window_width = self.view.get_viewport_width()
        data = self.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data is None:
            # Click in the gap between pages or outside the page margin: clamp to
            # the nearest page and page-local offsets。per-page 几何：找最近页
            # 并把 y_offset clamp 到该页范围内。
            n_pages = self.poppler_document.get_n_pages()
            page_idx = self.layout.get_page_by_offset(y_offset) - 1
            if page_idx < 0: page_idx = 0
            if page_idx >= n_pages: page_idx = n_pages - 1
            page_top = self.layout.get_page_top(page_idx)
            page_h_px = self.layout.get_page_height(page_idx)
            if page_top is None or page_h_px is None:
                return False
            y_pixels = min(max(y_offset - page_top, 0), page_h_px)
            x_pixels = min(max(x_offset - self.layout.get_horizontal_margin(window_width), 0), self.layout.page_width)
            page = page_idx
            x = x_pixels / self.layout.scale_factor
            y = y_pixels / self.layout.scale_factor
        else:
            page, x, y = data

        n_pages = self.poppler_document.get_n_pages()
        if page < 0 or page >= n_pages:
            _sync_debug('页号越界 page=%s n_pages=%s' % (page, n_pages))
            return False

        poppler_page = self.poppler_document.get_page(page)
        # per-page：x / y 是 un-rotated PDF 坐标，clamp 用该页尺寸。
        page_h_pdf = self.get_page_height(page)
        rect = Poppler.Rectangle()
        rect.x1 = max(min(x, self.page_width), 0)
        rect.y1 = max(min(y, page_h_pdf), 0)
        rect.x2 = max(min(x, self.page_width), 0)
        rect.y2 = max(min(y, page_h_pdf), 0)
        word = poppler_page.get_selected_text(Poppler.SelectionStyle.WORD, rect)
        context = poppler_page.get_selected_text(Poppler.SelectionStyle.LINE, rect)

        # Character-level refinement: locate the exact character under the
        # click point via Poppler's text layout. The 0-based offset within
        # the PDF line is later mapped to the source line (in build_system)
        # to align the cursor to the precise character, not just the line.
        pdf_line_offset, pdf_line_text = self._get_pdf_line_offset(poppler_page, x, y)

        _sync_debug('反查提交 page(1-based)=%s x=%.1f y=%.1f（直查，不经过 build mode）' % (
            page + 1, x, y))
        # 直查路径：后台线程 synctex edit → idle 跳转。编译期间也可用、
        # 无 build mode 往返（builder_backward_sync 旧路径闲置保留）。
        from setzer.helpers import synctex_query
        tex_filename = self.document.get_filename()
        if tex_filename is None or self.pdf_filename is None:
            return False
        # 影子 synctex 新鲜度门控：直查路径不经 can_sync，这里自行拦截，
        # 不拿陈旧影子 gz 跳转到错行。
        build_system = getattr(self.document, 'build_system', None)
        if build_system is not None and not build_system.synctex_is_fresh():
            from setzer.helpers import toast
            toast.show(_('Sync data is being updated in the background. Try again in a moment.'), timeout=2)
            return False
        directory = synctex_query.synctex_directory_for_source(tex_filename, self.pdf_filename)
        pdf_filename = self.pdf_filename
        refinement = (word, context, pdf_line_offset, pdf_line_text)
        # 主线程预提取本页字形盒与 buffer 快照：phitex 数学区反查落
        # prose 时，后台线程用视觉块归属纠正落点（线程里不读 poppler/Gtk）。
        try:
            ok, glyphs = poppler_page.get_text_layout()
            glyph_rects = [(g.x1, g.y1, g.x2, g.y2) for g in glyphs] if ok else []
        except Exception:
            glyph_rects = []
        buffer = self.document.source_buffer
        active_lines = buffer.get_text(buffer.get_start_iter(),
                                       buffer.get_end_iter(), True).splitlines(keepends=True)
        document = self.document

        def _run():
            position = synctex_query.query_source_position(
                pdf_filename, page + 1, x, y, directory)
            refine = refinement
            if position is not None and glyph_rects:
                from setzer.ai_fix import pdf_annotation_mapper
                target = pdf_annotation_mapper.backward_math_redirect(
                    document, pdf_filename, page, x, y, position,
                    active_lines, glyph_rects, directory)
                if target is not None:
                    _sync_debug('phitex 数学区反查落行 %s，视觉归属重定向到 %s' % (
                        position['line'], target))
                    position = dict(position, line=target)
                    # 精修字段来自点击处 prose 文本，对数学行不再适用。
                    refine = (None, None, None, None)
            GLib.idle_add(self._deliver_backward_sync, position, refine)

        threading.Thread(target=_run, daemon=True).start()
    
    def _deliver_backward_sync(self, position, refinement):
        if position is not None:
            (position['word'], position['context'],
             position['pdf_line_offset'], position['pdf_line_text']) = refinement
            position['line'] -= 1  # helper 1-based → set_synctex_position 0-based
            ServiceLocator.get_workspace().jump_to_synctex_position(self.document, position)
            # 通知独立窗口（若预览已 detach）present 主窗口，让用户看到跳转
            # 落点。查询失败（无 .synctex.gz / 超时 / 解析不出）没有落点，
            # 不发码——独立预览场景下不该无谓把主窗口抢到前台。
            self.add_change_code('synctex_backward')
        return False

    def _get_pdf_line_offset(self, poppler_page, click_x, click_y):
        '''Return (char_offset, line_text) for the character closest to
        (click_x, click_y) within its PDF text line, or (None, None) when
        the text layout is unavailable.

        Poppler's get_text_layout() yields one Rectangle per character in
        get_text(), so the index of the clicked rectangle is also its index
        in the page text. Counting back to the previous newline gives the
        0-based offset within the line.
        '''
        try:
            layout = poppler_page.get_text_layout()
            if not layout or not layout[0]:
                return None, None
            rects = layout[1]
        except (TypeError, IndexError, AttributeError):
            return None, None
        if not rects:
            return None, None

        try:
            page_text = poppler_page.get_text()
        except Exception:
            return None, None
        # Layout must have one rect per character for index arithmetic to hold
        if len(rects) != len(page_text):
            return None, None

        # Find the visible character closest to the click point.
        # Compare by (x_distance, y_distance) so that when two lines both
        # contain the click x (rare, but possible near line boundaries), the
        # character on the actually-clicked line wins.
        best_idx = -1
        best_x_dist = float('inf')
        best_y_dist = float('inf')
        for i, r in enumerate(rects):
            # Skip zero-area rects (newlines, control chars with no glyph)
            if r.x2 <= r.x1 and r.y2 <= r.y1:
                continue
            # Vertical filter: only consider characters near the click line.
            # Use a tight tolerance (2pt) so adjacent text lines (typically
            # 3-4pt apart) don't bleed into each other.
            if click_y < r.y1 - 2 or click_y > r.y2 + 2:
                continue
            # Horizontal distance to this character
            if r.x1 <= click_x <= r.x2:
                x_dist = 0.0
            else:
                x_dist = min(abs(r.x1 - click_x), abs(r.x2 - click_x))
            y_dist = abs((r.y1 + r.y2) / 2 - click_y)
            if x_dist < best_x_dist or (x_dist == best_x_dist and y_dist < best_y_dist):
                best_x_dist = x_dist
                best_y_dist = y_dist
                best_idx = i

        if best_idx < 0:
            return None, None

        # Walk back to the line start in page_text
        line_start = best_idx
        while line_start > 0 and page_text[line_start - 1] != '\n':
            line_start -= 1
        line_end = best_idx
        while line_end < len(page_text) - 1 and page_text[line_end + 1] != '\n':
            line_end += 1

        raw_line = page_text[line_start:line_end + 1]
        raw_offset = best_idx - line_start

        # Poppler may return accented characters in decomposed (NFD) form
        # (e.g. 'é' as 'e' + U+0301), while the Gtk source buffer stores them
        # precomposed (NFC). Normalize to NFC so SequenceMatcher can align the
        # two texts character-by-character. The offset is mapped through by
        # normalizing the prefix up to the clicked position.
        line_text = unicodedata.normalize('NFC', raw_line)
        offset = len(unicodedata.normalize('NFC', raw_line[:raw_offset]))

        return offset, line_text

    def shutdown(self):
        '''文档关闭时由 Document.shutdown 调用。取消滚动减速动画的 timeout、
        synctex 高亮淡出动画、断开 settings 单例信号连接，防止回调在 widget
        已销毁后继续访问 adjustment/drawing_area 等已释放对象，以及 settings
        持有引用导致文档无法 GC。'''
        try:
            self.view.content.cancel_deceleration()
        except AttributeError:
            pass
        try:
            self.presenter.cancel_fade_loop()
        except AttributeError:
            pass
        self._stop_external_pdf_monitor()
        try:
            self.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass

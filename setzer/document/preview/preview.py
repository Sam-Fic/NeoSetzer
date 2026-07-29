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
gi.require_version('Poppler', '0.18')
gi.require_version('Gtk', '4.0')
from gi.repository import Poppler
from gi.repository import GLib
from gi.repository import Gio

import os.path
import time
import math
import unicodedata

import setzer.document.preview.preview_viewgtk as preview_view
import setzer.document.preview.preview_layouter as preview_layouter
import setzer.document.preview.preview_presenter as preview_presenter
import setzer.document.preview.preview_controller as preview_controller
import setzer.document.preview.preview_page_renderer as preview_page_renderer
import setzer.document.preview.preview_links_parser as preview_links_parser
import setzer.document.preview.preview_zoom_manager as preview_zoom_manager
import setzer.document.preview.context_menu.context_menu as context_menu
from setzer.helpers.observable import Observable
from setzer.helpers.timer import timer


class Preview(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document

        self.pdf_filename = None
        # 构建失败但保留旧 PDF 时为 True：预览面板据此显示「构建失败，显示的是
        # 上一次成功的 PDF」横幅，避免用户误以为构建成功。由 build_system 置 True，
        # set_pdf_filename / reset_pdf_data 置 False。
        self.pdf_is_stale = False
        self.recolor_pdf = self.document.settings.get_value('preferences', 'recolor_pdf')

        self.poppler_document = None
        self.page_width = None
        self.page_height = None
        self.layout = None

        self.rotation = 0
        self._search_query = ''
        self._search_page = 0

        self.visible_synctex_rectangles = list()
        self.visible_synctex_rectangles_time = None

        self.view = preview_view.PreviewView()
        self.layouter = preview_layouter.PreviewLayouter(self, self.view)
        self.zoom_manager = preview_zoom_manager.PreviewZoomManager(self, self.view)
        self.controller = preview_controller.PreviewController(self, self.view)
        self.page_renderer = preview_page_renderer.PreviewPageRenderer(self)
        self.links_parser = preview_links_parser.PreviewLinksParser(self)
        self.presenter = preview_presenter.PreviewPresenter(self, self.page_renderer, self.view)
        self.context_menu = context_menu.ContextMenu(self, self.view)

        self.document.connect('filename_change', self.on_filename_change)
        self.document.connect('pdf_updated', self.on_pdf_updated)

        # 保存回调引用以便 shutdown 时断开 settings 单例连接。
        self._settings_callback = self.on_settings_changed
        self.document.settings.connect('settings_changed', self._settings_callback)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.recolor_pdf = value
            self.add_change_code('recolor_pdf_changed')
            self.view.drawing_area.queue_draw()

    def on_filename_change(self, document, filename=None):
        if filename != None:
            pdf_filename = os.path.splitext(filename)[0] + '.pdf'
            if os.path.exists(pdf_filename):
                self.set_pdf_filename(pdf_filename)
        self.load_pdf()

    def on_pdf_updated(self, document):
        self.load_pdf()

    def set_pdf_filename(self, pdf_filename):
        if pdf_filename != self.pdf_filename:
            self.pdf_filename = pdf_filename
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

    def load_pdf(self):
        new_document = None
        if self.pdf_filename != None:
            try:
                new_document = Poppler.Document.new_from_file(GLib.filename_to_uri(self.pdf_filename))
            except Exception:
                new_document = None

        if new_document != None:
            # a new PDF was loaded successfully -- replace the old one.
            self.poppler_document = new_document
            page_size = self.poppler_document.get_page(0).get_size()
            self.page_width = page_size.width
            self.page_height = page_size.height
            self.update_vertical_margin()
            self.layout = None
            self.add_change_code('pdf_changed')
            self.add_change_code('layout_changed')
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
            self.add_change_code('pdf_load_failed')

    def reset_pdf_data(self):
        self.pdf_filename = None
        self.poppler_document = None
        self.page_width = None
        self.page_height = None
        self.layout = None
        if self.pdf_is_stale:
            self.set_pdf_is_stale(False)
        self.add_change_code('pdf_changed')
        self.add_change_code('layout_changed')

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

        page_number = dest.page_num
        content = self.view.content
        if self.rotation == 0:
            left = dest.left * self.layout.scale_factor
            top = dest.top * self.layout.scale_factor
            # width 原代码未定义直接使用，导致 NameError 崩溃。
            # Poppler.Dest 有 right/left 属性，但 XYZ 型链接目标通常只设
            # left/top，right 默认 0。用 max(right-left, 0) 安全取宽。
            width = max((dest.right - dest.left) * self.layout.scale_factor, 0)
            x = max(min(left, content.scrolling_offset_x), left + width - content.width + 18)
            y = (self.layout.page_height + self.layout.page_gap) * (page_number) - top - self.layout.page_gap
            self.view.content.scroll_to_position([x, y])
        else:
            # dest coords are PDF (y-up). Convert the target to displayed canvas
            # coordinates via the rotation transform, then scroll there.
            top_down_top = self.page_height - dest.top
            pos = self.original_to_canvas(page_number, dest.left, top_down_top)
            if pos is None: return
            x_canvas, y_canvas = pos
            pos_r = self.original_to_canvas(page_number, dest.right, self.page_height - dest.bottom)
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
        margin = layout.get_horizontal_margin(self.view.get_allocated_width())
        x_canvas = margin + bx
        y_canvas = page_number * (layout.page_height + layout.page_gap) + by
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
        window_width = self.view.get_allocated_width()
        layout = self.layout
        page_height_plus_gap = layout.page_height + layout.page_gap
        n_pages = self.poppler_document.get_n_pages()
        page_number = int(y_offset // page_height_plus_gap)
        if page_number < 0: page_number = 0
        if page_number >= n_pages: page_number = n_pages - 1

        data = layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data is not None:
            _, x_pt, y_pt = data
        else:
            x_pt = (x_offset - layout.get_horizontal_margin(window_width)) / layout.scale_factor
            y_pt = (y_offset - page_number * page_height_plus_gap) / layout.scale_factor

        links = self.links_parser.get_links_for_page(page_number)
        x_off = self.page_width - x_pt
        y_off = self.page_height - y_pt
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
        top = self.page_height - rect.y2
        width = max(rect.x2 - rect.x1, 0)
        height = max(rect.y2 - rect.y1, 0)
        if width <= 0 or height <= 0:
            return
        self.set_synctex_rectangles([{'page': page + 1, 'h': h, 'v': top, 'width': width, 'height': height}])

    def set_synctex_rectangles(self, rectangles):
        if self.layout == None: return

        self.visible_synctex_rectangles = rectangles
        self.layouter.update_synctex_rectangles(self.layout)
        self.visible_synctex_rectangles_time = time.time()

        if len(rectangles) > 0:
            content = self.view.content
            position = rectangles[0]
            page_number = position['page']
            sf = self.layout.scale_factor
            # SyncTeX v 是包围框底部距页面顶部的距离 (即包围框的下边缘 y 坐标)。
            # 要得到包围框上边缘 (cairo y)，需减去高度：top = v - height。
            left = position['h'] * sf
            top = (position['v'] - position['height']) * sf
            width = position['width'] * sf
            height = position['height'] * sf

            x = max(min(left - 18, content.scrolling_offset_x), left + width - content.width + 18)
            y = (self.layout.page_height + self.layout.page_gap) * (page_number - 1) + max(0, top - height / 2 - content.height * 0.3)

            content.scroll_to_position([x, y])
            self.presenter.start_fade_loop()
        # 通知独立窗口（若预览已 detach）present 自身，让用户看到正向跳转结果。
        # 仅发 change_code，不直接 import 窗口类——保持 model 与 UI 解耦。
        self.add_change_code('synctex_forward')

    def init_backward_sync(self, x_offset, y_offset):
        if self.layout == None: return False

        window_width = self.view.get_allocated_width()
        data = self.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data is None:
            # Click in the gap between pages or outside the page margin: clamp to
            # the nearest page and page-local offsets.
            y_total_pixels = min(max(y_offset, 0), (self.layout.page_height + self.layout.page_gap) * self.poppler_document.get_n_pages() - self.layout.page_gap)
            x_pixels = min(max(x_offset - self.layout.get_horizontal_margin(window_width), 0), self.layout.page_width)
            page = math.floor(y_total_pixels / (self.layout.page_height + self.layout.page_gap))
            y_pixels = min(max(y_total_pixels - page * (self.layout.page_height + self.layout.page_gap), 0), self.layout.page_height)
            x = x_pixels / self.layout.scale_factor
            y = y_pixels / self.layout.scale_factor
        else:
            page, x, y = data

        n_pages = self.poppler_document.get_n_pages()
        if page < 0 or page >= n_pages: return False

        poppler_page = self.poppler_document.get_page(page)
        rect = Poppler.Rectangle()
        rect.x1 = max(min(x, self.page_width), 0)
        rect.y1 = max(min(y, self.page_height), 0)
        rect.x2 = max(min(x, self.page_width), 0)
        rect.y2 = max(min(y, self.page_height), 0)
        word = poppler_page.get_selected_text(Poppler.SelectionStyle.WORD, rect)
        context = poppler_page.get_selected_text(Poppler.SelectionStyle.LINE, rect)

        # Character-level refinement: locate the exact character under the
        # click point via Poppler's text layout. The 0-based offset within
        # the PDF line is later mapped to the source line (in build_system)
        # to align the cursor to the precise character, not just the line.
        pdf_line_offset, pdf_line_text = self._get_pdf_line_offset(poppler_page, x, y)

        self.document.build_system.backward_sync(page + 1, x, y, word, context, pdf_line_offset, pdf_line_text)
        # 通知独立窗口（若预览已 detach）present 主窗口，让用户看到反向跳转的源码位置。
        # 点击发生在独立窗口，抬起主窗口让源码跳转可见。
        self.add_change_code('synctex_backward')

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
        try:
            self.document.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass



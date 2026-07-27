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

from gi.repository import GLib

from setzer.helpers.observable import Observable


class PreviewZoomManager(Observable):

    def __init__(self, preview, view):
        Observable.__init__(self)
        self.preview = preview
        self.view = view

        self.zoom_level_fit_to_width = None
        self.zoom_level_fit_to_text_width = None
        self.zoom_level_fit_to_height = None
        self.zoom_level = None
        self.zoom_set = False
        # 待执行的“fit to text width 后水平居中文字”idle 回调 ID。
        # 见 set_zoom_fit_to_text_width 注释：居中需在 ScrolledWindow 完成
        # 尺寸分配（hadjustment 上界更新到新画布尺寸）之后进行，故延迟到 idle。
        self._center_text_idle_id = None
        # 递归保护：update_dynamic_zoom_levels 内部可能调用
        # set_zoom_fit_to_width_auto_offset → set_zoom_level → update_dynamic_zoom_levels，
        # 此标志防止递归调用导致多余的布局重建。
        self._in_update_dynamic_levels = False
        # 缩放停靠点缓存：on_zoom_request 原每次 Ctrl+滚轮都重建 3 元素列表
        # + `in` 线性扫描。fit_to_* 级别仅在 update_dynamic_zoom_levels 后变化，
        # 故在那里缓存为 tuple，on_zoom_request 直接读取，tuple 的 `in` 也快于 list。
        self._stopping_points = ()

    def update_dynamic_zoom_levels(self):
        if self.preview.layout == None: return
        if self.view.get_allocated_width() < 300: return

        old_level = self.zoom_level_fit_to_width

        self._in_update_dynamic_levels = True
        try:
            self.update_fit_to_width()
            self.update_fit_to_text_width()
            self.update_fit_to_height()

            if self.zoom_level == old_level and self.zoom_level_fit_to_width != old_level:
                self.set_zoom_fit_to_width_auto_offset()

            if not self.zoom_set:
                self.zoom_set = True
                self.set_zoom_fit_to_width()

            # fit_to_* 级别此刻已最终确定（含可能的 set_zoom_fit_to_width 回调后的
            # 值），缓存停靠点供 on_zoom_request 读取。
            self._stopping_points = tuple(
                lvl for lvl in (self.zoom_level_fit_to_width, self.zoom_level_fit_to_text_width, self.zoom_level_fit_to_height)
                if lvl is not None
            )
        finally:
            self._in_update_dynamic_levels = False

    def update_fit_to_width(self):
        self.zoom_level_fit_to_width = self.view.get_allocated_width() / (self.preview.page_width * self.preview.layout.hidpi_factor)

    def update_fit_to_text_width(self):
        self.zoom_level_fit_to_text_width = self.zoom_level_fit_to_width * (self.preview.page_width / (self.preview.page_width - 2 * self.preview.vertical_margin))

    def update_fit_to_height(self):
        self.zoom_level_fit_to_height = (self.view.stack.get_allocated_height() + self.preview.layout.border_width) / (self.preview.page_height * self.preview.layout.hidpi_factor)

    def set_zoom_fit_to_height(self):
        self.set_zoom_level_auto_offset(self.zoom_level_fit_to_height)

    def set_zoom_fit_to_text_width(self):
        if self.zoom_level_fit_to_text_width != None:
            self.set_zoom_level_auto_offset(self.zoom_level_fit_to_text_width)
        else:
            self.set_zoom_level_auto_offset(1.0)
            self.zoom_set = False
        # 缩放已正确，但页面可能靠左显示（文字未横向居中，需手动拖滚动条）。
        # 此处把水平滚动偏移居中到文字内容中心。居中必须在 ScrolledWindow
        # 完成尺寸分配之后才能正确生效——set_zoom_level 内部通过
        # on_layout_changed → set_content_width 改变画布尺寸，但 hadjustment
        # 上界要等下一帧分配才更新，立即设置 offset 会被旧上界错误钳制。
        # 故用 idle 延后到布局稳定后执行（若有连续多次点击，先取消上次的）。
        if self._center_text_idle_id is not None:
            GLib.source_remove(self._center_text_idle_id)
        self._center_text_idle_id = GLib.idle_add(self._center_text_horizontally_idle)

    def _center_text_horizontally_idle(self):
        self._center_text_idle_id = None
        self.center_text_horizontally()
        return False

    def center_text_horizontally(self):
        '''把 PDF 预览的水平滚动偏移设为文字内容（页面）中心。

        绘制时页面左边缘位于画布 canvas_x = horizontal_margin，文字内容左右各
        内缩 vertical_margin（PDF 点数），左右页边距对称，故文字中心 == 页面中心。
        缩放使文字内容宽度恰好铺满可视视口时，居中文字 == 居中页面。'''
        layout = self.preview.layout
        if layout is None or self.zoom_level is None:
            return

        window_width = self.view.get_allocated_width()
        scale_factor = layout.scale_factor
        h_margin = max((window_width - layout.page_width) / 2, 0)
        text_left_canvas = h_margin + self.preview.vertical_margin * scale_factor
        text_width_canvas = (self.preview.page_width - 2 * self.preview.vertical_margin) * scale_factor

        viewport_width = self.view.content.adjustment_x.get_page_size()
        if viewport_width <= 0:
            viewport_width = window_width

        x = text_left_canvas + text_width_canvas / 2 - viewport_width / 2
        x = max(x, 0)
        self.preview.scroll_to_position(x, self.view.content.scrolling_offset_y)

    def set_zoom_fit_to_width(self):
        if self.zoom_level_fit_to_width != None:
            self.set_zoom_level(self.zoom_level_fit_to_width)
        else:
            self.set_zoom_level(1.0)
            self.zoom_set = False

    def set_zoom_fit_to_width_auto_offset(self):
        if self.zoom_level_fit_to_width != None:
            zoom_level = self.zoom_level_fit_to_width
        else:
            zoom_level = 1.0
            self.zoom_set = False
        self.set_zoom_level_auto_offset(zoom_level)

    def zoom_in(self):
        try:
            zoom_level = min([level for level in self.get_list_of_zoom_levels() if level > self.zoom_level])
        except ValueError:
            zoom_level = max(self.get_list_of_zoom_levels())
        self.set_zoom_level_auto_offset(zoom_level)

    def zoom_out(self):
        try:
            zoom_level = max([level for level in self.get_list_of_zoom_levels() if level < self.zoom_level])
        except ValueError:
            # 已达最小缩放级别（无更小的级别可选）。回退到 min(levels)——
            # 通常等于当前 zoom_level，set_zoom_level_auto_offset → set_zoom_level
            # 内 `if level == self.zoom_level: return` 会直接返回，即 no-op。
            # 这是有意行为：缩到最小后再按缩小不应循环到最大或报错。
            # 原代码此处误写 self.zoom_levels（未定义属性）会抛 AttributeError，
            # 已修正为 self.get_list_of_zoom_levels()，与 zoom_in 对应分支一致。
            zoom_level = min(self.get_list_of_zoom_levels())
        self.set_zoom_level_auto_offset(zoom_level)

    def get_list_of_zoom_levels(self):
        zoom_levels = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
        if self.zoom_level_fit_to_width != None:
            zoom_levels.append(self.zoom_level_fit_to_width)
        if self.zoom_level_fit_to_text_width != None:
            zoom_levels.append(self.zoom_level_fit_to_text_width)
        if self.zoom_level_fit_to_height != None:
            zoom_levels.append(self.zoom_level_fit_to_height)
        return zoom_levels

    def set_zoom_level_auto_offset(self, zoom_level):
        layout = self.preview.layout
        if layout == None or self.zoom_level == None:
            # 首次设置缩放（zoom_level 仍为 None）或布局尚未建立时，
            # 无法计算偏移量，直接设置级别即可。
            self.set_zoom_level(zoom_level)
            return
        factor = zoom_level / self.zoom_level

        x = factor * self.view.content.scrolling_offset_x + (factor - 1) * self.view.content.width / 2
        prev_pages = self.view.content.scrolling_offset_y // (layout.page_height + layout.page_gap)
        y = (1 - factor) * prev_pages * layout.page_gap + factor * self.view.content.scrolling_offset_y

        self.set_zoom_level(zoom_level)
        self.preview.scroll_to_position(x, y)

    def set_zoom_level(self, level):
        if level == None: return
        if level == self.zoom_level: return
        if level > 4.0: level = 4.0
        if level < 0.25: level = 0.25

        self.zoom_level = level

        self.preview.layout = self.preview.layouter.create_layout()
        self.preview.add_change_code('layout_changed')
        # 仅在非递归调用时更新动态缩放级别——update_dynamic_zoom_levels
        # 内部可能调用 set_zoom_fit_to_width_auto_offset → set_zoom_level，
        # 递归调用会导致多余的布局重建。递归路径中的 set_zoom_level 仍会
        # 设置 zoom_level + 创建 layout，只是跳过再次 update_dynamic。
        if not self._in_update_dynamic_levels:
            self.update_dynamic_zoom_levels()

        self.zoom_set = True
        self.add_change_code('zoom_level_changed')

    def get_zoom_level(self):
        return self.zoom_level



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
        # “fit to text width 后水平居中文字”的目标滚动位置(详见
        # _apply_center_text_horizontally：缩放后先把 hadjustment 上界设为已知的新
        # 画布宽度，再同步设好滚动位置，首帧即居中，无闪烁也无需手动滚动)。
        self._center_text_target_x = None
        # 递归保护：update_dynamic_zoom_levels 内部可能调用
        # set_zoom_fit_to_width_auto_offset → set_zoom_level → update_dynamic_zoom_levels，
        # 此标志防止递归调用导致多余的布局重建。
        self._in_update_dynamic_levels = False
        # 缩放停靠点缓存：on_zoom_request 原每次 Ctrl+滚轮都重建 3 元素列表
        # + `in` 线性扫描。fit_to_* 级别仅在 update_dynamic_zoom_levels 后变化，
        # 故在那里缓存为 tuple，on_zoom_request 直接读取，tuple 的 `in` 也快于 list。
        self._stopping_points = ()
        # 当前缩放模式：'fit_to_width' / 'fit_to_text_width' / 'fit_to_height' /
        # 'manual'。重启/重编译/缩放窗口后据此重新推导级别，并在 fit_to_text_width
        # 时重新水平居中，使“文字居中”这一偏好可持久化且始终正确。
        self.zoom_mode = 'fit_to_width'
        # 文档状态恢复时暂存的滚动位置 (x, y, mode)，待首帧布局就绪后在
        # on_layout_changed 中一次性应用：fit_to_text_width 仅恢复 y（x 由居中决定），
        # 其余模式同时恢复 x/y。
        self._restore_pending = None
        self.preview.connect('layout_changed', self.on_layout_changed)

    def update_dynamic_zoom_levels(self):
        if self.preview.layout == None: return
        if self.view.get_allocated_width() < 300: return

        old_level = self.zoom_level_fit_to_width

        self._in_update_dynamic_levels = True
        try:
            self.update_fit_to_width()
            self.update_fit_to_text_width()
            self.update_fit_to_height()

            # 依据当前缩放模式重新推导级别并（必要时）居中。fit 模式因此能在
            # 布局就绪后（首次绘制、重编译、缩放窗口）始终保持正确，重启也能恢复；
            # 手动模式（'manual'）的级别保持用户设定的绝对值，不被覆盖。
            if self.zoom_mode == 'fit_to_text_width':
                self.set_zoom_fit_to_text_width()
            elif self.zoom_mode == 'fit_to_height':
                self.set_zoom_fit_to_height()
            elif self.zoom_mode == 'fit_to_width':
                self.set_zoom_fit_to_width_auto_offset()
            else:
                # 手动模式：级别已由 set_zoom_level 设好，绝不能在此覆盖
                # （否则首次 set_zoom_level 时 zoom_set 仍为 False，会误调用
                # set_zoom_fit_to_width 把 'manual' 模式冲回 'fit_to_width'）。
                pass

            if not self.zoom_set:
                self.zoom_set = True

            # fit_to_* 级别此刻已最终确定，缓存停靠点供 on_zoom_request 读取。
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
        self.zoom_mode = 'fit_to_height'
        self.set_zoom_level_auto_offset(self.zoom_level_fit_to_height)

    def set_zoom_fit_to_text_width(self):
        self.zoom_mode = 'fit_to_text_width'
        if self.zoom_level_fit_to_text_width != None:
            self.set_zoom_level_auto_offset(self.zoom_level_fit_to_text_width)
        else:
            self.set_zoom_level_auto_offset(1.0)
            self.zoom_set = False
        # 居中需在 ScrolledWindow 完成尺寸分配（hadjustment 上界更新到新画布宽度）
        # 之后进行，故 center_text_horizontally 内部先把上界设为已知的新画布宽度再
        # 同步滚动，首帧即居中，无闪烁也无需手动滚动。
        self.center_text_horizontally()

    def on_layout_changed(self, *args):
        '''布局（重）建立后，按缩放模式恢复正确的显示。

        - fit_to_text_width：把文字区域水平居中（依赖视口宽度，故每次 relayout
          都需重算，否则重启/重编译/缩放窗口后会偏左）。
        - 其余模式：不额外处理（fit_to_width/fit_to_height 的页面本身已居中）。
        最后，若文档状态恢复时暂存了滚动位置（_restore_pending），一次性应用之：
        fit_to_text_width 仅恢复垂直位置（水平由居中决定），其余模式同时恢复
        水平/垂直位置。'''
        if self.preview.layout is None:
            return
        if self.zoom_mode == 'fit_to_text_width':
            self.center_text_horizontally()
        if self._restore_pending is not None:
            x, y, mode = self._restore_pending
            self._restore_pending = None
            if mode == 'fit_to_text_width':
                # 水平已居中，仅恢复垂直滚动位置。
                self.preview.scroll_to_position(self.view.content.scrolling_offset_x, y)
            else:
                self.preview.scroll_to_position(x, y)

    def center_text_horizontally(self):
        '''把 PDF 预览的水平滚动偏移居中到文字内容（页面）中心。

        绘制时页面左边缘位于画布 canvas_x = horizontal_margin，文字内容左右各
        内缩 vertical_margin（PDF 点数），左右页边距对称，故文字中心 == 页面中心。
        缩放使文字内容宽度恰好铺满可视视口时，居中文字 == 居中页面。'''
        layout = self.preview.layout
        if layout is None or self.zoom_level is None:
            return

        # 用实际可视视口宽度（而非外层盒子宽度，后者含 .preview-card 约 6px 的
        # 左右外边距）计算。绘制时 page 左边界正是按该视口宽度求出的 margin，
        # 两者一致才能保证文字区域真正水平居中，而非偏左/偏右若干像素。
        viewport_width = self.view.content.adjustment_x.get_page_size()
        if viewport_width <= 0:
            viewport_width = self.view.get_allocated_width()
        margin = layout.get_horizontal_margin(viewport_width)
        # 文字在 page 内左右对称（vertical_margin 同时作用于两侧），故 page 中心
        # 即文字中心。把该中心对齐到视口中心即完成居中。
        page_center = margin + layout.page_width / 2
        target_x = max(page_center - viewport_width / 2, 0)

        self._center_text_target_x = target_x
        self._apply_center_text_horizontally()

    def _apply_center_text_horizontally(self):
        if self._center_text_target_x is None:
            return
        adj = self.view.content.adjustment_x
        # 缩放已改变画布宽度，但 ScrolledWindow 的 hadjustment 上界要等布局阶段
        # 重新分配后才更新。若此刻直接设滚动值，会被旧上界钳制（表现为"只缩放不
        # 居中"，或需手动滚动一下 'changed' 才补上）。此处先把上界设为已知的新
        # 画布宽度（与即将到来的布局结果一致），滚动值便不再被钳制；随后布局时
        # ScrolledWindow 也会把它设成同一个值，无冲突。整个过程同步完成，首帧即
        # 居中，既不闪烁也无需再滚动。
        new_upper = max(self.preview.layout.canvas_width, adj.get_upper())
        adj.set_upper(new_upper)
        max_scroll = max(new_upper - adj.get_page_size(), 0)
        target = min(self._center_text_target_x, max_scroll)
        self._center_text_target_x = None
        self.preview.scroll_to_position(target, self.view.content.scrolling_offset_y)

    def set_zoom_fit_to_width(self):
        self.zoom_mode = 'fit_to_width'
        if self.zoom_level_fit_to_width != None:
            self.set_zoom_level(self.zoom_level_fit_to_width)
        else:
            self.set_zoom_level(1.0)
            self.zoom_set = False

    def set_zoom_fit_to_width_auto_offset(self):
        self.zoom_mode = 'fit_to_width'
        if self.zoom_level_fit_to_width != None:
            zoom_level = self.zoom_level_fit_to_width
        else:
            zoom_level = 1.0
            self.zoom_set = False
        self.set_zoom_level_auto_offset(zoom_level)

    def zoom_in(self):
        # 缩放档位是手动缩放，脱离任何 fit 模式。
        self.zoom_mode = 'manual'
        current = self.zoom_level
        if current is None:
            self.set_zoom_level_auto_offset(min(self.get_list_of_zoom_levels()))
            return
        larger = [level for level in self.get_list_of_zoom_levels() if level > current]
        if not larger:
            # 已在最大档：不再静默无效缩放，给上层一个机会提示用户。
            self.add_change_code('zoom_clamped', 'in')
            return
        self.set_zoom_level_auto_offset(min(larger))

    def zoom_out(self):
        # 缩放档位是手动缩放，脱离任何 fit 模式。
        self.zoom_mode = 'manual'
        current = self.zoom_level
        if current is None:
            return
        smaller = [level for level in self.get_list_of_zoom_levels() if level < current]
        if not smaller:
            # 已在最小档：不再静默无效缩放，给上层一个机会提示用户。
            self.add_change_code('zoom_clamped', 'out')
            return
        self.set_zoom_level_auto_offset(max(smaller))

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
        # 注意：本方法不再自行设置 zoom_mode。它既被 fit 设置器
        # （set_zoom_fit_to_*）复用以保持“fit 模式”不变，也被真正的手动缩放
        # 入口（zoom_in / zoom_out / popover 选百分比）调用。手动语义由那些
        # 调用方负责设置 zoom_mode = 'manual'，否则 fit 模式会被错误地冲掉。
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



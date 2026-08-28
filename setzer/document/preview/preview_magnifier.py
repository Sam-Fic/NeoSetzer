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

'''PDF 预览放大镜（issue #398/#238 v1：按住左键放大）。

模块分两层：

- 纯函数 ``compute_magnifier_params`` / ``compute_magnifier_placement``：
  渲染参数推导与浮窗定位（含视口边缘翻转），不依赖 GTK，可单测。
- ``PreviewMagnifier(Gtk.DrawingArea)``：圆形浮窗本体。作为画布坐标系
  的 overlay 子控件挂到 ScrollingWidget 的定位层（Gtk.Fixed）上
  （add_overlay_widget），定位经 positioner 注入的 move_overlay_widget
  （画布坐标，float，无 Gtk margin 的 int16 上限，随滚动平移）。

语义：旁置式——浮窗摆在光标右下（贴近视口边缘时翻转），玻璃圆心
显示「光标处」的内容；圆心十字标记被跟踪点。（曾试验「圆心即光标+
中心镂空」的居中样式，用户实测仍报偏移且偏好旁置观感，故回退。）

关键约束：浮窗必须 set_can_target(False)，否则它会拦截指针事件，
破坏按住期间 motion / click 控制器的跟手与释放检测。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
import cairo
import math

from setzer.app.color_manager import ColorManager
from setzer.document.preview.magnifier_geometry import (
    MAGNIFICATION_FACTOR,
    apply_magnifier_transform,
    compute_magnifier_params,
    compute_magnifier_placement,
)


DEFAULT_DIAMETER = 240


class PreviewMagnifier(Gtk.DrawingArea):
    '''圆形放大镜浮窗：cairo 圆形 clip 内贴放大渲染的局部页面 surface，
    圆心十字标记被跟踪点（光标处内容）。

    生命周期由 PreviewController 驱动：present_at() 定位并显示，
    set_magnified_surface() 更新内容，dismiss() 隐藏并丢弃内容。'''

    def __init__(self, diameter=DEFAULT_DIAMETER):
        super().__init__()
        self.diameter = diameter
        self._surface = None
        # 画布定位函数（ScrollingWidget.move_overlay_widget），由 PreviewView
        # 在 add_overlay_widget 之后注入。Gtk margin 是 gint16（上限
        # 32767px），长 PDF 高缩放下画布坐标会超限导致定位失败。
        self._positioner = None

        self.set_content_width(diameter)
        self.set_content_height(diameter)
        # 左上角放在画布坐标 (x, y)，与页码徽章同一定位范式（Gtk.Fixed）。
        self.set_can_focus(False)
        # 关键：不参与输入。浮窗常悬停在光标附近，若可命中会吞掉 motion/
        # release 事件，导致放大镜抖动或无法松开消失。
        self.set_can_target(False)
        self.set_visible(False)
        self.set_draw_func(self._draw)

    def set_positioner(self, positioner):
        '''注入画布定位函数（ScrollingWidget.move_overlay_widget）。

        Gtk margin 是 gint16（上限 32767px），长 PDF 高缩放下画布坐标会
        超限导致定位失败；positioner 用 Gtk.Fixed 的 float 坐标无此限制。'''
        self._positioner = positioner

    def present_at(self, x, y):
        '''把浮窗左上角放到画布坐标 (x, y) 并显示。'''
        if self._positioner is not None:
            self._positioner(self, x, y)
        else:
            # 兜底：positioner 未注入时退回 margin 定位（短文档无影响）。
            self.set_margin_start(int(round(x)))
            self.set_margin_top(int(round(y)))
        self.set_visible(True)

    def set_magnified_surface(self, surface):
        '''更新放大内容（renderer 产出的方形 ImageSurface）并重绘。'''
        if surface is None or not isinstance(surface, cairo.ImageSurface):
            return
        self._surface = surface
        self.queue_draw()

    def dismiss(self):
        '''隐藏浮窗并丢弃内容引用（surface 约 0.9MB，及时释放）。'''
        if not self.get_visible() and self._surface is None:
            return
        self._surface = None
        self.set_visible(False)

    def _draw(self, area, ctx, width, height):
        radius = min(width, height) / 2 - 1
        if radius <= 0:
            return
        ctx.save()
        ctx.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        ctx.clip()

        # 底色：surface 未就绪的第一帧或裁剪区超出页面边缘时露出白色纸面，
        # 与 PDF 页面底色一致（recolor 模式下的深色底已烘进 surface 像素）。
        ctx.set_source_rgba(1, 1, 1, 1)
        ctx.paint()

        if self._surface is not None:
            surface_size = self._surface.get_width()
            if surface_size > 0:
                # surface 是 hidpi 加密的设备像素（S = D × hidpi），要铺满
                # D 逻辑像素 ⇒ 缩放系数 = D/S = 1/hidpi。⚠ 不能写成 S/D：
                # set_source_surface 按「1 surface px = 1 用户单位」铺图，
                # 先放大再贴会把 surface 铺成 S×(S/D) 逻辑像素——玻璃只
                # 露出左上四分之一，表现为「内容放大 4 倍 + 十字显示按点
                # 左上方内容」的固定偏移（v1 起就存在的真 bug，曾误排查
                # 坐标链很久）。
                ctx.scale(width / surface_size, height / surface_size)
                ctx.set_source_surface(self._surface, 0, 0)
                ctx.paint()
        ctx.restore()

        # 描边：外圈半透明黑保证任意底色上的轮廓对比度，内圈主题边框色收边。
        border = ColorManager.get_ui_color('borders')
        ctx.set_source_rgba(0, 0, 0, 0.35)
        ctx.set_line_width(1)
        ctx.arc(width / 2, height / 2, radius, 0, 2 * math.pi)
        ctx.stroke()
        ctx.set_source_rgba(border.red, border.green, border.blue, border.alpha)
        ctx.arc(width / 2, height / 2, max(radius - 1, 0), 0, 2 * math.pi)
        ctx.stroke()

        # 圆心十字标记：旁置式下玻璃圆心显示的是「光标处」的内容。
        fg = ColorManager.get_ui_color('view_fg_color')
        ctx.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
        arm = 5
        center_x, center_y = width / 2, height / 2
        ctx.move_to(center_x - arm, center_y)
        ctx.line_to(center_x + arm, center_y)
        ctx.move_to(center_x, center_y - arm)
        ctx.line_to(center_x, center_y + arm)
        ctx.stroke()

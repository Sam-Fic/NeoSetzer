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
  的 overlay 子控件挂到 ScrollingWidget 的 canvas 上（add_overlay_widget），
  定位用 margin_start / margin_top（画布坐标，随滚动平移）。

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


MAGNIFICATION_FACTOR = 2.0
DEFAULT_DIAMETER = 240


def compute_magnifier_params(diameter_css, hidpi_factor, layout_scale_factor, factor=MAGNIFICATION_FACTOR):
    '''由浮窗直径推导局部渲染的全部几何参数。

    单位链：PDF 点 --(scale_factor=zoom*hidpi)--> 画布 css px --(hidpi)--> 设备 px。
    放大镜把光标周围 region_css 见方的画布区域放大 factor 倍铺满直径
    diameter_css 的圆窗，因此：

    - region_css   = diameter / factor        （可见画布区域边长，css px）
    - region_pt    = region_css / layout_scale_factor （对应 PDF 点数）
    - density      = surface_px / region_pt = factor * scale * hidpi
                   （渲染密度：设备 px / PDF 点，是整页渲染的 factor 倍）
    - surface_px   = diameter * hidpi         （输出方形 surface 边长）

    参数:
        diameter_css: 浮窗直径（css px）
        hidpi_factor: 显示器缩放（view.get_scale_factor()）
        layout_scale_factor: layout.scale_factor（含 hidpi）
        factor: 相对当前显示的放大倍数
    返回:
        dict(region_css, region_pt, density, surface_px)
    '''
    region_css = diameter_css / factor
    region_pt = region_css / layout_scale_factor
    surface_px = diameter_css * hidpi_factor
    density = factor * layout_scale_factor * hidpi_factor
    return {
        'region_css': region_css,
        'region_pt': region_pt,
        'density': density,
        'surface_px': surface_px,
    }


def compute_magnifier_placement(cursor_x, cursor_y, diameter, viewport_x, viewport_y, viewport_w, viewport_h, gap=14.0):
    '''计算浮窗左上角的画布坐标：默认在光标右下方 gap 处；越出视口右/下缘
    时翻到光标左/上方；翻转后仍越界（贴角）则 clamp 进视口。

    全部坐标为画布坐标系（与 overlay 子控件的 margin 同系）。viewport_*
    为当前视口的画布坐标矩形（scrolling_offset + viewport size）。纯函数，
    无 GTK 依赖。
    '''
    x = cursor_x + gap
    if x + diameter > viewport_x + viewport_w:
        x = cursor_x - gap - diameter
    y = cursor_y + gap
    if y + diameter > viewport_y + viewport_h:
        y = cursor_y - gap - diameter
    if x < viewport_x:
        x = viewport_x
    if y < viewport_y:
        y = viewport_y
    return (x, y)


def apply_magnifier_transform(ctx, size_px, density, rotation, center_x_pt, center_y_pt):
    '''把 ctx 变换为「top-down 页面点坐标 → 浮窗 surface 设备像素」。

    输入坐标约定（易错点，有单测锁定）：center_x_pt / center_y_pt 是布局
    映射 get_page_number_and_offsets_by_document_offsets 返回的页面内
    top-down 点坐标（原点=页面左上角，y 向下）。

    变换自外向内：surface 中心 ← 旋转(与 presenter 画整页纹理一致，
    同为 ctx.rotate 正角) ← density 密度 ← 裁剪中心平移。

    ⚠ 不要在此处再做 y 翻转：page.render(ctx) 内部自带一次「PDF y-up →
    top-down」翻转（主渲染路径仅用正缩放即可出正立页面正是依赖它），
    外层再翻会双重翻转、内容上下镜像。同理，本函数返回后 ctx 仍消费
    top-down 坐标，手动绘制（如白底矩形）直接用 top-down 即可。'''
    ctx.translate(size_px / 2, size_px / 2)
    if rotation:
        ctx.rotate(math.radians(rotation))
    ctx.scale(density, density)
    ctx.translate(-center_x_pt, -center_y_pt)


class PreviewMagnifier(Gtk.DrawingArea):
    '''圆形放大镜浮窗：cairo 圆形 clip 内贴放大渲染的局部页面 surface，
    圆心十字标记被跟踪点（光标处内容）。

    生命周期由 PreviewController 驱动：present_at() 定位并显示，
    set_magnified_surface() 更新内容，dismiss() 隐藏并丢弃内容。'''

    def __init__(self, diameter=DEFAULT_DIAMETER):
        super().__init__()
        self.diameter = diameter
        self._surface = None

        self.set_content_width(diameter)
        self.set_content_height(diameter)
        # START 对齐 + margin_* 即「左上角放在 (margin_start, margin_top)」，
        # 与页码徽章同一定位范式（画布坐标系）。
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_can_focus(False)
        # 关键：不参与输入。浮窗常悬停在光标附近，若可命中会吞掉 motion/
        # release 事件，导致放大镜抖动或无法松开消失。
        self.set_can_target(False)
        self.set_visible(False)
        self.set_draw_func(self._draw)

    def present_at(self, x, y):
        '''把浮窗左上角放到画布坐标 (x, y) 并显示。'''
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

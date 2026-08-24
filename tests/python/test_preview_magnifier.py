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

'''放大镜纯逻辑单测：渲染参数推导、浮窗定位（边缘翻转）、request_id
过期判定、controller 更新路径坐标绑定。除 controller 路径用桩对象外
均不依赖 GUI 环境。'''

import queue
import math
import unittest

import cairo
import numpy as np

from setzer.document.preview.preview_magnifier import (
    MAGNIFICATION_FACTOR,
    PreviewMagnifier,
    apply_magnifier_transform,
    compute_magnifier_params,
    compute_magnifier_placement,
)


class MagnifierParamsTest(unittest.TestCase):
    '''compute_magnifier_params：单位链与密度推导。'''

    def test_density_is_factor_times_full_render_density(self):
        # 整页渲染密度 = scale_factor * hidpi（设备 px / PDF 点）；
        # 放大镜必须是它的 factor 倍，才能呈现 factor 倍视觉放大。
        params = compute_magnifier_params(240, 2.0, 2.0)
        self.assertAlmostEqual(params['density'], MAGNIFICATION_FACTOR * 2.0 * 2.0)

    def test_region_shrinks_by_factor(self):
        params = compute_magnifier_params(240, 1.0, 1.5)
        self.assertAlmostEqual(params['region_css'], 120.0)
        self.assertAlmostEqual(params['region_pt'], 80.0)

    def test_surface_matches_diameter_times_hidpi(self):
        params = compute_magnifier_params(240, 2.0, 0.5)
        self.assertEqual(params['surface_px'], 480)

    def test_identity_chain_round_trip(self):
        '''surface_px == region_pt * density（渲染参数自洽）。'''
        for hidpi in (1.0, 1.5, 2.0):
            for scale in (0.25, 1.0, 4.0):
                params = compute_magnifier_params(240, hidpi, scale)
                self.assertAlmostEqual(
                    params['region_pt'] * params['density'],
                    params['surface_px'], places=6)


class MagnifierPlacementTest(unittest.TestCase):
    '''compute_magnifier_placement：默认右下、越界翻转、贴角 clamp。'''

    def test_default_places_below_right_of_cursor(self):
        x, y = compute_magnifier_placement(500, 500, 240, 400, 400, 800, 600)
        self.assertGreater(x, 500)
        self.assertGreater(y, 500)

    def test_flips_left_near_right_viewport_edge(self):
        # 光标贴近视口右缘：右下放不下 → 翻到光标左侧。
        cursor_x, cursor_y = 760, 100
        vp_x, vp_y, vp_w, vp_h = 0, 0, 800, 600
        x, y = compute_magnifier_placement(cursor_x, cursor_y, 240, vp_x, vp_y, vp_w, vp_h)
        self.assertLess(x + 240, vp_x + vp_w)
        self.assertLess(x, cursor_x)

    def test_flips_up_near_bottom_viewport_edge(self):
        cursor_x, cursor_y = 100, 560
        x, y = compute_magnifier_placement(cursor_x, cursor_y, 240, 0, 0, 800, 600)
        self.assertLess(y + 240, 600)
        self.assertLess(y, cursor_y)

    def test_corner_clamps_inside_viewport(self):
        # 光标贴右下角：翻转后仍越出左/上缘时 clamp 进视口。
        x, y = compute_magnifier_placement(790, 590, 240, 0, 0, 800, 600)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 240, 800)
        self.assertLessEqual(y + 240, 600)

    def test_placement_tracks_viewport_origin(self):
        # 滚动后视口原点非零：定位仍相对视口而非画布原点。
        x, y = compute_magnifier_placement(1050, 50, 240, 1000, 0, 200, 600)
        self.assertGreaterEqual(x, 1000)


class MagnifierTransformTest(unittest.TestCase):
    '''apply_magnifier_transform 坐标约定（回归锁定）。

    约定：变换矩阵消费布局映射返回的 top-down 页面点坐标（原点=页面
    左上角，y 向下）；page.render 内部自带 PDF y-up → top-down 翻转，
    因此矩阵本身绝不能再翻——曾因外层多加一次翻转导致内容上下镜像
    （v2 回归），也曾误判需要翻转而改错方向。本组用例直接喂 top-down
    点锁死：光标点落 surface 中心、上方内容出现在中心上方。'''

    SIZE = 240
    DENSITY = 4.0  # 设备 px / PDF 点

    def _device_of(self, rotation, td_points, center_td=(100.0, 200.0)):
        surface = cairo.ImageSurface(cairo.Format.ARGB32, self.SIZE, self.SIZE)
        ctx = cairo.Context(surface)
        apply_magnifier_transform(ctx, float(self.SIZE), self.DENSITY,
                                  rotation, center_td[0], center_td[1])
        return [ctx.user_to_device(x, y) for x, y in td_points]

    def test_cursor_point_lands_at_surface_center(self):
        (dx, dy) = self._device_of(0, [(100.0, 200.0)])[0]
        self.assertAlmostEqual(dx, self.SIZE / 2, places=6)
        self.assertAlmostEqual(dy, self.SIZE / 2, places=6)

    def test_content_above_cursor_renders_above_center(self):
        # top-down y 更小 = 页面上更靠上 = 浮窗内 device y 更小。
        above, below = self._device_of(0, [(100.0, 190.0), (100.0, 210.0)])
        self.assertLess(above[1], self.SIZE / 2)
        self.assertGreater(below[1], self.SIZE / 2)

    def test_magnification_scales_distances_by_density(self):
        # 光标右侧 10pt 的点距中心应恰为 10pt × density 设备像素。
        (dx, dy) = self._device_of(0, [(110.0, 200.0)])[0]
        self.assertAlmostEqual(dx - self.SIZE / 2, 10 * self.DENSITY, places=6)

    def test_rotation_matches_presenter_convention(self):
        # presenter 用 ctx.rotate(radians(rotation)) 画整页纹理；放大镜
        # 同款旋转下，光标右侧的点旋转 90° 后应出现在中心正下方
        # （cairo rotate 正角在 y-down 设备空间为顺时针）。
        (dx, dy) = self._device_of(90, [(110.0, 200.0)])[0]
        self.assertAlmostEqual(dy - self.SIZE / 2, 10 * self.DENSITY, places=6)
        self.assertAlmostEqual(dx, self.SIZE / 2, places=6)

    def test_matrix_consumes_top_down_not_native_y_up(self):
        # 回归警示：若把 PDF 原生 y-up 值直接喂给矩阵（即多做了一次
        # 翻转的效果），top-down (100,600)、页高 800 的点会错位到镜像处
        # 而非按 top-down 语义落在中心下方 400pt×density 处。
        surface = cairo.ImageSurface(cairo.Format.ARGB32, self.SIZE, self.SIZE)
        ctx = cairo.Context(surface)
        apply_magnifier_transform(ctx, float(self.SIZE), self.DENSITY,
                                  0, 100.0, 200.0)
        dx, dy = ctx.user_to_device(100.0, 600.0)
        self.assertAlmostEqual(dx, self.SIZE / 2, places=6)
        self.assertAlmostEqual(dy - self.SIZE / 2, 400 * self.DENSITY, places=6)


class MagnifierUpdatePathTest(unittest.TestCase):
    '''PreviewController._update_magnifier 调用路径回归。

    用 object.__new__ 绕过 __init__（避免拉起完整 preview/widget 对象图），
    以最小桩验证 press / motion / 离开视口三条路径的坐标绑定与分发。
    背景：曾因 press 路径只传 doc_x 不传 doc_y，函数内 pos=(doc_x, doc_y)
    抛 UnboundLocalError——Observable 吞掉异常仅打印 traceback，表现为
    「按下无放大镜」且无崩溃，只能靠日志发现。'''

    class _Recorder:
        def __init__(self):
            self.calls = list()

        def present_at(self, x, y):
            self.calls.append(('present', x, y))

        def dismiss(self):
            self.calls.append(('dismiss',))

        def set_magnified_surface(self, surface):
            self.calls.append(('surface', surface))

    class _StubRenderer:
        def __init__(self):
            self.magnified_pages_queue = queue.Queue()
            self.requests = list()

        def request_magnifier_render(self, *arguments):
            self.requests.append(arguments)
            return len(self.requests)

    def _make_controller(self):
        from types import SimpleNamespace
        from setzer.document.preview.preview_controller import PreviewController

        controller = object.__new__(PreviewController)
        layout = SimpleNamespace(hidpi_factor=2.0, scale_factor=2.0)
        content = SimpleNamespace(
            width=800, height=600,
            scrolling_offset_x=0.0, scrolling_offset_y=0.0,
            cursor_x=None, cursor_y=None,
        )
        magnifier = self._Recorder()
        magnifier.diameter = 240
        controller.view = SimpleNamespace(content=content, magnifier=magnifier)
        renderer = self._StubRenderer()
        controller.preview = SimpleNamespace(
            layout=layout,
            poppler_document=object(),
            rotation=0,
            recolor_pdf=False,
            page_renderer=renderer,
        )
        controller._magnifier_active = True
        controller._magnifier_layout_ref = layout
        controller._magnifier_pending_request_id = 0
        controller._magnifier_last_enqueue_time = 0.0
        controller._magnifier_last_enqueue_pos = None
        controller._MAGNIFIER_DIAMETER = 240
        controller._MAGNIFIER_MIN_INTERVAL_S = 0.0
        controller._MAGNIFIER_MIN_MOVE_PX = 3.0
        return controller

    def test_press_path_binds_both_coordinates(self):
        # 回归：doc_x 有值而 doc_y 未绑定时按下即抛 UnboundLocalError。
        controller = self._make_controller()
        controller._update_magnifier(doc_x=100.0, doc_y=200.0, page_data=(0, 10.0, 20.0))
        self.assertEqual(controller.view.magnifier.calls[0][0], 'present')
        self.assertEqual(len(controller.preview.page_renderer.requests), 1)
        # 入队参数：页号 + top-down 点坐标原样透传。
        request = controller.preview.page_renderer.requests[0]
        self.assertEqual(request[0], 0)
        self.assertAlmostEqual(request[1], 10.0)
        self.assertAlmostEqual(request[2], 20.0)

    def test_motion_path_computes_document_coords_from_cursor(self):
        controller = self._make_controller()
        content = controller.view.content
        content.cursor_x, content.cursor_y = 30.0, 40.0
        layout = controller.preview.layout
        layout.get_page_number_and_offsets_by_document_offsets = (
            lambda x, y, w: (0, 5.0, 5.0))
        controller._update_magnifier()
        request = controller.preview.page_renderer.requests[0]
        self.assertAlmostEqual(request[1], 5.0)

    def test_cursor_outside_viewport_dismisses_but_stays_active(self):
        controller = self._make_controller()
        controller._update_magnifier()  # cursor_* 为 None（leave）
        self.assertEqual(controller.view.magnifier.calls, [('dismiss',)])
        self.assertTrue(controller._magnifier_active)


class MagnifierWidgetDrawTest(unittest.TestCase):
    '''PreviewMagnifier._draw 绘制路径（真实 Gtk widget + cairo.Context）。

    背景：环形 clip 曾因 pycairo 方法名写错（new_subpath → new_sub_path）
    在浮窗首次显示时抛 AttributeError 打穿主循环——_draw 只有真显示
    才会执行，此前的测试从未覆盖。本组用例无头执行两条绘制分支。'''

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk
        Gtk.init()

    def _draw_once(self, magnifier, with_surface):
        if with_surface:
            magnifier.set_magnified_surface(
                cairo.ImageSurface(cairo.Format.ARGB32, 480, 480))
        target = cairo.ImageSurface(cairo.Format.ARGB32, 480, 480)
        magnifier._draw(None, cairo.Context(target), 240, 240)

    def test_draw_without_surface(self):
        self._draw_once(PreviewMagnifier(), with_surface=False)

    def test_draw_with_surface(self):
        self._draw_once(PreviewMagnifier(), with_surface=True)

    def test_draw_paints_center_with_surface(self):
        '''圆形 clip 全区域绘制：中心与环带均不透明（旁置式无镂空）。'''
        magnifier = PreviewMagnifier()
        magnifier.set_magnified_surface(
            cairo.ImageSurface(cairo.Format.ARGB32, 480, 480))
        target = cairo.ImageSurface(cairo.Format.ARGB32, 240, 240)
        magnifier._draw(None, cairo.Context(target), 240, 240)
        data = bytearray(target.get_data())
        stride = target.get_stride()

        def alpha_at(x, y):
            return data[(y * stride) + (x * 4) + 3]  # BGRA 的 alpha 字节

        self.assertEqual(alpha_at(120, 120), 255)
        offset = int(60 / math.sqrt(2))
        self.assertEqual(alpha_at(120 - offset, 120 - offset), 255)
        self.assertEqual(alpha_at(120 + offset, 120 + offset), 255)


    def test_surface_content_lands_centered_at_correct_scale(self):
        '''端到端回归（v1 起的真 bug）：_draw 必须把 hidpi 加密的 surface
        恰好铺满 D 逻辑像素。曾把缩放系数写成 S/D（应为 D/S），导致玻璃
        只露出 surface 左上四分之一——内容放大 4 倍、十字显示按点左上方
        内容的固定偏移。本用例模拟 GTK 的设备缩放（logical 240 = device
        480），把中心带墨迹的 surface 走完整 _draw，像素级测量墨迹质心。'''
        magnifier = PreviewMagnifier()
        surface = cairo.ImageSurface(cairo.Format.ARGB32, 480, 480)
        sctx = cairo.Context(surface)
        sctx.set_source_rgb(0, 0, 0)
        sctx.arc(240, 240, 6, 0, 2 * math.pi)
        sctx.fill()

        target = cairo.ImageSurface(cairo.Format.ARGB32, 480, 480)
        ctx = cairo.Context(target)
        ctx.scale(2, 2)  # GTK：logical 240 → device 480
        magnifier.set_magnified_surface(surface)
        magnifier._draw(None, ctx, 240, 240)

        buf = np.frombuffer(target.get_data(), dtype=np.uint8).reshape(480, 480, 4)
        darkness = 765 - buf[..., 0].astype(int) - buf[..., 1].astype(int) - buf[..., 2].astype(int)
        ys, xs = np.nonzero(darkness > 150)
        self.assertGreater(len(xs), 0, '墨迹完全落在裁剪窗外（缩放系数反向的典型症状）')
        # 质心应在设备正中 (240, 240)，容差 2px（抗锯齿）。
        self.assertAlmostEqual(xs.mean(), 240.0, delta=2.0)
        self.assertAlmostEqual(ys.mean(), 240.0, delta=2.0)


class MagnifierRequestIdTest(unittest.TestCase):
    '''request_id 过期语义：只有「最新」请求的结果被采用。

    直接用 PreviewPageRenderer 会拉起 preview/document 对象图；这里以
    最小桩复刻 request/process 的判定契约（单调取号 + 等值比较），锁定
    「旧 id != 最新 id → 丢弃」这一行为不被回归。'''

    class _StubRenderer:
        def __init__(self):
            self._lock_held = False
            self.latest_request_id = 0

        def assign_request_id(self):
            # 与 request_magnifier_render 相同的取号语义。
            self.latest_request_id += 1
            return self.latest_request_id

        @staticmethod
        def is_stale(request_id, latest_request_id):
            return request_id != latest_request_id

    def test_only_latest_request_survives(self):
        renderer = self._StubRenderer()
        ids = [renderer.assign_request_id() for _ in range(5)]
        latest = renderer.latest_request_id
        stale = [i for i in ids if renderer.is_stale(i, latest)]
        self.assertEqual(stale, ids[:-1])
        self.assertFalse(renderer.is_stale(latest, latest))

    def test_ids_are_monotonic(self):
        renderer = self._StubRenderer()
        first = renderer.assign_request_id()
        second = renderer.assign_request_id()
        self.assertGreater(second, first)


if __name__ == '__main__':
    unittest.main()

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

import ast
import queue
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cairo
import numpy as np

from setzer.document.preview.magnifier_geometry import (
    MAGNIFICATION_FACTOR,
    MAGNIFIER_TILE_FACTOR,
    apply_magnifier_crop_transform,
    apply_magnifier_transform,
    compute_magnifier_params,
    compute_magnifier_placement,
    magnifier_tile_coverage,
)
from setzer.document.preview.preview_magnifier import PreviewMagnifier


RENDERER_SOURCE = (
    Path(__file__).resolve().parents[2] / 'setzer' / 'document' / 'preview'
    / 'preview_page_renderer.py'
)


def _load_renderer_method(name, **namespace):
    '''AST 加载 PreviewPageRenderer 的单个方法（与 test_preview_page_cache
    同范式）：不 import 模块、不拉起 preview/document 对象图。'''
    tree = ast.parse(RENDERER_SOURCE.read_text(encoding='utf-8'))
    renderer = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef)
                    and node.name == 'PreviewPageRenderer')
    method = next(node for node in renderer.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace.setdefault('math', math)
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(RENDERER_SOURCE), 'exec'),
         namespace)
    return namespace[name]


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
            self.reply = None

        def get_magnifier_surface(self, *arguments):
            self.requests.append(arguments)
            return self.reply

    def _make_controller(self):
        from types import SimpleNamespace
        from setzer.document.preview.preview_controller import PreviewController

        controller = object.__new__(PreviewController)
        layout = SimpleNamespace(
            hidpi_factor=2.0, scale_factor=2.0,
            page_width_original=100.0, page_height_original=200.0)
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
            pdf_version=1,
            rotation=0,
            recolor_pdf=False,
            page_renderer=renderer,
        )
        controller._magnifier_active = True
        controller._magnifier_layout_ref = layout
        controller._magnifier_last_update_time = 0.0
        controller._magnifier_last_update_pos = None
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
        # 裁剪参数：页号 + top-down 点坐标原样透传。
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

    def test_produced_surface_is_forwarded_to_the_lens_widget(self):
        controller = self._make_controller()
        renderer = controller.preview.page_renderer
        renderer.reply = ('sentinel-surface',)
        controller._update_magnifier(doc_x=100.0, doc_y=200.0, page_data=(0, 10.0, 20.0))
        self.assertEqual(controller.view.magnifier.calls[0],
                         ('surface', renderer.reply))
        self.assertEqual(controller.view.magnifier.calls[1][0], 'present')


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


class MagnifierTileCoverageTest(unittest.TestCase):
    '''magnifier_tile_coverage：瓦片覆盖判定的 pt 尺度（纯函数）。

    背景：旧整页缓存方案按「页面 pt 面积 × density²」算像素上限，但调用
    方传入的是随缩放变化的 css 尺寸，面积被多乘 scale²，hidpi=2 且缩放
    ≥ ~111% 时密度被 clamp（缩放 ≥ ~156% 时 clamp 到整页密度 → 放大倍率
    恰为 1×，表现为「根本没有放大」）。瓦片方案的像素量恒为
    factor² × 镜面像素量（与缩放无关），密度永不需要 clamp。'''

    def test_tile_half_is_factor_times_lens_half(self):
        tile_half, lens_half, threshold = magnifier_tile_coverage(480, 4.0)
        self.assertAlmostEqual(lens_half, 480 / (2 * 4.0))
        self.assertAlmostEqual(tile_half, MAGNIFIER_TILE_FACTOR * lens_half)
        self.assertAlmostEqual(threshold, tile_half - 2 * lens_half)

    def test_threshold_stays_positive_and_leaves_a_lens_window_of_slack(self):
        # 重建阈值比硬边界（tile_half − lens_half）提前一个镜头窗口，
        # 给异步构建留软帧过渡余量；阈值必须为正（factor ≥ 4）。
        for density in (2.0, 4.0, 8.0, 16.0):
            tile_half, lens_half, threshold = magnifier_tile_coverage(480, density)
            self.assertGreater(threshold, 0)
            self.assertAlmostEqual(threshold, tile_half - 2 * lens_half)
            self.assertLess(threshold, tile_half - lens_half)

    def test_travel_between_rebuilds_is_zoom_independent_in_css(self):
        # 两次重建间的光标行程（pt）× scale = 常量 css：密度与缩放同比例
        # 增长，行程的 css 长度不随缩放变化（= size_css × (factor−2)/2…）。
        # 这里验证 pt 行程随密度反比缩放。
        _, _, t1 = magnifier_tile_coverage(480, 4.0)
        _, _, t2 = magnifier_tile_coverage(480, 8.0)
        self.assertAlmostEqual(t1, 2 * t2)


class MagnifierCropTest(unittest.TestCase):
    '''apply_magnifier_crop_transform 与 _crop_magnifier_surface 的坐标
    约定（回归锁定）。

    约定与 apply_magnifier_transform 相同：消费布局映射返回的 top-down
    页面点坐标，旋转方向与 presenter 的 ctx.rotate(radians(rotation)) 一致；
    差别仅在源是预渲染 surface（1 px = 1/source_density pt），像素级裁剪
    用例走端到端验证。'''

    SIZE = 200
    SOURCE_DENSITY = 2.0   # 源 surface：200 px = 100 pt 页
    DENSITY = 4.0          # 镜头密度 = 2 × 整页

    def _device_of(self, rotation, source_px, center_pt=(50.0, 50.0)):
        ctx = cairo.Context(cairo.ImageSurface(cairo.Format.ARGB32,
                                               self.SIZE, self.SIZE))
        apply_magnifier_crop_transform(ctx, float(self.SIZE), self.DENSITY,
                                       self.SOURCE_DENSITY, rotation,
                                       center_pt[0], center_pt[1])
        return [ctx.user_to_device(x, y) for x, y in source_px]

    def test_page_point_at_center_lands_at_surface_center(self):
        # 页面点 (50, 50) = 源 px (100, 100)：应落镜面正中。
        (dx, dy) = self._device_of(0, [(100.0, 100.0)])[0]
        self.assertAlmostEqual(dx, self.SIZE / 2, places=6)
        self.assertAlmostEqual(dy, self.SIZE / 2, places=6)

    def test_magnification_scales_by_density_over_source_density(self):
        # 光标右侧 10pt 的点距中心 = 10pt × density 设备像素。
        (dx, dy) = self._device_of(0, [(120.0, 100.0)])[0]
        self.assertAlmostEqual(dx - self.SIZE / 2, 10 * self.DENSITY, places=6)

    def test_rotation_matches_presenter_convention(self):
        # 与 apply_magnifier_transform 同约定：光标右侧的点旋转 90° 后
        # 出现在中心正下方。
        (dx, dy) = self._device_of(90, [(120.0, 100.0)])[0]
        self.assertAlmostEqual(dy - self.SIZE / 2, 10 * self.DENSITY, places=6)
        self.assertAlmostEqual(dx, self.SIZE / 2, places=6)

    def _crop_from_page_surface(self, rotation, ink_center_px):
        '''端到端：源页面 surface 已知位置画墨迹 → _crop_magnifier_surface
        裁剪 → 测量墨迹质心（设备 px）。'''
        source = cairo.ImageSurface(cairo.Format.ARGB32, 200, 200)
        sctx = cairo.Context(source)
        sctx.set_source_rgb(1, 1, 1)
        sctx.paint()
        sctx.set_source_rgb(0, 0, 0)
        sctx.arc(ink_center_px[0], ink_center_px[1], 4, 0, 2 * math.pi)
        sctx.fill()

        crop = _load_renderer_method(
            '_crop_magnifier_surface', cairo=cairo,
            apply_magnifier_crop_transform=apply_magnifier_crop_transform)
        lens = crop(None, source, self.SOURCE_DENSITY, self.DENSITY,
                    rotation, 50.0, 50.0, self.SIZE)

        buf = np.frombuffer(lens.get_data(), dtype=np.uint8).reshape(
            lens.get_height(), lens.get_width(), 4)
        darkness = 765 - buf[..., 0].astype(int) - buf[..., 1].astype(int) - buf[..., 2].astype(int)
        ys, xs = np.nonzero(darkness > 150)
        self.assertGreater(len(xs), 0, '墨迹完全落在镜面裁剪窗外（裁剪数学反向的典型症状）')
        return xs.mean(), ys.mean()

    def test_crop_centers_the_cursor_page_point(self):
        # 页面 pt (50, 50) = 源 px (100, 100) 的墨迹 → 质心在镜面正中。
        cx, cy = self._crop_from_page_surface(0, (100.0, 100.0))
        self.assertAlmostEqual(cx, self.SIZE / 2, delta=2.0)
        self.assertAlmostEqual(cy, self.SIZE / 2, delta=2.0)

    def test_crop_applies_rotation_like_the_presenter(self):
        # 光标右侧 10pt 的墨迹（源 px (120, 100)）旋转 90° → 正中下方
        # 10pt × density。
        cx, cy = self._crop_from_page_surface(90, (120.0, 100.0))
        self.assertAlmostEqual(cx, self.SIZE / 2, delta=2.0)
        self.assertAlmostEqual(cy - self.SIZE / 2, 10 * self.DENSITY, delta=2.0)


class MagnifierCachePipelineTest(unittest.TestCase):
    '''_process_magnifier_cache_todo：任务身份过期判定与结果投递。

    旧管线按 request_id 单调取号判过期；新管线每页一次构建，以任务 dict
    身份判过期（invalidate 把 pending 置 None，在途任务完成后身份不一致
    即丢弃）。本组用例锁定该契约。'''

    def _make_harness(self, render_page=None):
        import threading
        page = (SimpleNamespace(render=render_page) if render_page is not None
                else SimpleNamespace(render=Mock()))
        return SimpleNamespace(
            _magnifier_request_lock=threading.Lock(),
            _magnifier_cache_pending=None,
            _magnifier_page_cache=None,
            _result_delivery_lock=threading.Lock(),
            _shutting_down=False,
            _magnifier_result_idle_id=None,
            magnified_pages_queue=queue.Queue(),
            on_magnifier_result_produced=lambda: False,
            _apply_theme_recolor=lambda surface, colors: surface,
            preview=SimpleNamespace(poppler_document=SimpleNamespace(
                get_page=lambda page_number: page)),
        )

    @staticmethod
    def _task():
        return dict(kind='magnifier_cache', page_number=0,
                    size_px=480, density=4.0, pdf_version=1,
                    matching_theme_colors=None, colors_key=None,
                    anchor_x_pt=50.0, anchor_y_pt=60.0)

    def _process(self, harness, task):
        process = _load_renderer_method(
            '_process_magnifier_cache_todo', GObject=SimpleNamespace(idle_add=Mock(return_value=1)),
            cairo=cairo, MAGNIFIER_TILE_FACTOR=MAGNIFIER_TILE_FACTOR)
        process(harness, task)

    def test_current_task_is_rendered_and_cached(self):
        harness = self._make_harness()
        task = self._task()
        harness._magnifier_cache_pending = task

        self._process(harness, task)

        self.assertIsNotNone(harness._magnifier_page_cache)
        cache = harness._magnifier_page_cache
        self.assertEqual(cache['page'], 0)
        self.assertEqual(cache['pdf_version'], 1)
        self.assertAlmostEqual(cache['density'], 4.0)
        self.assertAlmostEqual(cache['anchor_x_pt'], 50.0)
        self.assertAlmostEqual(cache['anchor_y_pt'], 60.0)
        self.assertIsInstance(cache['surface'], cairo.ImageSurface)
        # 瓦片边长 = MAGNIFIER_TILE_FACTOR × 镜面设备 px（与页面尺寸无关）。
        self.assertEqual(cache['surface'].get_width(),
                         MAGNIFIER_TILE_FACTOR * 480)
        self.assertEqual(cache['surface'].get_height(),
                         MAGNIFIER_TILE_FACTOR * 480)
        self.assertTrue(harness.magnified_pages_queue.get_nowait()
                        .get('kind') == 'magnifier_cache')
        self.assertIsNone(harness._magnifier_cache_pending)

    def test_superseded_task_is_dropped_without_queueing(self):
        harness = self._make_harness()
        stale_task = self._task()
        # pending 已被更新任务取代（身份不一致）→ 旧任务在渲染线程内即弃。
        harness._magnifier_cache_pending = self._task()

        self._process(harness, stale_task)

        self.assertIsNone(harness._magnifier_page_cache)
        self.assertTrue(harness.magnified_pages_queue.empty())

    def test_no_pending_task_is_dropped_after_invalidation(self):
        # invalidate 把 pending 置 None：在途任务完成后同样被丢弃。
        harness = self._make_harness()

        self._process(harness, self._task())

        self.assertIsNone(harness._magnifier_page_cache)
        self.assertTrue(harness.magnified_pages_queue.empty())

    # ---- get_magnifier_surface：软帧密度与瓦片锚点裁剪（像素级） ----

    def _make_get_harness(self, cache=None, rendered_item=None):
        import threading
        harness = self._make_harness()
        harness._magnifier_page_cache = cache
        harness.rendered_pages = ({0: rendered_item} if rendered_item is not None
                                  else {})
        crop = _load_renderer_method(
            '_crop_magnifier_surface', cairo=cairo,
            apply_magnifier_crop_transform=apply_magnifier_crop_transform)
        # AST 加载的裸函数挂到 SimpleNamespace 不会绑定 self：显式吞掉 self。
        harness._crop_magnifier_surface = (
            lambda *arguments, _crop=crop: _crop(None, *arguments))
        harness._magnifier_colors_key = lambda colors: None
        # 请求走真实实现（含覆盖/锚点去重），只记录入队任务。
        request = _load_renderer_method(
            'request_magnifier_page_cache',
            magnifier_tile_coverage=magnifier_tile_coverage,
            MAGNIFIER_TILE_FACTOR=MAGNIFIER_TILE_FACTOR)
        harness.render_queue = queue.Queue()
        harness.request_magnifier_page_cache = request.__get__(harness)
        return harness

    def _get_surface(self, harness, **overrides):
        get = _load_renderer_method(
            'get_magnifier_surface', cairo=cairo,
            apply_magnifier_crop_transform=apply_magnifier_crop_transform,
            magnifier_tile_coverage=magnifier_tile_coverage,
            MAGNIFIER_TILE_FACTOR=MAGNIFIER_TILE_FACTOR)
        arguments = dict(page_number=0, center_x_pt=50.0, center_y_pt=50.0,
                         out_css_size=240, hidpi_factor=2.0, density=8.0,
                         rotation=0, colors=None, pdf_version=1,
                         scale_factor=2.0)
        arguments.update(overrides)
        return get(harness, **arguments)

    @staticmethod
    def _ink_centroid(surface):
        buf = np.frombuffer(surface.get_data(), dtype=np.uint8).reshape(
            surface.get_height(), surface.get_width(), 4)
        darkness = (765 - buf[..., 0].astype(int) - buf[..., 1].astype(int)
                    - buf[..., 2].astype(int))
        ys, xs = np.nonzero(darkness > 150)
        return xs, ys

    def test_soft_path_density_accounts_for_scale(self):
        '''Bug B 回归：软帧源密度必须补乘 scale_factor。

        整页纹理的 css 尺寸随缩放变化（item[1] = 页面 pt × scale），内容
        密度 = surface px / 页面 pt = surface px × scale / css 宽。曾漏乘
        scale：软帧取样位置错（c/s 处）、倍率错（s·h 倍），与瓦片帧切换
        时取景/倍率跳变（闪烁）。scale=2 时墨点必须仍落在镜心。'''
        # 页面 100×200pt，scale 2 → css 200×400；hidpi 2 → 纹理 400×800 px，
        # 内容密度 = scale×hidpi = 4 px/pt。墨点画在页面 pt (50, 50)。
        texture = cairo.ImageSurface(cairo.Format.ARGB32, 400, 800)
        tctx = cairo.Context(texture)
        tctx.set_source_rgb(1, 1, 1)
        tctx.paint()
        tctx.set_source_rgb(0, 0, 0)
        tctx.arc(200, 200, 6, 0, 2 * math.pi)  # 密度 4 → px = pt × 4
        tctx.fill()
        item = [texture, 200, 400, 1, None]

        harness = self._make_get_harness(rendered_item=item)
        surface = self._get_surface(harness)

        self.assertIsNotNone(surface)
        self.assertEqual(surface.get_width(), 480)
        xs, ys = self._ink_centroid(surface)
        self.assertGreater(len(xs), 0, '墨点完全落在镜面外（软帧密度换算错误的典型症状）')
        self.assertAlmostEqual(xs.mean(), 240.0, delta=2.0)
        self.assertAlmostEqual(ys.mean(), 240.0, delta=2.0)
        # 无论软硬路径都请求了瓦片构建（含锚点）。
        task = harness.render_queue.get_nowait()
        self.assertEqual((task['page_number'], task['size_px'], task['density'],
                          task['pdf_version'], task['anchor_x_pt'],
                          task['anchor_y_pt']),
                         (0, 480, 8.0, 1, 50.0, 50.0))

    def test_tile_crop_offsets_center_by_the_anchor(self):
        '''瓦片裁剪回归：瓦片以锚点为原点，裁剪中心必须换成相对锚点坐标。

        曾漏换：从瓦片裁剪时仍用绝对页面 pt 作中心，内容整体偏移
        (c − anchor)×density 像素。锚点 (50, 50)、墨点在锚点右 5pt
        （页面 pt (55, 55)），光标同处 → 墨点必须落在镜心。'''
        ink_pt = (55.0, 55.0)
        anchor = (50.0, 50.0)
        density = 8.0

        def render_page(ctx):
            # 在瓦片构建的 pt 空间里画墨点（ctx 已 scale + translate）。
            ctx.set_source_rgb(0, 0, 0)
            ctx.arc(ink_pt[0], ink_pt[1], 3, 0, 2 * math.pi)
            ctx.fill()

        harness = self._make_harness(render_page=render_page)
        task = dict(kind='magnifier_cache', page_number=0, size_px=480,
                    density=density, pdf_version=1, matching_theme_colors=None,
                    colors_key=None,
                    anchor_x_pt=anchor[0], anchor_y_pt=anchor[1])
        harness._magnifier_cache_pending = task
        self._process(harness, task)
        harness = self._make_get_harness(cache=harness._magnifier_page_cache)

        surface = self._get_surface(harness, center_x_pt=ink_pt[0],
                                    center_y_pt=ink_pt[1])

        self.assertIsNotNone(surface)
        xs, ys = self._ink_centroid(surface)
        self.assertGreater(len(xs), 0, '墨点完全落在镜面外（瓦片锚点偏移未换算的典型症状）')
        self.assertAlmostEqual(xs.mean(), 240.0, delta=2.0)
        self.assertAlmostEqual(ys.mean(), 240.0, delta=2.0)
        # 瓦片覆盖当前光标（drift 5pt ≤ 硬边界）→ 真实去重不重复入队。
        self.assertTrue(harness.render_queue.empty())


if __name__ == '__main__':
    unittest.main()

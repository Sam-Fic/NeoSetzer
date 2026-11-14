#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Pure geometry helpers shared by the PDF preview magnifier and its tests.'''

import math


MAGNIFICATION_FACTOR = 2.0

# 镜头页级瓦片的边长（设备 px）= MAGNIFIER_TILE_FACTOR × 镜面边长。
# 瓦片像素量恒为 factor² × 镜面像素量（与缩放 / hidpi 无关，4× 时约
# 15MB），因此渲染密度永不需要因内存设上限——任何缩放下视觉放大倍率
# 恒为 MAGNIFICATION_FACTOR。光标漂移超出重建阈值才重新锚定构建。
MAGNIFIER_TILE_FACTOR = 4


def magnifier_tile_coverage(size_px, density, tile_factor=MAGNIFIER_TILE_FACTOR):
    '''瓦片覆盖判定的三个 pt 尺度（纯函数，可单测）。

    瓦片是以锚点（页面 top-down pt 坐标）为中心、边长 tile_factor×size_px
    设备 px 的正方形；镜头窗口是以光标为中心、边长 size_px 的正方形。

    返回 (tile_half_pt, lens_half_pt, rebuild_threshold_pt)：
    - tile_half_pt：瓦片半边长（pt）；
    - lens_half_pt：镜头窗口半边长（pt）；
    - rebuild_threshold_pt：光标离锚点的距离超过它就应重新锚定构建——
      比硬边界（tile_half − lens_half，再远镜头窗口会探出瓦片露白）提前
      一个镜头窗口，给异步构建留出软帧过渡余量。'''
    lens_half_pt = size_px / (2.0 * density)
    tile_half_pt = tile_factor * lens_half_pt
    return tile_half_pt, lens_half_pt, tile_half_pt - 2.0 * lens_half_pt


def compute_magnifier_params(diameter_css, hidpi_factor, layout_scale_factor,
                             factor=MAGNIFICATION_FACTOR):
    '''Derive local PDF render density and dimensions for a circular lens.'''
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


def compute_magnifier_placement(cursor_x, cursor_y, diameter, viewport_x,
                                 viewport_y, viewport_w, viewport_h,
                                 gap=14.0):
    '''Place the lens beside the cursor while keeping it inside the viewport.'''
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


def apply_magnifier_transform(ctx, size_px, density, rotation, center_x_pt,
                               center_y_pt):
    '''Map top-down PDF page points onto the local device-pixel surface.'''
    ctx.translate(size_px / 2, size_px / 2)
    if rotation:
        ctx.rotate(math.radians(rotation))
    ctx.scale(density, density)
    ctx.translate(-center_x_pt, -center_y_pt)


def apply_magnifier_crop_transform(ctx, size_px, density, source_density,
                                   rotation, center_x_pt, center_y_pt):
    '''Crop-transform: map source-surface device pixels onto the lens surface.

    Semantically identical to apply_magnifier_transform, but the source is a
    pre-rendered page surface (1 px = 1/source_density pt, e.g. the page-level
    lens cache or a full-page texture) instead of live PDF points:

        lens_px = S/2 + R(rotation) · density · (q_px / source_density − c_pt)

    Rotation matches the presenter's ctx.rotate(radians(rotation)) convention;
    for 90° multiples the sampled region stays an axis-aligned square, so
    painting the whole source surface exactly covers the lens window. The
    caller paints a white base first: source areas beyond the page edge stay
    white, matching the on-screen page background.'''
    ctx.translate(size_px / 2, size_px / 2)
    if rotation:
        ctx.rotate(math.radians(rotation))
    ctx.scale(density / source_density, density / source_density)
    ctx.translate(-center_x_pt * source_density, -center_y_pt * source_density)

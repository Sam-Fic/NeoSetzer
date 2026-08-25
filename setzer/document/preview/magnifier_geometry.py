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

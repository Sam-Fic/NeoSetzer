#!/usr/bin/env python3
# coding: utf-8

import math
import unittest

from setzer.document.preview.magnifier_geometry import (
    MAGNIFICATION_FACTOR,
    apply_magnifier_transform,
    compute_magnifier_params,
    compute_magnifier_placement,
)


class _RecordingContext:

    def __init__(self):
        self.calls = []

    def translate(self, x, y):
        self.calls.append(('translate', x, y))

    def rotate(self, radians):
        self.calls.append(('rotate', radians))

    def scale(self, x, y):
        self.calls.append(('scale', x, y))


class MagnifierGeometryTest(unittest.TestCase):

    def test_density_is_relative_to_the_full_page_render_density(self):
        params = compute_magnifier_params(240, 2.0, 2.0)
        self.assertAlmostEqual(
            params['density'], MAGNIFICATION_FACTOR * 2.0 * 2.0)

    def test_region_and_surface_dimensions_follow_the_requested_factor(self):
        params = compute_magnifier_params(240, 1.0, 1.5)
        self.assertEqual(params['region_css'], 120.0)
        self.assertAlmostEqual(params['region_pt'], 80.0)
        self.assertEqual(params['surface_px'], 240.0)

    def test_render_parameter_chain_is_self_consistent_across_hidpi_levels(self):
        for hidpi in (1.0, 1.5, 2.0):
            for scale in (0.25, 1.0, 4.0):
                params = compute_magnifier_params(240, hidpi, scale)
                self.assertAlmostEqual(
                    params['region_pt'] * params['density'],
                    params['surface_px'], places=6)

    def test_default_placement_is_below_and_right_of_the_cursor(self):
        x, y = compute_magnifier_placement(500, 500, 240, 400, 400, 800, 600)
        self.assertGreater(x, 500)
        self.assertGreater(y, 500)

    def test_placement_flips_and_clamps_at_viewport_edges(self):
        x, y = compute_magnifier_placement(790, 590, 240, 0, 0, 800, 600)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + 240, 800)
        self.assertLessEqual(y + 240, 600)

    def test_placement_uses_the_scrolled_viewport_origin(self):
        x, y = compute_magnifier_placement(1050, 50, 240, 1000, 0, 200, 600)
        self.assertGreaterEqual(x, 1000)
        self.assertGreaterEqual(y, 0)

    def test_transform_centers_the_cursor_before_scaling(self):
        ctx = _RecordingContext()
        apply_magnifier_transform(ctx, 240.0, 4.0, 0, 100.0, 200.0)
        self.assertEqual(ctx.calls, [
            ('translate', 120.0, 120.0),
            ('scale', 4.0, 4.0),
            ('translate', -100.0, -200.0),
        ])

    def test_transform_uses_the_presenter_rotation_before_density(self):
        ctx = _RecordingContext()
        apply_magnifier_transform(ctx, 240.0, 4.0, 90, 100.0, 200.0)
        self.assertEqual(ctx.calls[0], ('translate', 120.0, 120.0))
        self.assertEqual(ctx.calls[1], ('rotate', math.radians(90)))
        self.assertEqual(ctx.calls[2], ('scale', 4.0, 4.0))
        self.assertEqual(ctx.calls[3], ('translate', -100.0, -200.0))


if __name__ == '__main__':
    unittest.main()

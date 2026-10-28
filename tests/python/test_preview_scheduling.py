#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from setzer.document.preview.preview_layouter import PreviewLayouter
from setzer.document.preview.external_pdf_monitor import ExternalPdfState


ROOT = Path(__file__).resolve().parents[2]
PREVIEW = 'setzer/document/preview/'


def load_methods(path, class_name, names, **namespace):
    source = ROOT / path
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in cls.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    return type(class_name, (), {node.name: namespace[node.name] for node in methods})


class DrawingArea:
    def __init__(self):
        self.callbacks = {}
        self.next_id = 0
        self.queue_draw = Mock()

    def add_tick_callback(self, callback):
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        return self.next_id

    def remove_tick_callback(self, source):
        del self.callbacks[source]

    def tick(self, seconds):
        clock = SimpleNamespace(get_frame_time=lambda: round(seconds * 1_000_000))
        for source, callback in list(self.callbacks.items()):
            if not callback(self, clock):
                del self.callbacks[source]


class TestPreviewAnimation(unittest.TestCase):
    def setUp(self):
        cls = load_methods(PREVIEW + 'preview_presenter.py', 'PreviewPresenter',
                           {'start_fade_loop', 'cancel_fade_loop', 'ease'})
        self.presenter = cls()
        self.area = DrawingArea()
        self.presenter.view = SimpleNamespace(drawing_area=self.area)
        self.presenter.preview = SimpleNamespace(
            visible_synctex_rectangles_time=100.0, set_synctex_rectangles=Mock())
        self.presenter.highlight_duration = 1.5
        self.presenter._fade_loop_id = None
        self.presenter._current_time_factor = 0

    def test_hold_does_not_redraw_unchanged_highlight(self):
        self.presenter.start_fade_loop()
        for frame in range(1, 181):
            self.area.tick(100 + frame / 120)
        self.area.queue_draw.assert_called_once_with()
        self.assertEqual(self.presenter._current_time_factor, 1.0)

    def test_fade_updates_at_display_cadence(self):
        for refresh_rate in (60, 120, 144):
            with self.subTest(refresh_rate=refresh_rate):
                self.presenter.start_fade_loop()
                self.area.queue_draw.reset_mock()
                previous = 1.0
                for frame in range(1, refresh_rate // 4):
                    self.area.tick(101.5 + frame / refresh_rate)
                    current = self.presenter._current_time_factor
                    self.assertLess(current, previous)
                    previous = current
                self.assertEqual(self.area.queue_draw.call_count, refresh_rate // 4 - 1)
                self.area.tick(101.75)
                self.assertFalse(self.area.callbacks)
                self.assertIsNone(self.presenter._fade_loop_id)
                self.assertEqual(self.presenter._current_time_factor, 0)
        self.assertEqual(self.presenter.preview.set_synctex_rectangles.call_count, 3)

    def test_restart_replaces_callback_and_cancel_stops_animation(self):
        self.presenter.start_fade_loop()
        old_source = self.presenter._fade_loop_id
        self.presenter.start_fade_loop()
        self.assertNotIn(old_source, self.area.callbacks)
        self.assertEqual(len(self.area.callbacks), 1)
        self.presenter.cancel_fade_loop()
        self.assertFalse(self.area.callbacks)
        self.assertIsNone(self.presenter._fade_loop_id)


class TestPreviewHover(unittest.TestCase):
    def setUp(self):
        cls = load_methods('setzer/widgets/scrolling_widget/scrolling_widget.py',
                           'ScrollingWidget', {'set_cursor_position'})
        self.content = cls()
        self.content.cursor_x = self.content.cursor_y = None
        self.content.content = SimpleNamespace(queue_draw=Mock())
        self.content.width = 800
        self.content.scrolling_offset_x = 10
        self.content.scrolling_offset_y = 20
        controller_cls = load_methods(PREVIEW + 'preview_controller.py', 'PreviewController',
                                      {'on_hover_state_change', '_update_active_text_selection'})
        self.controller = controller_cls()
        self.controller.view = SimpleNamespace(content=self.content)
        self.controller.preview = SimpleNamespace(text_selection_dragging=False,
                                                  update_text_selection=Mock())
        self.controller.update_cursor = Mock()
        self.controller._magnifier_active = False
        self.controller._update_magnifier = Mock()
        self.content.queue_draw = self.content.content.queue_draw
        self.content.add_change_code = lambda signal: self.controller.on_hover_state_change()

    def test_ordinary_hover_only_updates_cursor(self):
        for position in ((1, 2), (3, 4), (3, 4), (None, None)):
            self.content.set_cursor_position(*position)
        self.assertEqual(self.controller.update_cursor.call_count, 3)
        self.content.queue_draw.assert_not_called()

    def test_drag_selection_still_redraws(self):
        self.controller.preview.text_selection_dragging = True
        self.content.set_cursor_position(30, 40)
        self.controller.preview.update_text_selection.assert_called_once_with(40, 60)
        self.content.queue_draw.assert_called_once_with()

    def test_magnifier_still_tracks_hover_without_redrawing_pdf(self):
        self.controller._magnifier_active = True
        self.content.set_cursor_position(30, 40)
        self.controller._update_magnifier.assert_called_once_with()
        self.content.queue_draw.assert_not_called()


class TestPreviewPageSizes(unittest.TestCase):
    def setUp(self):
        self.poppler = Mock()
        cls = load_methods(PREVIEW + 'preview.py', 'Preview',
                           {'load_pdf', 'reset_pdf_data', 'get_page_height'},
                           Poppler=self.poppler,
                           GLib=SimpleNamespace(filename_to_uri=lambda path: path),
                           ExternalPdfState=ExternalPdfState)
        self.preview = cls()
        self.preview.pdf_filename = 'test.pdf'
        self.preview.poppler_document = None
        self.preview.pdf_version = 0
        self.preview.pdf_is_stale = False
        self.preview.page_sizes = []
        self.preview.page_heights = None
        self.preview.page_width = self.preview.page_height = None
        self.preview._external_pdf_tracker = Mock()
        self.preview._text_layout_cache = {}
        for name in ('update_vertical_margin', 'clear_text_selection',
                     '_ensure_external_pdf_monitor', '_set_external_pdf_state',
                     '_stop_external_pdf_monitor', 'add_change_code'):
            setattr(self.preview, name, Mock())
        self.sizes = [(600, 800), (900, 500), (400, 700)]
        self.document = self.make_document(self.sizes)
        self.poppler.Document.new_from_file.return_value = self.document

    def make_document(self, sizes):
        pages = [SimpleNamespace(get_size=Mock(return_value=SimpleNamespace(width=w, height=h)))
                 for w, h in sizes]
        return SimpleNamespace(get_n_pages=lambda: len(pages), get_page=Mock(side_effect=pages.__getitem__))

    def test_zoom_and_rotation_reuse_sizes_without_poppler_calls(self):
        self.assertTrue(self.preview.load_pdf())
        self.assertEqual(self.document.get_page.call_count, len(self.sizes))
        self.assertEqual(self.preview.page_sizes, self.sizes)
        self.document.get_page.side_effect = AssertionError('layout reread Poppler page')
        view = SimpleNamespace(get_allocated_width=lambda: 800, get_scale_factor=lambda: 2)
        self.preview.visible_synctex_rectangles = []
        for rotation in (0, 90, 180, 270):
            for zoom in (0.5, 1.0, 2.0):
                self.preview.rotation = rotation
                self.preview.zoom_manager = SimpleNamespace(get_zoom_level=lambda: zoom)
                layout = PreviewLayouter(self.preview, view).create_layout()
                heights = [size[0 if rotation in (90, 270) else 1] * zoom * 2
                           for size in self.sizes]
                self.assertEqual(layout.page_heights, heights)
                self.assertEqual(layout.page_y_starts[1], layout.vertical_padding + heights[0] + layout.page_gap)
                self.assertEqual(layout.canvas_height, sum(heights) + 2 * layout.page_gap + 2 * layout.vertical_padding)

    def test_failed_reload_keeps_sizes_and_successful_reload_replaces_them(self):
        self.preview.load_pdf()
        old_sizes = self.preview.page_sizes
        old_version = self.preview.pdf_version
        self.poppler.Document.new_from_file.side_effect = RuntimeError('incomplete PDF')
        self.assertFalse(self.preview.load_pdf())
        self.assertIs(self.preview.page_sizes, old_sizes)
        self.assertEqual(self.preview.pdf_version, old_version)
        self.poppler.Document.new_from_file.side_effect = None
        self.poppler.Document.new_from_file.return_value = self.make_document([(300, 200)])
        self.assertTrue(self.preview.load_pdf())
        self.assertEqual(self.preview.page_sizes, [(300, 200)])
        self.assertEqual(self.preview.get_page_height(0), 200)
        self.assertEqual(self.preview.pdf_version, old_version + 1)

    def test_reset_clears_page_geometry(self):
        self.preview.load_pdf()
        self.preview.reset_pdf_data()
        self.assertEqual(self.preview.page_sizes, [])
        self.assertIsNone(self.preview.page_heights)
        self.assertIsNone(self.preview.poppler_document)


if __name__ == '__main__':
    unittest.main()

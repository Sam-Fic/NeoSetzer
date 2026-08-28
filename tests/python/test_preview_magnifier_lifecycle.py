#!/usr/bin/env python3
# coding: utf-8

import ast
import os
import queue
import threading
import unittest
from types import SimpleNamespace


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class _FakeGdk:
    KEY_Escape = 0xff1b
    ModifierType = SimpleNamespace(CONTROL_MASK=1)


def _load_controller_methods():
    path = os.path.join(REPO, 'setzer', 'document', 'preview',
                        'preview_controller.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef)
                      and node.name == 'PreviewController')
    names = {
        '_cancel_magnifier', '_set_hover_feedback',
        'on_magnifier_context_changed', 'on_magnifier_setting_changed',
        'on_key_pressed', 'update_cursor',
    }
    methods = [node for node in class_node.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'Gdk': _FakeGdk, '_': lambda text: text}
    exec(compile(ast.Module(body=methods, type_ignores=[]), path, 'exec'),
         namespace)
    return type('PreviewControllerHarness', (),
                {method.name: namespace[method.name] for method in methods})


def _load_renderer_invalidator():
    path = os.path.join(REPO, 'setzer', 'document', 'preview',
                        'preview_page_renderer.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef)
                      and node.name == 'PreviewPageRenderer')
    method = next(node for node in class_node.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == 'invalidate_magnifier_requests')
    namespace = {'queue': queue}
    exec(compile(ast.Module(body=[method], type_ignores=[]), path, 'exec'),
         namespace)
    return namespace['invalidate_magnifier_requests']


PreviewControllerHarness = _load_controller_methods()
invalidate_magnifier_requests = _load_renderer_invalidator()


class _DrawingArea:

    def __init__(self):
        self.tooltips = []

    def set_tooltip_text(self, value):
        self.tooltips.append(value)


class _Content:

    width = 800
    height = 600
    scrolling_offset_x = 0
    scrolling_offset_y = 0

    def __init__(self):
        self.cursor_x = 10
        self.cursor_y = 20
        self.draw_count = 0

    def queue_draw(self):
        self.draw_count += 1


class _Magnifier:

    def __init__(self):
        self.dismiss_count = 0

    def dismiss(self):
        self.dismiss_count += 1


class _View:

    def __init__(self):
        self.content = _Content()
        self.magnifier = _Magnifier()
        self.drawing_area = _DrawingArea()
        self.cursors = []
        self.link_targets = []

    def set_cursor(self, cursor):
        self.cursors.append(cursor)

    def set_link_target_string(self, target):
        self.link_targets.append(target)


class _Renderer:

    def __init__(self):
        self.invalidated = 0

    def invalidate_magnifier_requests(self):
        self.invalidated += 1


class _Layout:

    def get_page_number_and_offsets_by_document_offsets(self, x, y, width):
        return (0, x, y)


class PreviewMagnifierLifecycleTest(unittest.TestCase):

    def setUp(self):
        self.renderer = _Renderer()
        self.links = []
        self.controller = PreviewControllerHarness()
        self.controller.view = _View()
        self.controller.cursor_default = 'default'
        self.controller.cursor_pointer = 'pointer'
        self.controller.cursor_magnifier = 'zoom-in'
        self.controller.cursor_text = 'text'
        self.controller._current_cursor = None
        self.controller._current_link_target = None
        self.controller._current_tooltip = None
        self.controller._magnifier_active = False
        self.controller._magnifier_layout_ref = None
        self.controller._magnifier_pending_request_id = 3
        self.controller._magnifier_last_enqueue_pos = (10, 20)
        self.controller._magnifier_debug_pos = {'debug': True}
        # 文字选择状态（controller 的新增路径会读取 / 清除）。
        self.controller.preview = SimpleNamespace(
            layout=_Layout(),
            poppler_document=object(),
            page_renderer=self.renderer,
            links_parser=SimpleNamespace(get_links_for_page=lambda page: self.links),
            get_page_height=lambda page: 100,
            use_magnifier=True,
            text_selection=None,
            text_selection_dragging=False,
            text_selection_text=None,
            clear_text_selection=lambda: None,
        )

    def test_non_link_page_uses_zoom_in_cursor(self):
        self.controller.update_cursor()
        self.assertEqual(self.controller.view.cursors, ['zoom-in'])
        self.assertEqual(self.controller.view.link_targets, [''])

    def test_disabled_magnifier_uses_text_cursor_on_page(self):
        self.controller.preview.use_magnifier = False

        self.controller.update_cursor()

        self.assertEqual(self.controller.view.cursors, ['text'])

    def test_disabling_cancels_active_lens_and_restores_cursor(self):
        self.controller._magnifier_active = True
        self.controller._magnifier_layout_ref = self.controller.preview.layout
        self.controller.update_cursor()
        self.assertEqual(self.controller.view.cursors, ['zoom-in'])

        self.controller.preview.use_magnifier = False
        self.controller.on_magnifier_setting_changed()

        self.assertFalse(self.controller._magnifier_active)
        self.assertEqual(self.renderer.invalidated, 1)
        self.assertEqual(self.controller.view.magnifier.dismiss_count, 1)
        # 光标缓存被重置：即使缓存值已是 'zoom-in' 也必须重新下发 'text'
        # （关闭放大镜后悬停页面为普通光标模式的 text 光标）。
        self.assertEqual(self.controller.view.cursors, ['zoom-in', 'text'])

    def test_enabling_refreshes_cursor_without_cancelling(self):
        self.controller.preview.use_magnifier = False
        self.controller.on_magnifier_setting_changed()
        cursors = list(self.controller.view.cursors)

        self.controller.preview.use_magnifier = True
        self.controller.on_magnifier_setting_changed()

        self.assertEqual(self.renderer.invalidated, 0)
        self.assertEqual(self.controller.view.magnifier.dismiss_count, 0)
        self.assertEqual(self.controller.view.cursors, cursors + ['zoom-in'])

    def test_link_keeps_pointer_cursor_over_the_magnifier_cursor(self):
        rect = SimpleNamespace(x1=0, x2=20, y1=70, y2=90)
        self.links = [(rect, 'https://example.invalid', 'uri')]

        self.controller.update_cursor()

        self.assertEqual(self.controller.view.cursors, ['pointer'])
        self.assertEqual(self.controller.view.link_targets,
                         ['https://example.invalid'])

    def test_gap_or_leave_restores_default_feedback(self):
        self.controller.update_cursor()
        self.controller.view.content.cursor_x = None
        self.controller.view.content.cursor_y = None

        self.controller.update_cursor()

        self.assertEqual(self.controller.view.cursors, ['zoom-in', 'default'])
        # Empty values are cached, so leaving does not repeat GTK updates
        # when no link feedback was active on the preceding page position.
        self.assertEqual(self.controller.view.link_targets, [''])
        self.assertEqual(self.controller.view.drawing_area.tooltips, [''])

    def test_escape_cancels_an_active_lens_and_consumes_the_key(self):
        self.controller._magnifier_active = True
        self.controller._magnifier_layout_ref = self.controller.preview.layout

        self.assertTrue(self.controller.on_key_pressed(None, _FakeGdk.KEY_Escape,
                                                        0, 0))
        self.assertFalse(self.controller._magnifier_active)
        self.assertEqual(self.renderer.invalidated, 1)
        self.assertEqual(self.controller.view.magnifier.dismiss_count, 1)
        self.assertEqual(self.controller.view.content.draw_count, 1)

    def test_escape_is_not_consumed_without_an_active_lens(self):
        self.controller.preview.layout = None

        self.assertFalse(self.controller.on_key_pressed(None, _FakeGdk.KEY_Escape,
                                                         0, 0))
        self.assertEqual(self.renderer.invalidated, 0)

    def test_preview_context_change_cancels_active_lens_immediately(self):
        self.controller._magnifier_active = True
        self.controller._magnifier_layout_ref = self.controller.preview.layout

        self.controller.on_magnifier_context_changed(self.controller.preview)

        self.assertFalse(self.controller._magnifier_active)
        self.assertEqual(self.renderer.invalidated, 1)
        self.assertEqual(self.controller.view.magnifier.dismiss_count, 1)

    def test_renderer_invalidation_increments_id_and_drains_results(self):
        renderer = SimpleNamespace(
            _magnifier_request_lock=threading.Lock(),
            _magnifier_latest_request_id=7,
            magnified_pages_queue=queue.Queue(),
        )
        renderer.magnified_pages_queue.put({'request_id': 7})
        renderer.magnified_pages_queue.put({'request_id': 8})

        invalidate_magnifier_requests(renderer)

        self.assertEqual(renderer._magnifier_latest_request_id, 8)
        self.assertTrue(renderer.magnified_pages_queue.empty())


if __name__ == '__main__':
    unittest.main()

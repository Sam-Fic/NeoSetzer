#!/usr/bin/env python3
# coding: utf-8

import ast
import os
import tempfile
import unittest

from setzer.document.preview.external_pdf_monitor import (
    ExternalPdfChangeTracker,
    ExternalPdfState,
)


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class _FakeGLib:

    next_id = 1
    callbacks = {}
    removed = []

    @classmethod
    def reset(cls):
        cls.next_id = 1
        cls.callbacks = {}
        cls.removed = []

    @classmethod
    def timeout_add(cls, delay, callback):
        source_id = cls.next_id
        cls.next_id += 1
        cls.callbacks[source_id] = (delay, callback)
        return source_id

    @classmethod
    def source_remove(cls, source_id):
        cls.removed.append(source_id)
        cls.callbacks.pop(source_id, None)


class _FakeMonitor:

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _File:

    def __init__(self, path):
        self.path = path

    def get_path(self):
        return self.path


def _load_preview_methods():
    path = os.path.join(REPO, 'setzer/document/preview/preview.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'Preview')
    names = {
        '_set_external_pdf_state',
        '_clear_external_pdf_debounce',
        '_stop_external_pdf_monitor',
        '_on_external_pdf_file_changed',
        '_on_external_pdf_debounced',
        'on_build_state_change',
        'reload_external_pdf',
    }
    methods = [node for node in class_node.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        'GLib': _FakeGLib,
        'ExternalPdfState': ExternalPdfState,
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), path, 'exec'), namespace)
    return type('PreviewHarness', (), {method.name: namespace[method.name] for method in methods})


PreviewHarness = _load_preview_methods()


class ExternalPdfPreviewIntegrationTest(unittest.TestCase):

    def setUp(self):
        _FakeGLib.reset()
        self.preview = PreviewHarness()
        self.preview._external_pdf_debounce_id = None
        self.preview._external_pdf_debounce_ms = 400
        self.preview._external_pdf_monitor = None
        self.preview._external_pdf_monitor_directory = None
        self.preview._external_pdf_state = ExternalPdfState.CURRENT
        self.preview._suppress_monitor_for_build = False
        self.notifications = []
        self.preview.add_change_code = lambda code, state=None: self.notifications.append((code, state))

    def _write_pdf(self, filename, content):
        with open(filename, 'wb') as handle:
            handle.write(content)
        stat_result = os.stat(filename)
        os.utime(filename, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000))

    def test_target_event_is_debounced_and_publishes_changed_state_once(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            self.preview._external_pdf_tracker = ExternalPdfChangeTracker(target)
            self.preview._external_pdf_tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 changed')

            self.preview._on_external_pdf_file_changed(None, _File(target), None, object())
            first_id = self.preview._external_pdf_debounce_id
            self.preview._on_external_pdf_file_changed(None, _File(target), None, object())
            second_id = self.preview._external_pdf_debounce_id

            self.assertNotEqual(first_id, second_id)
            self.assertIn(first_id, _FakeGLib.removed)
            delay, callback = _FakeGLib.callbacks[second_id]
            self.assertEqual(delay, 400)
            self.assertFalse(callback())
            self.assertEqual(self.preview._external_pdf_state, ExternalPdfState.CHANGED)
            self.assertEqual(self.notifications, [('external_pdf_state_changed', ExternalPdfState.CHANGED)])

    def test_unrelated_event_does_not_schedule_a_reload_check(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self.preview._external_pdf_tracker = ExternalPdfChangeTracker(target)
            self.preview._on_external_pdf_file_changed(
                None, _File(os.path.join(directory, 'other.pdf')), None, object())
            self.assertIsNone(self.preview._external_pdf_debounce_id)
            self.assertEqual(_FakeGLib.callbacks, {})

    def test_monitor_and_debounce_are_cancelled_on_cleanup(self):
        self.preview._external_pdf_monitor = _FakeMonitor()
        self.preview._external_pdf_monitor_directory = '/tmp'
        self.preview._external_pdf_debounce_id = 9
        self.preview._stop_external_pdf_monitor()
        self.assertTrue(self.preview._external_pdf_monitor is None)
        self.assertIsNone(self.preview._external_pdf_monitor_directory)
        self.assertIn(9, _FakeGLib.removed)

    def test_banner_reload_entry_uses_external_reload_mode(self):
        calls = []
        self.preview.pdf_filename = '/tmp/document.pdf'
        self.preview.load_pdf = lambda external_reload=False: calls.append(external_reload) or True
        self.assertTrue(self.preview.reload_external_pdf())
        self.assertEqual(calls, [True])

    def test_build_in_progress_suppresses_monitor_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            self.preview._external_pdf_tracker = ExternalPdfChangeTracker(target)
            self.preview._external_pdf_tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 changed')

            self.preview.on_build_state_change(None, 'building_in_progress')
            self.preview._on_external_pdf_file_changed(None, _File(target), None, object())
            self.assertIsNone(self.preview._external_pdf_debounce_id)
            self.assertEqual(_FakeGLib.callbacks, {})
            self.assertEqual(self.preview._external_pdf_state, ExternalPdfState.CURRENT)
            self.assertEqual(self.notifications, [])

            # 构建期间横幅 Reload 不触发重载，预览保持旧 PDF。
            calls = []
            self.preview.pdf_filename = target
            self.preview.load_pdf = lambda external_reload=False: calls.append(external_reload) or True
            self.assertFalse(self.preview.reload_external_pdf())
            self.assertEqual(calls, [])

    def test_build_state_transitions_restore_monitor_response(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            self.preview._external_pdf_tracker = ExternalPdfChangeTracker(target)
            self.preview._external_pdf_tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 changed')

            self.preview.on_build_state_change(None, 'building_in_progress')
            self.preview.on_build_state_change(None, 'idle')
            self.assertFalse(self.preview._suppress_monitor_for_build)

            # idle 后监控恢复正常：事件走 debounce 并发布 CHANGED。
            self.preview._on_external_pdf_file_changed(None, _File(target), None, object())
            delay, callback = _FakeGLib.callbacks[self.preview._external_pdf_debounce_id]
            self.assertEqual(delay, 400)
            self.assertFalse(callback())
            self.assertEqual(self.preview._external_pdf_state, ExternalPdfState.CHANGED)


if __name__ == '__main__':
    unittest.main()

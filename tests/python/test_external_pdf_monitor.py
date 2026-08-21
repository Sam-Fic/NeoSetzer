#!/usr/bin/env python3
# coding: utf-8

import os
import tempfile
import time
import unittest

from setzer.document.preview.external_pdf_monitor import (
    ExternalPdfChangeTracker,
    ExternalPdfState,
)


class _File:

    def __init__(self, path):
        self.path = path

    def get_path(self):
        return self.path


class ExternalPdfMonitorTest(unittest.TestCase):

    def _write_pdf(self, filename, content):
        with open(filename, 'wb') as handle:
            handle.write(content)
        # Filesystems with a coarse clock need a distinct signature even where
        # a same-sized replacement happens during the same test tick.
        stat_result = os.stat(filename)
        os.utime(filename, ns=(stat_result.st_atime_ns, stat_result.st_mtime_ns + 1_000_000))

    def test_matches_only_the_target_from_either_rename_path(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            tracker = ExternalPdfChangeTracker(target)
            self.assertTrue(tracker.matches_event_files(_File(target)))
            self.assertTrue(tracker.matches_event_files(_File('unused.tmp'), _File(target)))
            self.assertFalse(tracker.matches_event_files(_File(os.path.join(directory, 'other.pdf'))))

    def test_ignores_events_until_a_successful_pdf_version_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            tracker = ExternalPdfChangeTracker(target)
            self._write_pdf(target, b'%PDF-1.7 second')
            self.assertEqual(tracker.inspect_disk_change(), ExternalPdfState.CURRENT)

    def test_detects_a_different_accepted_pdf_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            tracker = ExternalPdfChangeTracker(target)
            tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 changed externally')
            self.assertEqual(tracker.inspect_disk_change(), ExternalPdfState.CHANGED)

    def test_detects_atomic_replacement_even_when_file_size_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            replacement = os.path.join(directory, 'replacement.pdf')
            self._write_pdf(target, b'%PDF-1.7 same-size')
            tracker = ExternalPdfChangeTracker(target)
            tracker.accept_current_file()
            self._write_pdf(replacement, b'%PDF-1.7 same-size')
            os.replace(replacement, target)
            self.assertEqual(tracker.inspect_disk_change(), ExternalPdfState.CHANGED)

    def test_marks_previous_preview_unavailable_when_target_is_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            tracker = ExternalPdfChangeTracker(target)
            tracker.accept_current_file()
            os.remove(target)
            self.assertEqual(tracker.inspect_disk_change(), ExternalPdfState.UNAVAILABLE)

    def test_accepting_current_file_clears_external_change_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            tracker = ExternalPdfChangeTracker(target)
            tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 second')
            tracker.inspect_disk_change()
            tracker.accept_current_file()
            self.assertEqual(tracker.state, ExternalPdfState.CURRENT)
            self.assertEqual(tracker.inspect_disk_change(), ExternalPdfState.CURRENT)

    def test_reload_failure_keeps_retryable_status(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'document.pdf')
            self._write_pdf(target, b'%PDF-1.7 first')
            tracker = ExternalPdfChangeTracker(target)
            tracker.accept_current_file()
            self._write_pdf(target, b'%PDF-1.7 second')
            tracker.inspect_disk_change()
            self.assertEqual(tracker.record_reload_failure(), ExternalPdfState.RELOAD_FAILED)
            os.remove(target)
            self.assertEqual(tracker.record_reload_failure(), ExternalPdfState.UNAVAILABLE)


if __name__ == '__main__':
    unittest.main()

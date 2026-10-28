#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


PATH = Path(__file__).resolve().parents[2] / 'setzer/document/spellchecking/spellchecking.py'


def load_checker(glib):
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'SpellChecker')
    methods = {'on_buffer_changed', '_on_debounce_timeout', 'schedule_recheck',
               '_recheck_chunk', '_cancel_work', '_cancel_chunk_idle'}
    cls.body = [node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name in methods]
    constants = [node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id in
                         {'_CHUNK_LINES', '_CHUNK_BUDGET_US', '_DEBOUNCE_MS'}
                         for target in node.targets)]
    namespace = {'GLib': glib}
    exec(compile(ast.Module(body=constants + [cls], type_ignores=[]),
                 str(PATH), 'exec'), namespace)
    return namespace['SpellChecker']()


class TestSpellcheckingScheduling(unittest.TestCase):
    def setUp(self):
        self.pending = {}
        self.next_id = 0
        self.now = 0
        self.glib = SimpleNamespace(
            idle_add=self.schedule,
            timeout_add=lambda delay, callback: self.schedule(callback),
            get_monotonic_time=lambda: self.now,
            Source=SimpleNamespace(remove=lambda source: self.pending.pop(source)),
        )
        self.checker = load_checker(self.glib)
        self.checker.enabled = True
        self.checker._is_shutdown = False
        self.checker._generation = 0
        self.checker._chunk_idle_id = None
        self.checker._debounce_id = None
        self.checker._next_line = 0
        self.checker.buffer = SimpleNamespace(get_line_count=lambda: 1000)
        self.checker._check_line_range = Mock(side_effect=self.check_line)

    def schedule(self, callback, *args):
        self.next_id += 1
        self.pending[self.next_id] = (callback, args)
        return self.next_id

    def run_source(self, source):
        callback, args = self.pending.pop(source)
        self.assertIs(callback(*args), False)

    def check_line(self, start, end):
        self.now += 1000

    def test_time_budget_yields_and_resumes_at_next_line(self):
        self.checker.schedule_recheck()
        self.run_source(self.checker._chunk_idle_id)
        self.assertEqual(self.checker._next_line, 3)
        self.assertEqual(self.checker._check_line_range.call_count, 3)
        self.run_source(self.checker._chunk_idle_id)
        self.assertEqual(self.checker._next_line, 6)
        self.checker._check_line_range.assert_called_with(5, 6)
        self.assertEqual(len(self.pending), 1)

    def test_line_ceiling_applies_even_when_clock_does_not_advance(self):
        self.checker._check_line_range.side_effect = None
        self.checker.schedule_recheck()
        self.run_source(self.checker._chunk_idle_id)
        self.assertEqual(self.checker._next_line, 300)
        self.assertEqual(len(self.pending), 1)

    def test_last_chunk_does_not_leave_an_idle(self):
        self.checker.buffer.get_line_count = lambda: 2
        self.checker.schedule_recheck()
        self.run_source(self.checker._chunk_idle_id)
        self.assertEqual(self.checker._next_line, 2)
        self.assertIsNone(self.checker._chunk_idle_id)
        self.assertFalse(self.pending)

    def test_typing_immediately_cancels_pending_chunk(self):
        self.checker.schedule_recheck()
        stale_generation = self.checker._generation
        stale_source = self.checker._chunk_idle_id
        self.checker.on_buffer_changed(self.checker.buffer)
        self.assertNotIn(stale_source, self.pending)
        self.assertIsNone(self.checker._chunk_idle_id)
        self.assertGreater(self.checker._generation, stale_generation)
        self.assertEqual(len(self.pending), 1)
        self.run_source(self.checker._debounce_id)
        self.assertIsNone(self.checker._debounce_id)
        self.assertEqual(self.checker._next_line, 0)
        self.assertEqual(len(self.pending), 1)

    def test_repeated_typing_keeps_only_latest_debounce(self):
        self.checker.on_buffer_changed(self.checker.buffer)
        first_source = self.checker._debounce_id
        self.checker.on_buffer_changed(self.checker.buffer)
        self.assertNotIn(first_source, self.pending)
        self.assertEqual(len(self.pending), 1)

    def test_explicit_recheck_cancels_debounce(self):
        self.checker.on_buffer_changed(self.checker.buffer)
        self.checker.schedule_recheck()
        self.assertIsNone(self.checker._debounce_id)
        self.assertEqual(len(self.pending), 1)

    def test_stale_callback_cannot_clear_current_source(self):
        self.checker.schedule_recheck()
        stale_generation = self.checker._generation
        self.checker.schedule_recheck()
        current_source = self.checker._chunk_idle_id
        self.assertIs(self.checker._recheck_chunk(stale_generation), False)
        self.assertEqual(self.checker._chunk_idle_id, current_source)
        self.checker._check_line_range.assert_not_called()

    def test_shutdown_cancels_work_and_rejects_new_requests(self):
        self.checker.schedule_recheck()
        generation = self.checker._generation
        self.checker._is_shutdown = True
        self.checker._cancel_work()
        self.checker.schedule_recheck()
        self.checker.on_buffer_changed(self.checker.buffer)
        self.assertIs(self.checker._recheck_chunk(generation), False)
        self.assertFalse(self.pending)
        self.checker._check_line_range.assert_not_called()


if __name__ == '__main__':
    unittest.main()

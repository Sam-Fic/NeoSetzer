#!/usr/bin/env python3
# coding: utf-8

import ast
from pathlib import Path
import unittest

from setzer.document.smart_list import (
    SmartListNewlineKind,
    get_smart_list_newline_action,
)


class _FakeIter:

    def __init__(self, buffer, offset):
        self.buffer = buffer
        self.offset = offset

    def get_line(self):
        return self.buffer.text.count('\n', 0, self.offset)

    def get_line_offset(self):
        last_newline = self.buffer.text.rfind('\n', 0, self.offset)
        return self.offset if last_newline == -1 else self.offset - last_newline - 1


class _FakeBuffer:

    def __init__(self, text, cursor=None):
        self.text = text
        self.cursor = len(text) if cursor is None else cursor
        self.user_action_depth = 0
        self.begin_count = 0
        self.end_count = 0

    def get_insert(self):
        return object()

    def get_iter_at_mark(self, mark):
        return _FakeIter(self, self.cursor)

    def get_iter_at_line(self, line_number):
        if line_number < 0:
            return False, None
        if line_number == 0:
            return True, _FakeIter(self, 0)
        offset = 0
        for _ in range(line_number):
            newline = self.text.find('\n', offset)
            if newline == -1:
                return False, None
            offset = newline + 1
        return True, _FakeIter(self, offset)

    def begin_user_action(self):
        self.begin_count += 1
        self.user_action_depth += 1

    def end_user_action(self):
        self.end_count += 1
        self.user_action_depth -= 1

    def insert_at_cursor(self, text):
        self.text = self.text[:self.cursor] + text + self.text[self.cursor:]
        self.cursor += len(text)

    def delete(self, start, end):
        self.text = self.text[:start.offset] + self.text[end.offset:]
        self.cursor = start.offset


class _FakeDocument:

    def __init__(self, text, cursor=None):
        self.source_buffer = _FakeBuffer(text, cursor)

    def get_line(self, line_number):
        return self.source_buffer.text.split('\n')[line_number]


def _load_handle_smart_list_newline():
    source_path = Path(__file__).parents[2] / 'setzer/document/document_controller.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    controller_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == 'DocumentController')
    method = next(
        node for node in controller_class.body if isinstance(node, ast.FunctionDef)
        and node.name == 'handle_smart_list_newline')
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {
        'SmartListNewlineKind': SmartListNewlineKind,
        'get_smart_list_newline_action': get_smart_list_newline_action,
    }
    exec(compile(module, str(source_path), 'exec'), namespace)
    return namespace['handle_smart_list_newline']


_HANDLE_SMART_LIST_NEWLINE = _load_handle_smart_list_newline()


class SmartListControllerBehaviorTest(unittest.TestCase):

    def make_controller(self, text, cursor=None):
        controller = type('Controller', (), {})()
        controller.document = _FakeDocument(text, cursor)
        return controller

    def test_continue_inserts_same_indentation_and_one_undo_action(self):
        controller = self.make_controller('Before\n    \\item First item')

        self.assertTrue(_HANDLE_SMART_LIST_NEWLINE(controller))

        buffer = controller.document.source_buffer
        self.assertEqual(buffer.text, 'Before\n    \\item First item\n    \\item ')
        self.assertEqual(buffer.cursor, len(buffer.text))
        self.assertEqual((buffer.begin_count, buffer.end_count, buffer.user_action_depth), (1, 1, 0))

    def test_exit_removes_empty_marker_and_leaves_blank_line(self):
        controller = self.make_controller('Before\n\t\\item ')

        self.assertTrue(_HANDLE_SMART_LIST_NEWLINE(controller))

        buffer = controller.document.source_buffer
        self.assertEqual(buffer.text, 'Before\n\n')
        self.assertEqual(buffer.cursor, len('Before\n\n'))
        self.assertEqual((buffer.begin_count, buffer.end_count, buffer.user_action_depth), (1, 1, 0))

    def test_non_item_text_is_not_modified(self):
        controller = self.make_controller('Plain paragraph')

        self.assertFalse(_HANDLE_SMART_LIST_NEWLINE(controller))

        buffer = controller.document.source_buffer
        self.assertEqual(buffer.text, 'Plain paragraph')
        self.assertEqual((buffer.begin_count, buffer.end_count, buffer.user_action_depth), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8

import ast
import os
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_method(path, class_name, method_name):
    tree = ast.parse(open(path, encoding='utf-8').read())
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in class_node.body
                  if isinstance(node, ast.FunctionDef) and node.name == method_name)
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), path, 'exec'), namespace)
    return namespace[method_name]


_SCROLL_TO_TOP = _load_method(
    os.path.join(REPO, 'setzer/document/document.py'),
    'Document', 'scroll_cursor_to_top')
_SCROLL_TO_CENTER = _load_method(
    os.path.join(REPO, 'setzer/document/document.py'),
    'Document', 'scroll_cursor_to_center')
_SCROLL_WITH_CONTEXT = _load_method(
    os.path.join(REPO, 'setzer/document/document.py'),
    'Document', 'scroll_cursor_with_context')
_STRUCTURE_ROW_ACTIVATED = _load_method(
    os.path.join(REPO, 'setzer/workspace/sidebar/document_structure_page/structure.py'),
    'StructureSection', 'on_row_activated')
_LABEL_ROW_ACTIVATED = _load_method(
    os.path.join(REPO, 'setzer/workspace/sidebar/document_structure_page/labels.py'),
    'LabelsSection', 'on_row_activated')
_TODO_JUMP = _load_method(
    os.path.join(REPO, 'setzer/workspace/sidebar/document_structure_page/todos.py'),
    'TodosSection', 'jump_to_todo')


class _FakeIter:

    def __init__(self, label, calls):
        self.label = label
        self.calls = calls

    def copy(self):
        return _FakeIter(f'{self.label}-copy', self.calls)

    def backward_lines(self, count):
        self.calls.append(('backward-lines', self.label, count))

    def get_line(self):
        return 37


class _ScrolledWindow:

    def __init__(self, calls):
        self.calls = calls

    def set_kinetic_scrolling(self, enabled):
        self.calls.append(('kinetic', enabled))

    def get_allocated_height(self):
        return 700


class _StickyScroll:

    def __init__(self, calls, reserved_height):
        self.calls = calls
        self.reserved_height = reserved_height

    def get_navigation_reserved_height(self, line_number):
        self.calls.append(('sticky-height', line_number))
        return self.reserved_height


class _SourceView:

    def __init__(self, calls):
        self.calls = calls

    def scroll_to_mark(self, mark, within_margin, use_align, xalign, yalign):
        self.calls.append(('scroll-mark', mark, within_margin, use_align, xalign, yalign))

    def scroll_to_iter(self, text_iter, within_margin, use_align, xalign, yalign):
        self.calls.append(('scroll-iter', text_iter.label, within_margin, use_align, xalign, yalign))

    def grab_focus(self):
        self.calls.append(('focus',))


class _SourceBuffer:

    def __init__(self, calls):
        self.calls = calls
        self.cursor_iter = _FakeIter('cursor', calls)

    def get_insert(self):
        return 'insert-mark'

    def get_iter_at_mark(self, mark):
        self.calls.append(('get-iter-at-mark', mark))
        return self.cursor_iter

    def get_iter_at_offset(self, offset):
        self.calls.append(('get-iter-at-offset', offset))
        return _FakeIter('offset', self.calls)


class _Document:

    def __init__(self, calls):
        self.calls = calls
        self.source_view = _SourceView(calls)
        self.view = type('View', (), {
            'scrolled_window': _ScrolledWindow(calls),
            'source_view': self.source_view,
        })()
        self.source_buffer = _SourceBuffer(calls)

    def place_cursor(self, line_number):
        self.calls.append(('cursor', line_number))

    scroll_cursor_to_top = _SCROLL_TO_TOP
    scroll_cursor_to_center = _SCROLL_TO_CENTER
    scroll_cursor_with_context = _SCROLL_WITH_CONTEXT


class _Workspace:

    def __init__(self, document, calls):
        self.document = document
        self.active_document = document
        self.calls = calls
        self.open_result = document

    def set_active_document(self, document):
        self.calls.append(('activate', document))
        self.active_document = document

    def open_document_by_filename(self, filename):
        self.calls.append(('open', filename))
        return self.open_result


def _section(data_provider):
    return type('Section', (), {'data_provider': data_provider})()


class StructureTopAlignmentTest(unittest.TestCase):

    def test_scroll_cursor_to_top_uses_explicit_vertical_top_alignment(self):
        calls = []
        document = _Document(calls)

        document.scroll_cursor_to_top()

        self.assertEqual(calls, [
            ('get-iter-at-mark', 'insert-mark'),
            ('kinetic', False),
            ('scroll-mark', 'insert-mark', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
        ])

    def test_scroll_cursor_to_top_reserves_actual_sticky_header_height(self):
        calls = []
        document = _Document(calls)
        document.sticky_scroll = _StickyScroll(calls, 56)

        document.scroll_cursor_to_top()

        self.assertEqual(calls, [
            ('get-iter-at-mark', 'insert-mark'),
            ('sticky-height', 37),
            ('kinetic', False),
            ('scroll-mark', 'insert-mark', 0.0, True, 0.0, 0.08),
            ('kinetic', True),
        ])

    def test_scroll_cursor_to_center_uses_explicit_vertical_center_alignment(self):
        calls = []
        document = _Document(calls)

        document.scroll_cursor_to_center()

        self.assertEqual(calls, [
            ('kinetic', False),
            ('scroll-mark', 'insert-mark', 0.0, True, 0.0, 0.5),
            ('kinetic', True),
        ])

    def test_scroll_cursor_with_context_anchors_two_lines_before_cursor(self):
        calls = []
        document = _Document(calls)

        document.scroll_cursor_with_context()

        self.assertEqual(calls, [
            ('get-iter-at-mark', 'insert-mark'),
            ('backward-lines', 'cursor-copy', 2),
            ('kinetic', False),
            ('scroll-iter', 'cursor-copy', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
        ])

    def test_structure_activation_places_cursor_then_aligns_heading_to_top(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        section = _section(type('Provider', (), {'workspace': workspace})())
        row = type('Row', (), {
            'item_data': {'item': [document, 37, 'view-list-symbolic', 'Results']},
        })()

        _STRUCTURE_ROW_ACTIVATED(section, row)

        self.assertEqual(calls, [
            ('activate', document),
            ('cursor', 37),
            ('get-iter-at-mark', 'insert-mark'),
            ('kinetic', False),
            ('scroll-mark', 'insert-mark', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
            ('focus',),
        ])

    def test_label_activation_uses_context_scroll(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        section = _section(type('Provider', (), {'workspace': workspace})())
        row = type('Row', (), {'item_data': ['fig:workflow', 123, document]})()

        _LABEL_ROW_ACTIVATED(section, row)

        self.assertEqual(calls, [
            ('get-iter-at-offset', 123),
            ('activate', document),
            ('cursor', 37),
            ('get-iter-at-mark', 'insert-mark'),
            ('backward-lines', 'cursor-copy', 2),
            ('kinetic', False),
            ('scroll-iter', 'cursor-copy', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
            ('focus',),
        ])

    def test_todo_activation_uses_context_scroll(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        section = _section(type('Provider', (), {'workspace': workspace})())

        _TODO_JUMP(section, ['Rewrite conclusion', 456, document])

        self.assertEqual(calls, [
            ('get-iter-at-offset', 456),
            ('activate', document),
            ('cursor', 37),
            ('get-iter-at-mark', 'insert-mark'),
            ('backward-lines', 'cursor-copy', 2),
            ('kinetic', False),
            ('scroll-iter', 'cursor-copy', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
            ('focus',),
        ])

    def test_go_to_line_and_build_log_use_centered_navigation(self):
        targets = (
            (os.path.join(REPO, 'setzer/workspace/actions/actions.py'),
             'Actions', 'go_to_line_callback'),
            (os.path.join(REPO, 'setzer/dialogs/build_log/build_log_dialog_controller.py'),
             'BuildLogDialogController', 'on_row_activated'),
        )
        for path, class_name, method_name in targets:
            with open(path, encoding='utf-8') as source_file:
                tree = ast.parse(source_file.read())
            class_node = next(node for node in tree.body
                              if isinstance(node, ast.ClassDef) and node.name == class_name)
            method = next(node for node in class_node.body
                          if isinstance(node, ast.FunctionDef) and node.name == method_name)
            calls = [node.func.attr for node in ast.walk(method)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
            self.assertIn('scroll_cursor_to_center', calls, f'{class_name}.{method_name}')
            self.assertNotIn('scroll_cursor_onscreen', calls, f'{class_name}.{method_name}')

    def test_unopenable_include_stops_before_cursor_or_scroll_access(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        workspace.open_result = None
        section = _section(type('Provider', (), {'workspace': workspace})())
        row = type('Row', (), {
            'item_data': {'item': [None, 0, 'text-x-generic-symbolic', '/missing.tex']},
        })()

        _STRUCTURE_ROW_ACTIVATED(section, row)

        self.assertEqual(calls, [('open', '/missing.tex')])


if __name__ == '__main__':
    unittest.main()

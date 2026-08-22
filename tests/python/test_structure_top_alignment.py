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
_ROW_ACTIVATED = _load_method(
    os.path.join(REPO, 'setzer/workspace/sidebar/document_structure_page/structure.py'),
    'StructureSection', 'on_row_activated')


class _ScrolledWindow:

    def __init__(self, calls):
        self.calls = calls

    def set_kinetic_scrolling(self, enabled):
        self.calls.append(('kinetic', enabled))


class _SourceView:

    def __init__(self, calls):
        self.calls = calls

    def scroll_to_mark(self, mark, within_margin, use_align, xalign, yalign):
        self.calls.append(('scroll', mark, within_margin, use_align, xalign, yalign))

    def grab_focus(self):
        self.calls.append(('focus',))


class _SourceBuffer:

    def get_insert(self):
        return 'insert-mark'


class _Document:

    def __init__(self, calls):
        self.calls = calls
        self.source_view = _SourceView(calls)
        self.view = type('View', (), {
            'scrolled_window': _ScrolledWindow(calls),
            'source_view': self.source_view,
        })()
        self.source_buffer = _SourceBuffer()

    def place_cursor(self, line_number):
        self.calls.append(('cursor', line_number))

    scroll_cursor_to_top = _SCROLL_TO_TOP


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


class StructureTopAlignmentTest(unittest.TestCase):

    def test_scroll_cursor_to_top_uses_explicit_vertical_top_alignment(self):
        calls = []
        document = _Document(calls)

        document.scroll_cursor_to_top()

        self.assertEqual(calls, [
            ('kinetic', False),
            ('scroll', 'insert-mark', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
        ])

    def test_structure_activation_places_cursor_then_aligns_heading_to_top(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        section = type('Section', (), {
            'data_provider': type('Provider', (), {'workspace': workspace})(),
        })()
        row = type('Row', (), {
            'item_data': {'item': [document, 37, 'view-list-symbolic', 'Results']},
        })()

        _ROW_ACTIVATED(section, row)

        self.assertEqual(calls, [
            ('activate', document),
            ('cursor', 37),
            ('kinetic', False),
            ('scroll', 'insert-mark', 0.0, True, 0.0, 0.0),
            ('kinetic', True),
            ('focus',),
        ])

    def test_unopenable_include_stops_before_cursor_or_scroll_access(self):
        calls = []
        document = _Document(calls)
        workspace = _Workspace(document, calls)
        workspace.open_result = None
        section = type('Section', (), {
            'data_provider': type('Provider', (), {'workspace': workspace})(),
        })()
        row = type('Row', (), {
            'item_data': {'item': [None, 0, 'text-x-generic-symbolic', '/missing.tex']},
        })()

        _ROW_ACTIVATED(section, row)

        self.assertEqual(calls, [('open', '/missing.tex')])


if __name__ == '__main__':
    unittest.main()

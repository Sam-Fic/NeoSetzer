#!/usr/bin/env python3
# coding: utf-8

import ast
from pathlib import Path
import unittest

from setzer.document.smart_typing import (
    get_smart_quote_insertion,
    is_auto_subscript_group,
    is_subscript_group_char,
    should_open_subscript,
)


class _FakeGdk:
    '''Printable keysyms are their own code point; control keysyms are not, and
    GTK maps some of them to control code points rather than 0 (Tab to 9). The
    subscript rule must treat those as untyped keys, so the fake mirrors it.'''

    CONTROL_KEYVALS = {0xFE20: 0, 0xFF08: 8, 0xFF09: 9, 0xFF0D: 13, 0xFF8D: 0}

    class ModifierType:
        CONTROL_MASK = 1 << 2
        ALT_MASK = 1 << 3
        SHIFT_MASK = 1 << 0

    @staticmethod
    def keyval_to_unicode(keyval):
        return _FakeGdk.CONTROL_KEYVALS.get(keyval, keyval)


class _FakeGtk:
    '''Only the modifier mask the rules entry point checks.'''

    @staticmethod
    def accelerator_get_default_mod_mask():
        return (_FakeGdk.ModifierType.SHIFT_MASK
                | _FakeGdk.ModifierType.CONTROL_MASK
                | _FakeGdk.ModifierType.ALT_MASK)


class _FakeServiceLocator:

    counters = {}

    @classmethod
    def get_increment(cls, name):
        cls.counters[name] = cls.counters.get(name, 0) + 1
        return cls.counters[name]


class _FakeMark:

    def __init__(self, name, offset, left_gravity):
        self.name = name
        self.offset = offset
        self.left_gravity = left_gravity


class _FakeIter:

    def __init__(self, buffer, offset):
        self.buffer = buffer
        self.offset = offset

    def get_offset(self):
        return self.offset

    def get_char(self):
        return self.buffer.text[self.offset] if self.offset < len(self.buffer.text) else ''

    def copy(self):
        return _FakeIter(self.buffer, self.offset)

    def forward_char(self):
        if self.offset < len(self.buffer.text):
            self.offset += 1

    def backward_char(self):
        self.offset = max(0, self.offset - 1)

    def get_line(self):
        return self.buffer.text.count('\n', 0, self.offset)

    def get_line_offset(self):
        last_newline = self.buffer.text.rfind('\n', 0, self.offset)
        return self.offset if last_newline == -1 else self.offset - last_newline - 1

    def ends_line(self):
        return self.buffer.text.find('\n', self.offset) == self.offset or \
            self.offset == len(self.buffer.text)

    def forward_to_line_end(self):
        newline = self.buffer.text.find('\n', self.offset)
        self.offset = len(self.buffer.text) if newline == -1 else newline

    def __repr__(self):
        return f'<_FakeIter {self.offset}>'


class _FakeBuffer:

    def __init__(self, text, cursor=None, context_classes=(), classes_by_offset=()):
        self.text = text
        self.context_classes = set(context_classes)
        # GtkSourceView reports no class at an end-of-line iter; tests model
        # that by giving specific offsets their own class set.
        self.classes_by_offset = {
            offset: set(classes) for offset, classes in dict(classes_by_offset).items()}
        self.calls = []
        self.user_action_depth = 0
        self.begin_count = 0
        self.end_count = 0
        self.marks = []
        cursor = len(text) if cursor is None else cursor
        self.insert_mark = _FakeMark('insert', cursor, True)
        self.has_selection = False

    @property
    def cursor(self):
        return self.insert_mark.offset

    def get_insert(self):
        return self.insert_mark

    def get_iter_at_mark(self, mark):
        return _FakeIter(self, mark.offset)

    def get_start_iter(self):
        return _FakeIter(self, 0)

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

    def get_has_selection(self):
        return self.has_selection

    def begin_user_action(self):
        self.begin_count += 1
        self.user_action_depth += 1

    def end_user_action(self):
        self.end_count += 1
        self.user_action_depth -= 1

    def insert_at_cursor(self, text):
        self._shift_marks(self.cursor, len(text))
        self.text = self.text[:self.cursor] + text + self.text[self.cursor:]
        self.insert_mark.offset = self.cursor + len(text)
        self.has_selection = False

    def place_cursor(self, text_iter):
        self.insert_mark.offset = text_iter.get_offset()
        self.has_selection = False

    def _shift_marks(self, offset, delta):
        for mark in self.marks:
            if mark.offset > offset or (mark.offset == offset and not mark.left_gravity):
                mark.offset += delta

    def create_mark(self, name, text_iter, left_gravity):
        mark = _FakeMark(name, text_iter.get_offset(), left_gravity)
        self.marks.append(mark)
        return mark

    def delete_mark(self, mark):
        if mark in self.marks:
            self.marks.remove(mark)

    def ensure_highlight(self, start, end):
        self.calls.append('ensure_highlight')

    def iter_has_context_class(self, text_iter, syntax_class):
        self.calls.append('iter_has_context_class')
        by_offset = self.classes_by_offset.get(text_iter.get_offset())
        classes = self.context_classes if by_offset is None else by_offset
        return syntax_class in classes


class _FakeSettings:

    def __init__(self, values):
        self.values = dict(values)

    def get_value(self, section, key):
        return self.values.get(key, False)


class _FakeDocument:

    def __init__(self, text, cursor=None, settings=None, context_classes=(),
                 classes_by_offset=(), language='latex'):
        self.source_buffer = _FakeBuffer(
            text, cursor, context_classes, classes_by_offset)
        self.settings = settings or _FakeSettings({})
        self.language = language

    def is_latex_document(self):
        return self.language == 'latex'

    def get_chars_at_iter(self, text_iter, count):
        if count >= 0:
            raise NotImplementedError
        start = max(0, text_iter.get_offset() + count)
        return self.source_buffer.text[start:text_iter.get_offset()]


_METHOD_NAMES = (
    'on_smart_typing_keypress',
    'handle_smart_quote',
    'handle_smart_subscript',
    '_checked_subscript_group_end',
    '_drop_subscript_mark',
    '_completion_popup_is_active',
    '_has_syntax_class',
)


def _load_controller_methods():
    source_path = Path(__file__).parents[2] / 'setzer/document/document_controller.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    controller_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef)
        and node.name == 'DocumentController')
    methods = [
        node for node in controller_class.body
        if isinstance(node, ast.FunctionDef) and node.name in _METHOD_NAMES
    ]
    missing = set(_METHOD_NAMES) - {node.name for node in methods}
    if missing:
        raise AssertionError(f'methods not found in document_controller.py: {missing}')
    module = ast.Module(body=methods, type_ignores=[])
    namespace = {
        'Gdk': _FakeGdk,
        'Gtk': _FakeGtk,
        '_KEYVAL_QUOTEDBL': ord('"'),
        'ServiceLocator': _FakeServiceLocator,
        'get_smart_quote_insertion': get_smart_quote_insertion,
        'is_auto_subscript_group': is_auto_subscript_group,
        'is_subscript_group_char': is_subscript_group_char,
        'should_open_subscript': should_open_subscript,
    }
    exec(compile(module, str(source_path), 'exec'), namespace)
    return {name: namespace[name] for name in _METHOD_NAMES}


_METHODS = _load_controller_methods()


class _ControllerBase(unittest.TestCase):

    def make_controller(self, text, cursor=None, settings=None, context_classes=(),
                        classes_by_offset=(), language='latex'):
        controller = type('Controller', (), dict(_METHODS))()
        document = _FakeDocument(
            text, cursor, _FakeSettings(settings or {}), context_classes,
            classes_by_offset, language)
        controller.document = document
        controller._subscript_mark = None
        return controller

    @property
    def buffer(self):
        return self.controller.document.source_buffer

    def setUp(self):
        _FakeServiceLocator.counters = {}

    def press_subscript(self, char):
        '''Feed one keypress; unhandled keys reach the default insertion.'''
        handled = self.controller.handle_smart_subscript(ord(char))
        if not handled:
            self.buffer.insert_at_cursor(char)
        return handled

    def assert_user_actions_balanced(self):
        self.assertEqual(
            (self.buffer.begin_count, self.buffer.end_count,
             self.buffer.user_action_depth), (1, 1, 0))


QUOTING = {'enable_smart_quotes': True}
SUBSCRIPTING = {'enable_auto_subscript': True}


class SmartQuoteControllerTest(_ControllerBase):

    def test_disabled_setting_inserts_nothing(self):
        self.controller = self.make_controller('Hello', settings=QUOTING)
        self.controller.document.settings.values['enable_smart_quotes'] = False

        self.assertFalse(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, 'Hello')
        self.assertEqual((self.buffer.begin_count, self.buffer.end_count), (0, 0))

    def test_after_word_inserts_closing_quotes_as_one_undo_action(self):
        self.controller = self.make_controller('Hello', settings=QUOTING)

        self.assertTrue(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, "Hello''")
        self.assertEqual(self.buffer.cursor, len(self.buffer.text))
        self.assert_user_actions_balanced()

    def test_at_document_start_inserts_opening_quotes(self):
        self.controller = self.make_controller('', settings=QUOTING)

        self.assertTrue(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, '``')

    def test_after_punctuation_inserts_closing_quotes(self):
        self.controller = self.make_controller('Really?', settings=QUOTING)

        self.assertTrue(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, "Really?''")

    def test_non_spell_checked_contexts_keep_the_literal_quote(self):
        self.controller = self.make_controller(
            r'\verb|', settings=QUOTING, context_classes={'no-spell-check'})

        self.assertFalse(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, r'\verb|')
        self.assertEqual((self.buffer.begin_count, self.buffer.end_count), (0, 0))

    def test_selection_is_left_to_the_user(self):
        self.controller = self.make_controller('word', settings=QUOTING)
        self.buffer.has_selection = True

        self.assertFalse(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, 'word')

    def test_active_completion_popup_is_skipped(self):
        self.controller = self.make_controller('word', settings=QUOTING)
        self.controller.document.autocomplete = type('AC', (), {'is_active': True})()

        self.assertFalse(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, 'word')

    def test_popup_check_survives_uninitialised_autocomplete(self):
        # autocomplete is built lazily for LaTeX documents, so the attribute
        # may not exist at all yet — the guard must not raise.
        self.controller = self.make_controller('word', settings=QUOTING)

        self.assertFalse(self.controller._completion_popup_is_active())


class AutoSubscriptControllerTest(_ControllerBase):

    def test_disabled_setting_inserts_nothing(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING)
        self.controller.document.settings.values['enable_auto_subscript'] = False

        self.assertFalse(self.controller.handle_smart_subscript(ord('1')))
        self.assertEqual(self.buffer.text, 'x_')
        self.assertIsNone(self.controller._subscript_mark)

    def test_non_character_key_is_ignored(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING)

        self.assertFalse(self.controller.handle_smart_subscript(0))
        self.assertEqual(self.buffer.text, 'x_')

    def test_outside_math_nothing_happens(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING)
        self.controller.document.source_buffer.context_classes = set()

        self.assertFalse(self.controller.handle_smart_subscript(ord('1')))
        self.assertEqual(self.buffer.text, 'x_')

    def test_brace_after_marker_is_left_to_bracket_completion(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        self.assertFalse(self.controller.handle_smart_subscript(ord('{')))
        self.assertIsNone(self.controller._subscript_mark)

    def test_escaped_underscore_is_a_typeset_underscore(self):
        self.controller = self.make_controller('x\\_', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        self.assertFalse(self.controller.handle_smart_subscript(ord('1')))
        self.assertEqual(self.buffer.text, 'x\\_')

    def test_first_alnum_is_wrapped_and_cursor_stays_inside(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        self.assertTrue(self.controller.handle_smart_subscript(ord('1')))
        self.assertEqual(self.buffer.text, 'x_{1}')
        self.assertEqual(self.buffer.cursor, 4)
        self.assert_user_actions_balanced()

        mark = self.controller._subscript_mark
        self.assertIsNotNone(mark)
        self.assertFalse(mark.left_gravity)
        self.assertEqual(mark.offset, self.buffer.cursor)

    def test_selection_is_left_to_the_user(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.buffer.has_selection = True

        self.assertFalse(self.controller.handle_smart_subscript(ord('1')))
        self.assertEqual(self.buffer.text, 'x_')

    def test_digits_keep_extending_the_group(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        self.assertTrue(self.press_subscript('1'))
        self.assertFalse(self.press_subscript('2'))
        self.assertEqual(self.buffer.text, 'x_{12}')
        self.assertEqual(self.buffer.cursor, 5)

    def test_full_sequence_x_12_plus_y(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        for char in '12+y':
            self.press_subscript(char)

        self.assertEqual(self.buffer.text, 'x_{12}+y')
        self.assertEqual(self.buffer.cursor, len(self.buffer.text))
        self.assertIsNone(self.controller._subscript_mark)

    def test_superscript_after_a_group_exits_it(self):
        self.controller = self.make_controller('', settings=SUBSCRIPTING,
                                               context_classes={'math'})

        for char in 'x_i^2 ':
            self.press_subscript(char)

        self.assertEqual(self.buffer.text, 'x_{i}^{2} ')
        self.assertEqual(self.buffer.cursor, len(self.buffer.text))

    def test_closing_brace_jumps_out_without_inserting(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.press_subscript('1')

        self.assertTrue(self.press_subscript('}'))
        self.assertEqual(self.buffer.text, 'x_{1}')
        self.assertEqual(self.buffer.cursor, 5)

    def test_group_is_dropped_when_its_brace_disappears(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.press_subscript('1')
        self.buffer.text = self.buffer.text[:-1]  # the user deleted the closing brace

        self.assertFalse(self.press_subscript('2'))
        self.assertEqual(self.buffer.text, 'x_{12')
        self.assertIsNone(self.controller._subscript_mark)
        self.assertEqual(self.buffer.marks, [])

    def test_group_is_dropped_when_the_cursor_moves_away(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.press_subscript('1')
        self.buffer.insert_mark.offset = 0  # the user clicked back to the start

        self.assertFalse(self.press_subscript('9'))
        self.assertEqual(self.buffer.text, '9x_{1}')
        self.assertIsNone(self.controller._subscript_mark)
        self.assertEqual(self.buffer.marks, [])


class SyntaxClassQueryTest(_ControllerBase):

    def test_highlight_is_forced_before_the_class_is_read(self):
        self.controller = self.make_controller('$x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.buffer.calls = []

        self.assertTrue(self.controller._has_syntax_class(
            self.buffer.get_iter_at_mark(self.buffer.get_insert()), 'math'))
        self.assertEqual(
            self.buffer.calls, ['ensure_highlight', 'iter_has_context_class'])

    def test_unknown_class_returns_false(self):
        self.controller = self.make_controller('x', settings=SUBSCRIPTING)

        self.assertFalse(self.controller._has_syntax_class(
            self.buffer.get_iter_at_mark(self.buffer.get_insert()), 'math'))


class ControlKeyTest(_ControllerBase):
    '''Tab/BackSpace/Return map to control code points, not 0 — the rule must
    ignore them instead of jumping out of the group and then letting the default
    handler insert a tab at the new position (found by typing into the editor).'''

    def group_controller(self):
        self.controller = self.make_controller('x_', settings=SUBSCRIPTING,
                                               context_classes={'math'})
        self.press_subscript('1')
        self.press_subscript('2')
        self.buffer.calls = []
        return self.controller

    def test_tab_leaves_the_group_to_tab_jump_brackets(self):
        controller = self.group_controller()

        self.assertFalse(controller.handle_smart_subscript(0xFF09))
        self.assertEqual(self.buffer.cursor, 5)
        self.assertEqual(self.buffer.text, 'x_{12}')
        self.assertEqual(self.buffer.calls, [])

    def test_shift_tab_leaves_the_group_alone(self):
        controller = self.group_controller()

        self.assertFalse(controller.handle_smart_subscript(0xFE20))
        self.assertEqual(self.buffer.cursor, 5)

    def test_backspace_deletes_inside_the_group(self):
        controller = self.group_controller()

        self.assertFalse(controller.handle_smart_subscript(0xFF08))
        self.assertEqual(self.buffer.cursor, 5)

    def test_return_does_not_move_the_cursor(self):
        controller = self.group_controller()

        self.assertFalse(controller.handle_smart_subscript(0xFF0D))
        self.assertEqual(self.buffer.cursor, 5)


class RulesEntryPointTest(_ControllerBase):

    CTRL = _FakeGdk.ModifierType.CONTROL_MASK
    ALT = _FakeGdk.ModifierType.ALT_MASK
    SHIFT = _FakeGdk.ModifierType.SHIFT_MASK

    def press(self, keyval, state=0):
        return self.controller.on_smart_typing_keypress(None, keyval, 0, state)

    def test_plain_quote_reaches_the_quote_rule(self):
        self.controller = self.make_controller('word', settings=QUOTING)

        self.assertTrue(self.press(ord('"')))
        self.assertEqual(self.buffer.text, 'word\'\'')

    def test_shifted_quote_still_counts_as_typing_it(self):
        self.controller = self.make_controller('word', settings=QUOTING)

        self.assertTrue(self.press(ord('"'), self.SHIFT))

    def test_modifier_combinations_are_left_to_shortcuts(self):
        self.controller = self.make_controller('word', settings=QUOTING)

        self.assertFalse(self.press(ord('"'), self.CTRL))
        self.assertFalse(self.press(ord('"'), self.ALT))
        self.assertEqual(self.buffer.text, 'word')

    def test_extra_cursors_disable_the_rules(self):
        # A rule firing only at the main caret would desynchronise the cursors.
        self.controller = self.make_controller('word', settings=QUOTING)
        self.controller.document.multicursor = type('MC', (), {
            'has_multiple_cursors': lambda self: True,
            'is_column_mode': lambda self: False,
        })()

        self.assertFalse(self.press(ord('"')))
        self.assertEqual(self.buffer.text, 'word')

    def test_column_mode_disables_the_rules(self):
        self.controller = self.make_controller('word', settings=QUOTING)
        self.controller.document.multicursor = type('MC', (), {
            'has_multiple_cursors': lambda self: False,
            'is_column_mode': lambda self: True,
        })()

        self.assertFalse(self.press(ord('"')))

    def test_rules_work_before_multicursor_exists(self):
        # multicursor is built from an idle, so the attribute is missing for the
        # first moments of a document's life; the rules must still fire.
        self.controller = self.make_controller('word', settings=QUOTING)

        self.assertFalse(hasattr(self.controller.document, 'multicursor'))
        self.assertTrue(self.press(ord('"')))

    def test_non_latex_documents_keep_their_literal_characters(self):
        # BibTeX uses "…" as string delimiters, so rewriting quotes there
        # would corrupt entries.
        self.controller = self.make_controller('title = ', settings=QUOTING,
                                               language='bibtex')

        self.assertFalse(self.press(ord('"')))
        self.assertEqual(self.buffer.text, 'title = ')

    def test_alnum_after_marker_reaches_the_subscript_rule(self):
        self.controller = self.make_controller(
            'x_', settings=SUBSCRIPTING, context_classes={'math'})

        self.assertTrue(self.press(ord('1')))
        self.assertEqual(self.buffer.text, 'x_{1}')


class EndOfLineContextClassTest(_ControllerBase):
    '''An iter sitting on a line break has no character, so GtkSourceView
    reports no context class there — while the user is typing the last character
    of a verbatim or math region the cursor is exactly there.'''

    def test_line_end_borrows_the_previous_characters_class(self):
        self.controller = self.make_controller(
            '\\begin{verbatim}\nw', classes_by_offset={18: (), 17: ('no-spell-check',)})
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        self.assertTrue(cursor.ends_line())

        self.assertTrue(self.controller._has_syntax_class(cursor, 'no-spell-check'))

    def test_quote_at_the_end_of_a_verbatim_line_stays_literal(self):
        self.controller = self.make_controller(
            '\\begin{verbatim}\nw', settings=QUOTING,
            classes_by_offset={18: (), 17: ('no-spell-check',)})

        self.assertFalse(self.controller.handle_smart_quote())
        self.assertEqual(self.buffer.text, '\\begin{verbatim}\nw')

    def test_mid_line_position_is_not_borrowed(self):
        # Inside a line the cursor's own answer is authoritative: borrowing the
        # previous character would treat text right after \end{verbatim} as
        # still verbatim.
        self.controller = self.make_controller(
            'ab cd', classes_by_offset={3: (), 2: ('no-spell-check',)})
        cursor = self.buffer.get_iter_at_line(0)[1]
        cursor.offset = 3

        self.assertFalse(self.controller._has_syntax_class(cursor, 'no-spell-check'))

    def test_nothing_is_borrowed_before_the_start_of_the_buffer(self):
        self.controller = self.make_controller(
            '\nx', classes_by_offset={0: ()})
        cursor = self.buffer.get_start_iter()
        self.assertTrue(cursor.ends_line())

        self.assertFalse(self.controller._has_syntax_class(cursor, 'no-spell-check'))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

r'''Small, conservative typing-time rules for LaTeX quotes and scripts.

The caller supplies the already-narrowed context (a couple of characters
around the cursor plus the syntax-context answer), so every rule here stays
pure and testable without gi.
'''

from __future__ import annotations

import re


OPENING_QUOTES = '``'
CLOSING_QUOTES = "''"

# 光标前是这些标点时，紧随的 " 属于一段引号的收尾（``Really?''、``word.''）。
# 空白与开括符不在此列，它们后面的 " 开启一段引号。
_CLOSING_QUOTE_CONTEXT = '!?.,;:\'"'

_SCRIPT_MARKERS = '_^'

# 自动加括号的组：``_``/``^`` + ``{`` + 纯字母数字直到光标。
_AUTO_SUBSCRIPT_GROUP_REGEX = re.compile(r'[_^]\{[A-Za-z0-9]*\Z')


def _has_odd_trailing_backslashes(text: str) -> bool:
    r'''True if ``text`` ends with an escaped character (odd backslash count).

    ``x\_1`` is a typeset underscore, ``\\\\_1`` (line break then subscript) is
    a real subscript — so parity, not mere presence, decides.
    '''

    backslashes = len(text) - len(text.rstrip('\\'))
    return bool(backslashes % 2)


def is_subscript_group_char(char: str) -> bool:
    r'''Whether a typed char continues a sub/superscript group.

    Only letters and digits do. Anything else (whitespace, ``+``, ``,``) ends
    the group, which is what lets ``x_i^2`` become ``x_{i}^{2}`` instead of
    nesting.
    '''

    return bool(char) and char.isalnum()


def get_smart_quote_insertion(previous_char: str) -> str:
    r'''Return the LaTeX quotation marks that replace a typed ``"``.

    ``previous_char`` is the single character before the cursor, or ``''`` at
    the document start.
    '''

    if previous_char and (previous_char.isalnum()
                          or previous_char in _CLOSING_QUOTE_CONTEXT):
        return CLOSING_QUOTES
    return OPENING_QUOTES


def should_open_subscript(chars_before_cursor: str, typed_char: str,
                          in_math: bool) -> bool:
    r'''Whether ``typed_char`` should be wrapped into an auto-braced script.

    The rule fires on the first character *after* the marker rather than on the
    marker itself, so typing ``x_{`` still reaches the regular bracket
    auto-close and leaves no stray braces behind.
    '''

    if not in_math or not chars_before_cursor:
        return False
    if not is_subscript_group_char(typed_char):
        return False
    if chars_before_cursor[-1] not in _SCRIPT_MARKERS:
        return False
    return not _has_odd_trailing_backslashes(chars_before_cursor[:-1])


def is_auto_subscript_group(chars_before_cursor: str) -> bool:
    r'''Whether the cursor sits at the end of an auto-braced script group.

    Used to re-validate the remembered mark on every keystroke: if the text no
    longer reads ``…_{letters`` up to the cursor, the group is gone (edited
    away, or the closing brace deleted) and the pending state is dropped.
    '''

    match = _AUTO_SUBSCRIPT_GROUP_REGEX.search(chars_before_cursor)
    if not match:
        return False
    return not _has_odd_trailing_backslashes(chars_before_cursor[:match.start()])

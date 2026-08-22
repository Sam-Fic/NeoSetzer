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

'''Small, conservative rules for continuing literal LaTeX list items.'''

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SmartListNewlineKind(Enum):
    '''The edit to make after Return at the end of a list-item line.'''

    CONTINUE = 'continue'
    EXIT = 'exit'


@dataclass(frozen=True)
class SmartListNewlineAction:
    '''A smart list newline operation and the current list indentation.'''

    kind: SmartListNewlineKind
    indentation: str


def get_smart_list_newline_action(line_text: str, cursor_offset: int) -> SmartListNewlineAction | None:
    r'''Return a list-continuation action for a cursor at a literal ``\item`` line end.

    The rule intentionally supports only the conventional ``\item `` marker.
    This prevents accidental changes to ``\itemize``, labelled items, and partially
    typed commands. A blank marker means the user pressed Return a second time and
    exits the list; non-whitespace body content continues the item at the same
    indentation level.
    '''

    if not isinstance(line_text, str) or cursor_offset != len(line_text):
        return None

    indentation_length = len(line_text) - len(line_text.lstrip(' \t'))
    indentation = line_text[:indentation_length]
    remainder = line_text[indentation_length:]

    if not remainder.startswith('\\item '):
        return None

    body = remainder[len('\\item '):]
    if body == '':
        return SmartListNewlineAction(SmartListNewlineKind.EXIT, indentation)
    if body.strip():
        return SmartListNewlineAction(SmartListNewlineKind.CONTINUE, indentation)
    return None

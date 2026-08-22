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

'''Small, tolerant scanners for literal LaTeX braced arguments.

The helpers deliberately do not execute TeX. They only locate a balanced
literal ``{...}`` argument, which is sufficient for editor structure parsing.
'''

from __future__ import annotations


def scan_balanced_braced_argument(text: str, opening_offset: int) -> tuple[str, int] | None:
    r'''Return ``(contents, end_offset)`` for a balanced literal braced argument.

    ``opening_offset`` must point at the outer opening brace. ``end_offset`` is
    the first character after the matching outer closing brace. Escaped literal
    braces (``\{`` and ``\}``) do not affect depth. An incomplete argument is
    expected while the user is typing, so it returns ``None`` rather than
    raising an exception.
    '''

    if not isinstance(text, str) or opening_offset < 0 or opening_offset >= len(text):
        return None
    if text[opening_offset] != '{':
        return None

    depth = 1
    offset = opening_offset + 1
    while offset < len(text):
        character = text[offset]
        if character == '\\':
            if offset + 1 < len(text):
                escaped = text[offset + 1]
                if escaped in '{}\\':
                    offset += 2
                    continue
            offset += 1
            continue
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return text[opening_offset + 1:offset], offset + 1
        offset += 1
    return None

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''Locate the ``\\begin{...}`` / ``\\end{...}`` region containing the cursor.

Replaces the original ``%•%`` sentinel-injection approach in
``update_matching_blocks.py``: instead of mutating the line text to insert
a marker character and running a regex on the modified string, we split
the line at the cursor offset and check structurally.

The sentinel approach was fragile: if the user's text contained the
literal ``%•%`` string (rare but possible in LaTeX comments), the regex
would falsely match and corrupt the matching-block feature. The
structural approach has no such collision risk because it never modifies
the line text.

This module is gi-free for unit testability.
'''

import re


# Matches ``\begin{`` or ``\end{`` (the opening, without yet requiring a
# closing brace). We find candidate openings with finditer, then validate
# the closing brace and cursor position structurally.
_BEGIN_END_OPEN_REGEX = re.compile(r'\\(begin|end)\{')

# Chars not allowed inside the brace content (per the original regex
# ``[^\{\[\(]``). ``{`` would start a nested group (LaTeX commands like
# ``\begin{a}{b}`` are not supported by this feature), ``[`` starts an
# optional arg, ``(`` starts a LaTeX argument. Their presence invalidates
# the match — the cursor is not considered to be inside a simple
# ``\begin{...}`` / ``\end{...}`` region if any of these appear between
# ``{`` and the closing ``}``.
_FORBIDDEN_IN_CONTENT = frozenset('{[(')


def find_cursor_in_begin_end(line, cursor_offset):
    '''Find the rightmost ``\\begin{...}`` or ``\\end{...}`` on the line
    such that the cursor is inside the braces and the content (excluding
    the closing brace) contains no ``{``, ``[``, or ``(``.

    Returns a tuple ``(begin_or_end, before_cursor, after_cursor, backslash_offset)``
    where:

    - ``begin_or_end`` is the string ``"begin"`` or ``"end"``
    - ``before_cursor`` is the content between ``{`` and the cursor
    - ``after_cursor`` is the content between the cursor and the closing ``}``
    - ``backslash_offset`` is the offset of the ``\\`` in ``line``

    Returns ``None`` if no such region exists.

    Replaces the original ``%•%`` sentinel-injection approach: instead of
    modifying the line text to insert a marker, we split the line at the
    cursor offset and check structurally. This avoids false matches when
    the user's text contains the sentinel string.

    ``cursor_offset`` is the offset of the cursor within ``line`` (i.e.
    ``Gtk.TextIter.get_line_offset()``). An offset of ``n`` means the cursor
    is between ``line[n-1]`` and ``line[n]``; ``before_cursor`` is
    ``line[content_start:cursor_offset]`` and ``after_cursor`` is
    ``line[cursor_offset:last_close]``.
    '''
    best = None
    for m in _BEGIN_END_OPEN_REGEX.finditer(line):
        content_start = m.end()  # offset right after `{`
        # If `{` is at or after the cursor, this and all later matches
        # are after the cursor and can't contain it. Stop.
        if content_start > cursor_offset:
            break
        # Find the maximal valid content extent: scan from content_start,
        # tracking the last `}` seen, stopping at the first forbidden char.
        # The closing `}` is the last `}` before the first forbidden char
        # (or end of line). This mirrors the original regex's greedy
        # `[^\{\[\(]*` (which allows `}` in content) followed by `\}`.
        last_close = None
        for i in range(content_start, len(line)):
            c = line[i]
            if c in _FORBIDDEN_IN_CONTENT:
                break
            if c == '}':
                last_close = i
        if last_close is None:
            continue  # unclosed brace on this line; no match here
        if cursor_offset > last_close:
            continue  # cursor is past the closing brace
        # Cursor is within [content_start, last_close]. Since `{` is
        # forbidden in content, regions can't overlap or nest, so at most
        # one match per line contains the cursor. Keep the rightmost
        # (finditer is left-to-right, so later overwrites earlier).
        before = line[content_start:cursor_offset]
        after = line[cursor_offset:last_close]
        best = (m.group(1), before, after, m.start())
    return best

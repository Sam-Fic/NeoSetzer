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

'''Predictable source-level numbering for document-structure entries.

This module intentionally models only standard, directly visible LaTeX
sectioning commands and literal counter operations. It does not execute TeX
macros or emulate document-class-specific counter formatting; its result is a
stable navigation aid rather than a replacement for TeX's execution engine.
'''

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


SECTION_COMMANDS = (
    'part',
    'chapter',
    'section',
    'subsection',
    'subsubsection',
    'paragraph',
    'subparagraph',
)

# These are LaTeX's standard secnumdepth values, independent from the parser's
# own zero-based tree indentation levels.
LATEX_SECTION_LEVELS = {
    'part': -1,
    'chapter': 0,
    'section': 1,
    'subsection': 2,
    'subsubsection': 3,
    'paragraph': 4,
    'subparagraph': 5,
}
PARSER_SECTION_LEVELS = {command: index for index, command in enumerate(SECTION_COMMANDS)}
DEFAULT_SECNUMDEPTH = 5


@dataclass(frozen=True)
class SectioningCommand:
    '''A standard sectioning command located in the source document.'''

    offset: int
    command: str
    starred: bool = False

    def __post_init__(self):
        if self.command not in PARSER_SECTION_LEVELS:
            raise ValueError(f'Unsupported sectioning command: {self.command}')
        if self.offset < 0:
            raise ValueError('Sectioning command offset must not be negative')


@dataclass(frozen=True)
class SecnumDepthChange:
    r'''A literal ``\setcounter{secnumdepth}{N}`` event in source order.'''

    offset: int
    value: int

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError('secnumdepth change offset must not be negative')


@dataclass(frozen=True)
class AppendixStart:
    '''A literal ``\appendix`` command and its document-class root counter.'''

    offset: int
    root_command: str = 'section'

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError('appendix start offset must not be negative')
        if self.root_command not in ('chapter', 'section'):
            raise ValueError('appendix root must be chapter or section')


@dataclass(frozen=True)
class CounterChange:
    '''A literal setcounter/addtocounter operation for a sectioning counter.'''

    offset: int
    counter: str
    value: int
    relative: bool = False

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError('counter change offset must not be negative')
        if self.counter not in PARSER_SECTION_LEVELS:
            raise ValueError(f'Unsupported sectioning counter: {self.counter}')


def format_structure_title(title: str, number: str | None) -> str:
    '''Return the sidebar title for a numbered or unnumbered structure entry.'''

    return f'{number} {title}' if number else title


def _alphabetic_counter(value: int) -> str:
    '''Return LaTeX-like upper-case alphabetic counter text (A, ..., AA, ...).'''

    if value <= 0:
        return str(value)
    letters = []
    while value:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr(ord('A') + remainder))
    return ''.join(reversed(letters))


def _format_number(counters, level, appendix_root_level):
    parts = []
    for index, counter in enumerate(counters[:level + 1]):
        if counter <= 0:
            continue
        if index == appendix_root_level:
            parts.append(_alphabetic_counter(counter))
        else:
            parts.append(str(counter))
    return '.'.join(parts)


def calculate_structure_numbers(
        commands: Iterable[SectioningCommand],
        secnumdepth_changes: Iterable[SecnumDepthChange] = (),
        default_secnumdepth: int = DEFAULT_SECNUMDEPTH,
        appendix_starts: Iterable[AppendixStart] = (),
        counter_changes: Iterable[CounterChange] = ()) -> dict[int, str | None]:
    r'''Return source offsets mapped to a display number or ``None``.

    Numbered commands increment their own counter and reset deeper counters.
    Starred commands and commands deeper than the active ``secnumdepth`` remain
    navigable but neither display nor alter a number. Literal ``\appendix``
    events reset the document's primary root counter: chapter when chapters are
    present, otherwise section. From that point this root is rendered A, B, C
    and descendants are rendered A.1, B.1, and so on. Literal
    ``\setcounter`` and ``\addtocounter`` events update only the standard
    sectioning counters they name. Counter formatting redefinitions and macro
    expansion deliberately remain outside this source-level model.
    '''

    commands = tuple(commands)

    events = []
    for change in secnumdepth_changes:
        # Apply a setting before a command only if it appears at the same offset.
        events.append((change.offset, 0, change))
    for appendix_start in appendix_starts:
        events.append((appendix_start.offset, 1, appendix_start))
    for change in counter_changes:
        events.append((change.offset, 2, change))
    for command in commands:
        events.append((command.offset, 3, command))
    events.sort(key=lambda event: (event[0], event[1]))

    counters = [0] * len(SECTION_COMMANDS)
    secnumdepth = default_secnumdepth
    in_appendix = False
    appendix_root_level = None
    numbers: dict[int, str | None] = {}
    for _, _, event in events:
        if isinstance(event, SecnumDepthChange):
            secnumdepth = event.value
            continue
        if isinstance(event, AppendixStart):
            in_appendix = True
            appendix_root_level = PARSER_SECTION_LEVELS[event.root_command]
            for level in range(appendix_root_level, len(counters)):
                counters[level] = 0
            continue
        if isinstance(event, CounterChange):
            level = PARSER_SECTION_LEVELS[event.counter]
            if event.relative:
                counters[level] += event.value
            else:
                counters[level] = event.value
            continue

        level = PARSER_SECTION_LEVELS[event.command]
        latex_level = LATEX_SECTION_LEVELS[event.command]
        if event.starred or latex_level > secnumdepth:
            numbers[event.offset] = None
            continue

        counters[level] += 1
        for deeper_level in range(level + 1, len(counters)):
            counters[deeper_level] = 0
        numbers[event.offset] = _format_number(
            counters,
            level,
            appendix_root_level if in_appendix else None,
        )
    return numbers

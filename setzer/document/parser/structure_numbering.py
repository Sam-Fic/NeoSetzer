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
sectioning commands.  It does not execute TeX macros or emulate document-class
specific counter formatting; its result is a stable navigation aid.
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
    '''A literal ``\\setcounter{secnumdepth}{N}`` event in source order.'''

    offset: int
    value: int

    def __post_init__(self):
        if self.offset < 0:
            raise ValueError('secnumdepth change offset must not be negative')


def format_structure_title(title: str, number: str | None) -> str:
    '''Return the sidebar title for a numbered or unnumbered structure entry.'''

    return f'{number} {title}' if number else title


def calculate_structure_numbers(
        commands: Iterable[SectioningCommand],
        secnumdepth_changes: Iterable[SecnumDepthChange] = (),
        default_secnumdepth: int = DEFAULT_SECNUMDEPTH) -> dict[int, str | None]:
    '''Return source offsets mapped to a display number or ``None``.

    Numbered commands increment their own counter and reset deeper counters.
    Starred commands and commands deeper than the active ``secnumdepth`` remain
    navigable but neither display nor alter a number.  Zero counters are omitted
    from the textual number so a tolerated source jump directly to
    ``\\subsection`` is displayed as ``1`` rather than ``0.1``.
    '''

    events = []
    for command in commands:
        events.append((command.offset, 1, command))
    for change in secnumdepth_changes:
        # Apply a setting before a command only if it appears earlier in source.
        events.append((change.offset, 0, change))
    events.sort(key=lambda event: (event[0], event[1]))

    counters = [0] * len(SECTION_COMMANDS)
    secnumdepth = default_secnumdepth
    numbers: dict[int, str | None] = {}
    for _, _, event in events:
        if isinstance(event, SecnumDepthChange):
            secnumdepth = event.value
            continue

        level = PARSER_SECTION_LEVELS[event.command]
        latex_level = LATEX_SECTION_LEVELS[event.command]
        if event.starred or latex_level > secnumdepth:
            numbers[event.offset] = None
            continue

        counters[level] += 1
        for deeper_level in range(level + 1, len(counters)):
            counters[deeper_level] = 0
        numbers[event.offset] = '.'.join(
            str(counter) for counter in counters[:level + 1] if counter > 0)
    return numbers

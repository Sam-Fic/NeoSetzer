#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Extract titled Beamer frames for the document-structure navigator."""

from __future__ import annotations

from dataclasses import dataclass
import re


# Accept two brace depths so common titles such as
# ``\frametitle{A \emph{highlight}}`` remain a single navigator entry.
# The outer title content may contain a braced group, which itself may contain
# one ordinary braced command argument.
_BRACED_TITLE_GROUP = r'\{(?:[^{}]|\{[^{}]*\})*\}'
_TITLE_CONTENT = r'(?P<title>(?:[^{}]|' + _BRACED_TITLE_GROUP + r')*)'
_FRAME_BEGIN_RE = re.compile(
    r'\\begin\s*\{\s*frame\s*\}'
    r'(?:\s*<[^>\n]*>)?'
    r'(?:\s*\[[^\]\n]*\])?'
    r'\s*\{' + _TITLE_CONTENT + r'\}',
    re.IGNORECASE,
)
_FRAME_TITLE_RE = re.compile(
    r'\\frametitle(?:\s*<[^>\n]*>)?\s*\{' + _TITLE_CONTENT + r'\}',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BeamerFrameTitle:
    """A titled frame entry with the offset at which navigation should land."""

    offset: int
    title: str


def extract_beamer_frame_titles(text: str) -> list[BeamerFrameTitle]:
    """Return navigable Beamer frame titles in source order.

    A title in ``\begin{frame}{...}`` wins over a later ``\frametitle{...}``
    in the same environment, preventing duplicate navigator rows. Frames with
    no explicit title remain absent; deriving arbitrary paragraph snippets is
    intentionally outside this syntax-only parser's scope.
    """

    if not isinstance(text, str) or not text:
        return []

    titles: list[BeamerFrameTitle] = []
    frame_starts: list[tuple[int, bool]] = []
    token_re = re.compile(r'\\begin\s*\{\s*frame\s*\}|\\end\s*\{\s*frame\s*\}', re.IGNORECASE)
    events: list[tuple[int, str, re.Match[str]]] = []
    events.extend((match.start(), 'begin_title', match) for match in _FRAME_BEGIN_RE.finditer(text))
    events.extend((match.start(), 'frame_title', match) for match in _FRAME_TITLE_RE.finditer(text))
    events.extend((match.start(), 'frame_token', match) for match in token_re.finditer(text))
    events.sort(key=lambda event: event[0])

    for offset, kind, match in events:
        if kind == 'begin_title':
            title = match.group('title').strip()
            frame_starts.append((offset, bool(title)))
            if title:
                titles.append(BeamerFrameTitle(offset, title))
        elif kind == 'frame_title':
            if frame_starts and not frame_starts[-1][1]:
                title = match.group('title').strip()
                if title:
                    titles.append(BeamerFrameTitle(offset, title))
                    frame_starts[-1] = (frame_starts[-1][0], True)
        else:
            token = match.group(0).casefold()
            if token.startswith('\\begin'):
                # A begin with a title appears in both regex streams at the
                # same offset; only create state if that event has not done it.
                if not frame_starts or frame_starts[-1][0] != offset:
                    frame_starts.append((offset, False))
            elif frame_starts:
                frame_starts.pop()

    return titles

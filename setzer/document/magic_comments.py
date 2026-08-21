#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Parse the small, safe subset of TeXworks-style Magic Comments we support.

The directives are metadata in a LaTeX source comment. They never become shell
commands: ``program`` is restricted to the engines NeoSetzer already supports,
and ``root`` only resolves a relative ``.tex`` path for the current build.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


MAX_MAGIC_COMMENT_LINES = 50
SUPPORTED_PROGRAMS = frozenset(('xelatex', 'pdflatex', 'lualatex', 'tectonic'))
_MAGIC_COMMENT_RE = re.compile(
    r'^\s*%\s*!\s*tex\s+(?P<key>program|root)\s*(?:=|:)\s*(?P<value>.*?)\s*$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MagicComments:
    """Recognized document metadata from the beginning of a TeX source."""

    program: str | None = None
    root: str | None = None


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1].strip()
    return value


def parse_magic_comments(text: str, max_lines: int = MAX_MAGIC_COMMENT_LINES) -> MagicComments:
    """Return supported TeXworks-style metadata found near a source's start.

    The first valid directive for each supported key wins.  This keeps the
    closest-to-the-top project declaration authoritative and prevents later
    ordinary comments from unexpectedly changing build behavior.
    """

    if not isinstance(text, str) or max_lines <= 0:
        return MagicComments()

    values: dict[str, str] = {}
    for line in text.splitlines()[:max_lines]:
        match = _MAGIC_COMMENT_RE.match(line)
        if match is None:
            continue
        key = match.group('key').casefold()
        if key in values:
            continue
        value = _clean_value(match.group('value'))
        if not value:
            continue
        if key == 'program':
            value = value.casefold()
            if value not in SUPPORTED_PROGRAMS:
                continue
        values[key] = value

    return MagicComments(program=values.get('program'), root=values.get('root'))


def resolve_root_filename(document_filename: str | None, root_value: str | None) -> str | None:
    """Resolve a valid relative ``root`` directive to an existing ``.tex`` file.

    The magic comment is never a command.  Absolute paths are deliberately
    rejected, while ``..`` remains valid because split LaTeX projects commonly
    put chapter files below the root document (for example ``../main.tex``).
    """

    if not document_filename or not root_value:
        return None
    root_value = _clean_value(root_value)
    if not root_value or os.path.isabs(root_value):
        return None
    if not root_value.casefold().endswith('.tex'):
        return None

    candidate = os.path.abspath(os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(document_filename)), root_value)
    ))
    if not os.path.isfile(candidate):
        return None
    return candidate

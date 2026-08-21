#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""State coordination for PDFs changed outside NeoSetzer.

The module intentionally contains no GTK/Gio imports.  ``Preview`` owns the
platform-specific directory monitor and timers, while this class makes path,
signature and user-visible state decisions straightforward to regression test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ExternalPdfState(str, Enum):
    """Persistent user-facing state for an externally changed preview PDF."""

    CURRENT = 'current'
    CHANGED = 'changed'
    UNAVAILABLE = 'unavailable'
    RELOAD_FAILED = 'reload_failed'


@dataclass(frozen=True)
class PdfSignature:
    """A lightweight identity for the version currently accepted by Preview."""

    modified_ns: int
    size: int
    inode: int


def _normalise_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _path_from_file(file_or_path) -> Optional[str]:
    """Return a local path from a Gio.File-like object or path-like value."""

    if file_or_path is None:
        return None
    get_path = getattr(file_or_path, 'get_path', None)
    if callable(get_path):
        return get_path()
    try:
        return os.fspath(file_or_path)
    except TypeError:
        return None


class ExternalPdfChangeTracker:
    """Track one PDF path without deciding how its events are scheduled.

    A caller records the signature only after a successful Poppler load.  A
    later different on-disk signature is therefore reliably distinguishable
    from a monitor event produced by NeoSetzer's own already-loaded build.
    """

    def __init__(self, pdf_filename: Optional[str] = None):
        self.pdf_filename: Optional[str] = None
        self._accepted_signature: Optional[PdfSignature] = None
        self.state = ExternalPdfState.CURRENT
        self.set_pdf_filename(pdf_filename)

    @property
    def directory(self) -> Optional[str]:
        if self.pdf_filename is None:
            return None
        return os.path.dirname(self.pdf_filename)

    @property
    def basename(self) -> Optional[str]:
        if self.pdf_filename is None:
            return None
        return os.path.basename(self.pdf_filename)

    def set_pdf_filename(self, pdf_filename: Optional[str]) -> bool:
        """Set a new target and reset history if its canonical path changed."""

        normalised = _normalise_path(pdf_filename)
        if normalised == self.pdf_filename:
            return False
        self.pdf_filename = normalised
        self._accepted_signature = None
        self.state = ExternalPdfState.CURRENT
        return True

    def matches_event_files(self, file, other_file=None) -> bool:
        """Whether a directory-monitor event names the target PDF.

        Checking both paths covers backends that report a rename through the
        old and new locations.  Canonical paths avoid false negatives from
        relative paths or symlink spellings.
        """

        if self.pdf_filename is None:
            return False
        return any(
            _normalise_path(_path_from_file(candidate)) == self.pdf_filename
            for candidate in (file, other_file)
            if candidate is not None
        )

    def get_signature(self) -> Optional[PdfSignature]:
        if self.pdf_filename is None:
            return None
        try:
            stat_result = os.stat(self.pdf_filename)
        except OSError:
            return None
        if stat_result.st_size <= 0:
            return None
        return PdfSignature(
            stat_result.st_mtime_ns,
            stat_result.st_size,
            getattr(stat_result, 'st_ino', 0),
        )

    def accept_current_file(self) -> None:
        """Record the version that Preview successfully opened."""

        self._accepted_signature = self.get_signature()
        self.state = ExternalPdfState.CURRENT

    def inspect_disk_change(self) -> ExternalPdfState:
        """Update and return the state after a debounced monitor event.

        Until a successful preview load has recorded an accepted signature,
        monitor events are ignored: they can belong to the initial PDF output
        or to a document hand-over still in progress.
        """

        current_signature = self.get_signature()
        if current_signature is None:
            if self._accepted_signature is not None:
                self.state = ExternalPdfState.UNAVAILABLE
            return self.state
        if self._accepted_signature is not None and current_signature != self._accepted_signature:
            self.state = ExternalPdfState.CHANGED
        return self.state

    def record_reload_failure(self) -> ExternalPdfState:
        """Keep the old preview and expose a retryable persistent state."""

        self.state = (ExternalPdfState.UNAVAILABLE
                      if self.get_signature() is None
                      else ExternalPdfState.RELOAD_FAILED)
        return self.state

    def clear(self) -> None:
        self._accepted_signature = None
        self.state = ExternalPdfState.CURRENT

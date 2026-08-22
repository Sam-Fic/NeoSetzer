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

'''Safe, explicit persistence for bibliography files not open in Setzer.'''

from __future__ import annotations

from dataclasses import dataclass
import builtins
import hashlib
import os
from pathlib import Path
import tempfile


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class BibTeXExternalChangeError(RuntimeError):
    '''The on-disk bibliography changed after this session read it.'''


@dataclass(frozen=True)
class _Fingerprint:
    digest: str
    mtime_ns: int
    size: int


class BibTeXFileSession:
    '''Read and atomically save one standalone UTF-8 bibliography file.

    A caller must explicitly call :meth:`reload` after an external change. This
    prevents an editor dialog from silently overwriting changes made by another
    program while it was open.
    '''

    def __init__(self, pathname: str):
        self.path = Path(pathname).expanduser().resolve(strict=True)
        if not self.path.is_file():
            raise IsADirectoryError(_('The selected BibTeX path is not a regular file'))
        self.text = ''
        self._fingerprint: _Fingerprint | None = None
        self.reload()

    def reload(self) -> str:
        '''Reload text and establish a new external-change baseline.'''
        raw = self.path.read_bytes()
        self.text = _decode_utf8(raw)
        self._fingerprint = _fingerprint(self.path, raw)
        return self.text

    def has_external_change(self) -> bool:
        '''Return whether on-disk content no longer matches the read baseline.'''
        try:
            raw = self.path.read_bytes()
            return _fingerprint(self.path, raw) != self._fingerprint
        except FileNotFoundError:
            return True

    def write_text(self, text: str) -> None:
        '''Atomically replace the file after verifying no external modification.'''
        if not isinstance(text, str):
            raise TypeError('text must be a string')
        if self.has_external_change():
            raise BibTeXExternalChangeError(_('The bibliography changed outside Setzer'))
        raw = text.encode('utf-8')
        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f'.{self.path.name}.', suffix='.tmp', dir=str(self.path.parent),
            )
            with os.fdopen(descriptor, 'wb') as temporary_file:
                descriptor = None
                temporary_file.write(raw)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            _fsync_directory(self.path.parent)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
        self.text = text
        self._fingerprint = _fingerprint(self.path, raw)


def _decode_utf8(raw: bytes) -> str:
    '''Decode plain UTF-8 and UTF-8 with BOM without silently replacing bytes.'''
    return raw.decode('utf-8-sig')


def _fingerprint(path: Path, raw: bytes) -> _Fingerprint:
    stat = path.stat()
    return _Fingerprint(
        digest=hashlib.sha256(raw).hexdigest(),
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )


def _fsync_directory(directory: Path) -> None:
    '''Persist the atomic rename metadata when the platform supports it.'''
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)

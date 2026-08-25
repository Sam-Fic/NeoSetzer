#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Discover and safely copy user-owned single-file LaTeX templates.

This module intentionally stays free of GTK dependencies so discovery and copy
rules can be tested in headless environments.  Version one exposes only direct
``.tex`` files in the user's Templates directory and copies no adjacent assets
such as images, bibliography databases, or style files.
'''

from __future__ import annotations

import builtins
from dataclasses import dataclass
import os
import stat
import tempfile


MAX_TEMPLATE_BYTES = 8 * 1024 * 1024
_COPY_CHUNK_SIZE = 128 * 1024


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class FileTemplateError(ValueError):
    '''A user-safe error raised while discovering or copying file templates.'''


@dataclass(frozen=True)
class FileTemplate:
    '''A valid top-level .tex file offered from the user's Templates directory.'''

    path: str
    name: str


def list_file_templates(directory: str) -> list[FileTemplate]:
    '''Return valid top-level ``.tex`` files, ordered case-insensitively.

    Missing or inaccessible directories are treated as an empty library.  This
    is appropriate for the optional XDG Templates directory and avoids making
    a missing directory an application-level error.
    '''
    if not isinstance(directory, str) or not directory:
        return []
    directory = os.path.abspath(directory)
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []

    templates = []
    for entry in entries:
        try:
            if (entry.name.startswith('.') or not entry.is_file(follow_symlinks=False)
                    or not entry.name.casefold().endswith('.tex')):
                continue
            if entry.stat(follow_symlinks=False).st_size > MAX_TEMPLATE_BYTES:
                continue
        except OSError:
            continue
        templates.append(FileTemplate(os.path.abspath(entry.path), entry.name))
    return sorted(templates, key=lambda template: template.name.casefold())


def copy_file_template(source_path: str, destination_path: str) -> str:
    '''Atomically create a new .tex file from a selected user template.

    The destination is never overwritten.  Source bytes are copied verbatim so
    template encoding and line-ending conventions remain intact.  The return
    value is the absolute destination path on success.
    '''
    source_path = _validate_template_source(source_path)
    destination_path = _validate_destination(destination_path)
    if os.path.abspath(source_path) == destination_path:
        raise FileTemplateError(_('The destination must be different from the template file'))
    if os.path.exists(destination_path):
        raise FileTemplateError(_('The destination file already exists'))

    destination_directory = os.path.dirname(destination_path)
    try:
        source_mode = os.stat(source_path, follow_symlinks=False).st_mode & 0o777
    except OSError as error:
        raise FileTemplateError(_('The template file could not be read')) from error
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix='.new-', suffix='.tex', dir=destination_directory)
    except OSError as error:
        raise FileTemplateError(_('The destination folder could not be written')) from error

    try:
        # 模板常是团队共享文件；继承其普通访问位，比 mkstemp 默认的 0600
        # 更符合“创建新文档”的用户预期，同时不复制所有者或特殊权限位。
        os.fchmod(descriptor, source_mode)
        with os.fdopen(descriptor, 'wb') as destination_file:
            descriptor = None
            with open(source_path, 'rb') as source_file:
                _copy_bounded(source_file, destination_file)
            destination_file.flush()
            os.fsync(destination_file.fileno())
        # link() succeeds only if destination_path does not exist, so another
        # process cannot be silently overwritten between the preflight check
        # and publication of the fully written temporary file.
        os.link(temporary_path, destination_path)
    except FileExistsError as error:
        raise FileTemplateError(_('The destination file already exists')) from error
    except (OSError, FileTemplateError) as error:
        if isinstance(error, FileTemplateError):
            raise
        raise FileTemplateError(_('Could not create the document from this template')) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    return destination_path


def _validate_template_source(source_path: str) -> str:
    if not isinstance(source_path, str) or not source_path:
        raise FileTemplateError(_('The template file is missing'))
    source_path = os.path.abspath(source_path)
    if not source_path.casefold().endswith('.tex'):
        raise FileTemplateError(_('Template files must use the .tex extension'))
    try:
        stat_result = os.stat(source_path, follow_symlinks=False)
    except FileNotFoundError as error:
        raise FileTemplateError(_('The template file is missing')) from error
    except OSError as error:
        raise FileTemplateError(_('The template file could not be read')) from error
    if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size > MAX_TEMPLATE_BYTES:
        raise FileTemplateError(_('The template file is not a supported LaTeX file'))
    return source_path


def _validate_destination(destination_path: str) -> str:
    if not isinstance(destination_path, str) or not destination_path:
        raise FileTemplateError(_('Choose a destination for the new document'))
    destination_path = os.path.abspath(destination_path)
    if not destination_path.casefold().endswith('.tex'):
        raise FileTemplateError(_('The destination file must use the .tex extension'))
    if not os.path.isdir(os.path.dirname(destination_path)):
        raise FileTemplateError(_('The destination folder does not exist'))
    return destination_path


def _copy_bounded(source_file, destination_file) -> None:
    copied = 0
    while True:
        chunk = source_file.read(_COPY_CHUNK_SIZE)
        if not chunk:
            return
        copied += len(chunk)
        if copied > MAX_TEMPLATE_BYTES:
            raise FileTemplateError(_('The template file is too large'))
        destination_file.write(chunk)

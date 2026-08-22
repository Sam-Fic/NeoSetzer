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

'''Safe, local snapshots for user-created LaTeX document templates.

The store deliberately persists copied UTF-8 source text, never a path to the
originating document.  Template names are metadata only; UUID filenames avoid
turning user input into filesystem paths.
'''

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
import tempfile
import uuid
from typing import Any


INDEX_FILENAME = 'index.json'
STORE_DIRECTORY = 'document-templates'
STORE_VERSION = 1
MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
MAX_TEMPLATE_NAME_LENGTH = 80
_UUID_FILENAME_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.tex$')


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class TemplateStoreError(ValueError):
    '''A user-safe error raised for invalid template data or store failures.'''


@dataclass(frozen=True)
class UserDocumentTemplate:
    '''Validated metadata for one immutable user template snapshot.'''

    identifier: str
    name: str
    filename: str
    created_at: str
    updated_at: str
    character_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.identifier,
            'name': self.name,
            'filename': self.filename,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'character_count': self.character_count,
        }


class UserDocumentTemplateStore:
    '''Persist and retrieve user-owned, single-file LaTeX source templates.'''

    def __init__(self, user_data_directory: str):
        if not isinstance(user_data_directory, str) or not user_data_directory:
            raise ValueError(_('A user data directory is required'))
        self.directory = os.path.join(os.path.abspath(user_data_directory), STORE_DIRECTORY)
        self.index_path = os.path.join(self.directory, INDEX_FILENAME)

    @staticmethod
    def validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise TemplateStoreError(_('Template name must be text'))
        name = name.strip()
        if not name:
            raise TemplateStoreError(_('Template name cannot be empty'))
        if len(name) > MAX_TEMPLATE_NAME_LENGTH:
            raise TemplateStoreError(_('Template name is too long'))
        if any(ord(character) < 32 or character in '/\\' for character in name):
            raise TemplateStoreError(_('Template name contains unsupported characters'))
        return name

    @staticmethod
    def validate_text(text: str) -> str:
        if not isinstance(text, str):
            raise TemplateStoreError(_('Template source must be text'))
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        try:
            encoded = text.encode('utf-8')
        except UnicodeEncodeError as error:
            raise TemplateStoreError(_('Template source must be valid UTF-8 text')) from error
        if not text.strip():
            raise TemplateStoreError(_('Template source cannot be empty'))
        if len(encoded) > MAX_TEMPLATE_BYTES:
            raise TemplateStoreError(_('Template source is too large'))
        return text

    def list_templates(self) -> list[UserDocumentTemplate]:
        return sorted(self._read_index(), key=lambda template: template.name.casefold())

    def save(self, name: str, source: str) -> UserDocumentTemplate:
        name = self.validate_name(name)
        source = self.validate_text(source)
        templates = self._read_index()
        if any(template.name.casefold() == name.casefold() for template in templates):
            raise TemplateStoreError(_('A template with this name already exists'))

        self._ensure_directory()
        identifier = str(uuid.uuid4())
        filename = identifier + '.tex'
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        template = UserDocumentTemplate(
            identifier=identifier,
            name=name,
            filename=filename,
            created_at=timestamp,
            updated_at=timestamp,
            character_count=len(source),
        )
        self._write_atomic(self._template_path(filename), source)
        try:
            self._write_index_atomic(templates + [template])
        except Exception:
            try:
                os.unlink(self._template_path(filename))
            except FileNotFoundError:
                pass
            raise
        return template

    def load(self, identifier: str) -> str:
        template = self._get_template(identifier)
        try:
            with open(self._template_path(template.filename), 'r', encoding='utf-8', newline=None) as source_file:
                return self.validate_text(source_file.read())
        except FileNotFoundError as error:
            raise TemplateStoreError(_('The template source file is missing')) from error
        except UnicodeDecodeError as error:
            raise TemplateStoreError(_('The template source file is not valid UTF-8')) from error

    def delete(self, identifier: str) -> bool:
        templates = self._read_index()
        retained = [template for template in templates if template.identifier != identifier]
        if len(retained) == len(templates):
            return False
        target = next(template for template in templates if template.identifier == identifier)
        self._ensure_directory()
        self._write_index_atomic(retained)
        try:
            os.unlink(self._template_path(target.filename))
        except FileNotFoundError:
            pass
        return True

    def _get_template(self, identifier: str) -> UserDocumentTemplate:
        if not isinstance(identifier, str):
            raise TemplateStoreError(_('Template identifier is invalid'))
        for template in self._read_index():
            if template.identifier == identifier:
                return template
        raise TemplateStoreError(_('The selected template no longer exists'))

    def _read_index(self) -> list[UserDocumentTemplate]:
        try:
            with open(self.index_path, 'r', encoding='utf-8') as index_file:
                raw_index = json.load(index_file)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as error:
            raise TemplateStoreError(_('The template library index could not be read')) from error

        if not isinstance(raw_index, dict) or raw_index.get('version') != STORE_VERSION:
            raise TemplateStoreError(_('The template library index has an unsupported format'))
        raw_templates = raw_index.get('templates')
        if not isinstance(raw_templates, list):
            raise TemplateStoreError(_('The template library index is invalid'))

        templates = []
        for raw_template in raw_templates:
            try:
                templates.append(self._template_from_dict(raw_template))
            except TemplateStoreError:
                # A stale or manually damaged entry should not prevent the rest
                # of the user library from being usable.
                continue
        return templates

    @classmethod
    def _template_from_dict(cls, raw_template: Any) -> UserDocumentTemplate:
        if not isinstance(raw_template, dict):
            raise TemplateStoreError(_('Template metadata is invalid'))
        identifier = raw_template.get('id')
        filename = raw_template.get('filename')
        try:
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError
        except (ValueError, AttributeError, TypeError) as error:
            raise TemplateStoreError(_('Template identifier is invalid')) from error
        if not isinstance(filename, str) or not _UUID_FILENAME_RE.fullmatch(filename):
            raise TemplateStoreError(_('Template filename is invalid'))
        if filename != identifier + '.tex':
            raise TemplateStoreError(_('Template filename does not match its identifier'))
        name = cls.validate_name(raw_template.get('name'))
        created_at = raw_template.get('created_at')
        updated_at = raw_template.get('updated_at')
        character_count = raw_template.get('character_count')
        if (not isinstance(created_at, str) or not isinstance(updated_at, str)
                or not isinstance(character_count, int) or character_count < 0):
            raise TemplateStoreError(_('Template metadata is invalid'))
        return UserDocumentTemplate(identifier, name, filename, created_at, updated_at, character_count)

    def _template_path(self, filename: str) -> str:
        if not _UUID_FILENAME_RE.fullmatch(filename):
            raise TemplateStoreError(_('Template filename is invalid'))
        path = os.path.abspath(os.path.join(self.directory, filename))
        if os.path.dirname(path) != self.directory:
            raise TemplateStoreError(_('Template path is invalid'))
        return path

    def _ensure_directory(self):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def _write_index_atomic(self, templates: list[UserDocumentTemplate]):
        content = json.dumps(
            {
                'version': STORE_VERSION,
                'templates': [template.to_dict() for template in templates],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + '\n'
        self._write_atomic(self.index_path, content)

    @staticmethod
    def _write_atomic(path: str, content: str):
        directory = os.path.dirname(path)
        descriptor, temporary_path = tempfile.mkstemp(prefix='.new-', dir=directory, text=True)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output_file:
                descriptor = None
                output_file.write(content)
                output_file.flush()
                os.fsync(output_file.fileno())
            os.replace(temporary_path, path)
        except OSError as error:
            raise TemplateStoreError(_('The template library could not be written')) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

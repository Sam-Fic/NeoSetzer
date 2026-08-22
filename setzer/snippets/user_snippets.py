# coding: utf-8
#
# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

'''Safe, local storage and autocomplete proposals for user-defined snippets.'''

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


STORE_DIRECTORY = 'snippets'
INDEX_FILENAME = 'index.json'
STORE_VERSION = 1
MAX_NAME_LENGTH = 80
MAX_TRIGGER_LENGTH = 80
MAX_BODY_BYTES = 128 * 1024
_TRIGGER_RE = re.compile(r'^\\[A-Za-z][A-Za-z0-9@]*$')
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class SnippetStoreError(ValueError):
    '''A user-safe error raised for invalid snippet data or store failures.'''


@dataclass(frozen=True)
class UserSnippet:
    '''Validated metadata and source for one user-defined snippet.'''

    identifier: str
    name: str
    trigger: str
    body: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            'id': self.identifier,
            'name': self.name,
            'trigger': self.trigger,
            'body': self.body,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class UserSnippetStore:
    '''Persist and query user-defined LaTeX snippets in a versioned JSON file.'''

    def __init__(self, user_data_directory: str):
        if not isinstance(user_data_directory, str) or not user_data_directory:
            raise ValueError(_('A user data directory is required'))
        self.directory = os.path.join(os.path.abspath(user_data_directory), STORE_DIRECTORY)
        self.index_path = os.path.join(self.directory, INDEX_FILENAME)

    @staticmethod
    def validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise SnippetStoreError(_('Snippet name must be text'))
        name = name.strip()
        if not name:
            raise SnippetStoreError(_('Snippet name cannot be empty'))
        if len(name) > MAX_NAME_LENGTH:
            raise SnippetStoreError(_('Snippet name is too long'))
        if any(ord(character) < 32 or character in '/\\' for character in name):
            raise SnippetStoreError(_('Snippet name contains unsupported characters'))
        return name

    @staticmethod
    def validate_trigger(trigger: str) -> str:
        if not isinstance(trigger, str):
            raise SnippetStoreError(_('Snippet trigger must be text'))
        trigger = trigger.strip()
        if not _TRIGGER_RE.fullmatch(trigger):
            raise SnippetStoreError(_(
                'Snippet trigger must start with a backslash and use letters, numbers, or @'))
        if len(trigger) > MAX_TRIGGER_LENGTH:
            raise SnippetStoreError(_('Snippet trigger is too long'))
        return trigger

    @staticmethod
    def validate_body(body: str) -> str:
        if not isinstance(body, str):
            raise SnippetStoreError(_('Snippet body must be text'))
        body = body.replace('\r\n', '\n').replace('\r', '\n')
        try:
            encoded = body.encode('utf-8')
        except UnicodeEncodeError as error:
            raise SnippetStoreError(_('Snippet body must be valid UTF-8 text')) from error
        if not body.strip():
            raise SnippetStoreError(_('Snippet body cannot be empty'))
        if len(encoded) > MAX_BODY_BYTES:
            raise SnippetStoreError(_('Snippet body is too large'))
        return body

    def list_snippets(self) -> list[UserSnippet]:
        return sorted(self._read_index(), key=lambda snippet: (snippet.trigger.casefold(), snippet.name.casefold()))

    def create(self, name: str, trigger: str, body: str) -> UserSnippet:
        name = self.validate_name(name)
        trigger = self.validate_trigger(trigger)
        body = self.validate_body(body)
        snippets = self._read_index()
        self._validate_unique_trigger(snippets, trigger)

        timestamp = self._timestamp()
        snippet = UserSnippet(
            identifier=str(uuid.uuid4()), name=name, trigger=trigger, body=body,
            created_at=timestamp, updated_at=timestamp)
        self._write_index_atomic(snippets + [snippet])
        return snippet

    def update(self, identifier: str, name: str, trigger: str, body: str) -> UserSnippet:
        name = self.validate_name(name)
        trigger = self.validate_trigger(trigger)
        body = self.validate_body(body)
        snippets = self._read_index()
        existing = self._get_snippet(snippets, identifier)
        self._validate_unique_trigger(snippets, trigger, ignored_identifier=existing.identifier)

        updated = UserSnippet(
            identifier=existing.identifier, name=name, trigger=trigger, body=body,
            created_at=existing.created_at, updated_at=self._timestamp())
        self._write_index_atomic([
            updated if snippet.identifier == existing.identifier else snippet
            for snippet in snippets])
        return updated

    def delete(self, identifier: str) -> bool:
        snippets = self._read_index()
        retained = [snippet for snippet in snippets if snippet.identifier != identifier]
        if len(retained) == len(snippets):
            return False
        self._write_index_atomic(retained)
        return True

    def proposals_for(self, current_word: str) -> list[dict[str, Any]]:
        '''Return autocomplete-compatible candidates matching a command prefix.'''
        if not isinstance(current_word, str) or not current_word.startswith('\\'):
            return []
        prefix = current_word.casefold()
        try:
            snippets = self.list_snippets()
        except SnippetStoreError:
            # A manually corrupted snippet library must never disable editor completion.
            return []
        proposals = []
        for snippet in snippets:
            if snippet.trigger.casefold().startswith(prefix):
                proposals.append({
                    'command': snippet.trigger,
                    'description': snippet.name,
                    'dotlabels': '',
                    'insert_text': snippet.body,
                    'is_snippet': True,
                    '_preamble_only': False,
                })
        return proposals

    def _validate_unique_trigger(self, snippets: list[UserSnippet], trigger: str,
                                 ignored_identifier: str | None = None):
        if any(snippet.identifier != ignored_identifier and
               snippet.trigger.casefold() == trigger.casefold() for snippet in snippets):
            raise SnippetStoreError(_('A snippet with this trigger already exists'))

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _get_snippet(snippets: list[UserSnippet], identifier: str) -> UserSnippet:
        if not isinstance(identifier, str) or not _UUID_RE.fullmatch(identifier):
            raise SnippetStoreError(_('Snippet identifier is invalid'))
        for snippet in snippets:
            if snippet.identifier == identifier:
                return snippet
        raise SnippetStoreError(_('The selected snippet no longer exists'))

    def _read_index(self) -> list[UserSnippet]:
        try:
            with open(self.index_path, 'r', encoding='utf-8') as index_file:
                raw_index = json.load(index_file)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as error:
            raise SnippetStoreError(_('The snippet library could not be read')) from error

        if not isinstance(raw_index, dict) or raw_index.get('version') != STORE_VERSION:
            raise SnippetStoreError(_('The snippet library has an unsupported format'))
        raw_snippets = raw_index.get('snippets')
        if not isinstance(raw_snippets, list):
            raise SnippetStoreError(_('The snippet library is invalid'))

        snippets = []
        for raw_snippet in raw_snippets:
            try:
                snippets.append(self._snippet_from_dict(raw_snippet))
            except SnippetStoreError:
                # Ignore a stale or manually damaged record without losing the library.
                continue
        return snippets

    @classmethod
    def _snippet_from_dict(cls, raw_snippet: Any) -> UserSnippet:
        if not isinstance(raw_snippet, dict):
            raise SnippetStoreError(_('Snippet metadata is invalid'))
        identifier = raw_snippet.get('id')
        try:
            if str(uuid.UUID(identifier)) != identifier:
                raise ValueError
        except (ValueError, AttributeError, TypeError) as error:
            raise SnippetStoreError(_('Snippet identifier is invalid')) from error
        name = cls.validate_name(raw_snippet.get('name'))
        trigger = cls.validate_trigger(raw_snippet.get('trigger'))
        body = cls.validate_body(raw_snippet.get('body'))
        created_at = raw_snippet.get('created_at')
        updated_at = raw_snippet.get('updated_at')
        if not isinstance(created_at, str) or not isinstance(updated_at, str):
            raise SnippetStoreError(_('Snippet metadata is invalid'))
        return UserSnippet(identifier, name, trigger, body, created_at, updated_at)

    def _ensure_directory(self):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def _write_index_atomic(self, snippets: list[UserSnippet]):
        self._ensure_directory()
        content = json.dumps({
            'version': STORE_VERSION,
            'snippets': [snippet.to_dict() for snippet in snippets],
        }, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        descriptor, temporary_path = tempfile.mkstemp(prefix='.new-', dir=self.directory, text=True)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output_file:
                descriptor = None
                output_file.write(content)
                output_file.flush()
                os.fsync(output_file.fileno())
            os.replace(temporary_path, self.index_path)
        except OSError as error:
            raise SnippetStoreError(_('The snippet library could not be written')) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

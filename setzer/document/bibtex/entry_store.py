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

'''A conservative, source-preserving BibTeX entry model.

This module intentionally does not serialize a complete bibliography.  It scans
literal entry ranges and creates patches for one chosen entry, so comments,
strings, preambles, entry order, and hand-written formatting outside that range
remain untouched.  Whole-file formatting rewrites exactly the safely parsed
entry ranges, keeping every other byte of source intact.
'''

from __future__ import annotations

from dataclasses import dataclass
import builtins
import re
from typing import Iterable, Mapping


_ENTRY_START = re.compile(r'@(?P<entry_type>[A-Za-z][A-Za-z0-9_-]*)\s*(?P<delimiter>[{(])')
_KEY_PATTERN = re.compile(r'^[^\s,{}()]+$')
_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')
_SPECIAL_BLOCK_TYPES = frozenset(('comment', 'preamble', 'string'))
_COMMON_FIELDS = (
    'author', 'title', 'year', 'journal', 'booktitle', 'publisher', 'volume',
    'number', 'pages', 'doi', 'url', 'editor', 'edition', 'address', 'month',
    'note', 'series', 'institution', 'school', 'howpublished', 'keywords',
)
_COMMON_ENTRY_TYPES = (
    'article', 'book', 'booklet', 'conference', 'inbook', 'incollection',
    'inproceedings', 'manual', 'mastersthesis', 'misc', 'phdthesis',
    'proceedings', 'techreport', 'unpublished',
)


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class BibTeXEntryError(ValueError):
    '''A user-safe error raised for invalid or unsafe bibliography edits.'''


@dataclass(frozen=True)
class BibTeXEntry:
    '''One source-backed BibTeX entry, including its exact text range.'''

    key: str
    entry_type: str
    fields: tuple[tuple[str, str], ...]
    start: int
    end: int
    raw: str
    field_kinds: tuple[tuple[str, str], ...] = ()

    @property
    def field_map(self) -> dict[str, str]:
        return dict(self.fields)

    def get(self, name: str, default: str = '') -> str:
        return self.field_map.get(name.lower(), default)


@dataclass(frozen=True)
class BibTeXParseResult:
    '''The safely indexed entries and non-fatal parsing diagnostics for text.'''

    entries: tuple[BibTeXEntry, ...]
    diagnostics: tuple[str, ...]

    @property
    def has_errors(self) -> bool:
        return bool(self.diagnostics)


class BibTeXEntryStore:
    '''Read, search and patch BibTeX entries without whole-file reformatting.'''

    def __init__(self, text: str):
        if not isinstance(text, str):
            raise TypeError('text must be a string')
        self.text = text
        self.result = self.parse(text)

    @property
    def entries(self) -> tuple[BibTeXEntry, ...]:
        return self.result.entries

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self.result.diagnostics

    @staticmethod
    def common_entry_types() -> tuple[str, ...]:
        return _COMMON_ENTRY_TYPES

    @staticmethod
    def common_fields() -> tuple[str, ...]:
        return _COMMON_FIELDS

    @classmethod
    def parse(cls, text: str) -> BibTeXParseResult:
        '''Index safe entry blocks while leaving malformed content untouched.'''
        entries: list[BibTeXEntry] = []
        diagnostics: list[str] = []
        offset = 0
        while True:
            match = _ENTRY_START.search(text, offset)
            if match is None:
                break
            entry_type = match.group('entry_type').lower()
            opening = match.group('delimiter')
            closing = '}' if opening == '{' else ')'
            start = match.start()
            end = _scan_balanced_block(text, match.end() - 1, opening, closing)
            if end is None:
                diagnostics.append(_('An entry beginning at character {position} is not closed').format(
                    position=start + 1,
                ))
                # The following text belongs to an ambiguous, unfinished block.
                # Do not surface a literal @... in it as a false editable entry.
                break
            raw = text[start:end]
            offset = end
            if entry_type in _SPECIAL_BLOCK_TYPES:
                continue
            entry = _parse_entry(raw, start, entry_type, opening)
            if isinstance(entry, str):
                diagnostics.append(entry)
                continue
            entries.append(entry)

        keys: set[str] = set()
        for entry in entries:
            if entry.key in keys:
                diagnostics.append(_('The citation key “{key}” is used more than once').format(key=entry.key))
            keys.add(entry.key)
        return BibTeXParseResult(tuple(entries), tuple(diagnostics))

    def list_entries(self, query: str = '', sort_by: str = 'key') -> tuple[BibTeXEntry, ...]:
        '''Return filtered entries with deterministic key, title, author, or year order.'''
        sort_fields = {
            'key': lambda entry: entry.key,
            'title': lambda entry: entry.get('title'),
            'author': lambda entry: entry.get('author'),
            'year': lambda entry: entry.get('year'),
        }
        if sort_by not in sort_fields:
            raise ValueError('sort_by must be key, title, author, or year')
        needle = (query or '').casefold().strip()
        result = []
        for entry in self.entries:
            searchable = ' '.join((
                entry.key,
                entry.entry_type,
                entry.get('title'),
                entry.get('author'),
                entry.get('year'),
            )).casefold()
            if not needle or needle in searchable:
                result.append(entry)
        selected_field = sort_fields[sort_by]
        return tuple(sorted(
            result,
            key=lambda entry: (selected_field(entry).casefold(), entry.key.casefold(), entry.key),
        ))

    def get_entry(self, key: str) -> BibTeXEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def add_entry(self, entry_type: str, key: str, fields: Mapping[str, str]) -> str:
        '''Return text with a formatted new entry appended; never mutate self.'''
        normalized_type = _validate_entry_type(entry_type)
        normalized_key = _validate_key(key)
        if self.get_entry(normalized_key) is not None:
            raise BibTeXEntryError(_('The citation key “{key}” already exists').format(key=normalized_key))
        rendered = render_entry(normalized_type, normalized_key, fields)
        if not self.text:
            return rendered + '\n'
        separator = '' if self.text.endswith(('\n', '\r')) else '\n'
        if self.text.rstrip('\r\n'):
            separator += '\n'
        return self.text + separator + rendered + _line_ending_for(self.text)

    def update_entry(self, key: str, entry_type: str, new_key: str,
                     fields: Mapping[str, str]) -> str:
        '''Return text with exactly one existing entry range replaced.'''
        matching_entries = [entry for entry in self.entries if entry.key == key]
        if not matching_entries:
            raise BibTeXEntryError(_('The citation key “{key}” no longer exists').format(key=key))
        if len(matching_entries) != 1:
            raise BibTeXEntryError(_('The citation key “{key}” is ambiguous').format(key=key))
        entry = matching_entries[0]
        normalized_key = _validate_key(new_key)
        if normalized_key != key and self.get_entry(normalized_key) is not None:
            raise BibTeXEntryError(_('The citation key “{key}” already exists').format(key=normalized_key))
        rendered = render_entry(entry_type, normalized_key, fields,
                                line_ending=_line_ending_for(entry.raw))
        return self.text[:entry.start] + rendered + self.text[entry.end:]

    def format_bibliography(self) -> str:
        '''Return text with every safely parsed entry rewritten in canonical style.

        Fields are reordered into :data:`common_fields` order (unknown fields
        follow alphabetically), names and equals signs are aligned, and each
        entry is re-indented.  Values keep their original form: bare macro
        references such as ``journal`` stay bare, quoted values become braced.
        Unparsable blocks, comments, ``@string``, ``@preamble``, entry order,
        and all surrounding whitespace are left byte-for-byte unchanged, so
        formatting is safe even when diagnostics were reported.
        '''
        pieces: list[str] = []
        cursor = 0
        for entry in self.entries:
            pieces.append(self.text[cursor:entry.start])
            pieces.append(render_entry(
                entry.entry_type, entry.key, entry.field_map,
                line_ending=_line_ending_for(entry.raw), align_names=True,
                value_kinds=dict(entry.field_kinds),
            ))
            cursor = entry.end
        pieces.append(self.text[cursor:])
        return ''.join(pieces)

    def delete_entry(self, key: str) -> str:
        '''Return text without one entry and one adjacent separator if present.'''
        matching_entries = [entry for entry in self.entries if entry.key == key]
        if not matching_entries:
            raise BibTeXEntryError(_('The citation key “{key}” no longer exists').format(key=key))
        if len(matching_entries) != 1:
            raise BibTeXEntryError(_('The citation key “{key}” is ambiguous').format(key=key))
        entry = matching_entries[0]
        start, end = entry.start, entry.end
        if self.text[end:end + 2] == '\r\n':
            end += 2
            if self.text[end:end + 2] == '\r\n':
                end += 2
        elif self.text[end:end + 1] == '\n':
            end += 1
            if self.text[end:end + 1] == '\n':
                end += 1
        elif start > 0 and self.text[start - 2:start] == '\r\n':
            start -= 2
        elif start > 0 and self.text[start - 1:start] == '\n':
            start -= 1
        return self.text[:start] + self.text[end:]


def render_entry(entry_type: str, key: str, fields: Mapping[str, str],
                 line_ending: str = '\n', align_names: bool = False,
                 value_kinds: Mapping[str, str] | None = None) -> str:
    '''Render one validated entry using predictable, local formatting only.

    With ``align_names`` the field names are padded so equals signs line up.
    ``value_kinds`` maps a field name to its source form; bare macro values
    are re-emitted without braces while every other value becomes braced.
    '''
    normalized_type = _validate_entry_type(entry_type)
    normalized_key = _validate_key(key)
    normalized_fields = _ordered_fields(fields)
    if not normalized_fields:
        return f'@{normalized_type}{{{normalized_key}}}'
    kinds = dict(value_kinds) if value_kinds else {}
    width = max(len(name) for name in normalized_fields) if align_names else 0
    body = [f'@{normalized_type}{{{normalized_key},']
    field_items = list(normalized_fields.items())
    for index, (name, value) in enumerate(field_items):
        comma = ',' if index < len(field_items) - 1 else ''
        label = name.ljust(width) if align_names else name
        if kinds.get(name) == 'bare':
            rendered_value = value
        else:
            rendered_value = f'{{{value}}}'
        body.append(f'  {label} = {rendered_value}{comma}')
    body.append('}')
    return line_ending.join(body)


def _ordered_fields(fields: Mapping[str, str]) -> dict[str, str]:
    '''Return validated fields in canonical order, unknown names alphabetically.'''
    normalized = _normalize_fields(fields)
    known = [name for name in _COMMON_FIELDS if name in normalized]
    unknown = sorted(name for name in normalized if name not in _COMMON_FIELDS)
    return {name: normalized[name] for name in known + unknown}


def _scan_balanced_block(text: str, opening_offset: int, opening: str,
                         closing: str) -> int | None:
    '''Return the offset after a matching block while honoring strings/comments.'''
    depth = 1
    quote = False
    escaped = False
    offset = opening_offset + 1
    while offset < len(text):
        character = text[offset]
        if escaped:
            escaped = False
        elif character == '\\':
            escaped = True
        elif quote:
            if character == '"':
                quote = False
        elif character == '"':
            quote = True
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return offset + 1
        offset += 1
    return None


def _parse_entry(raw: str, start: int, entry_type: str, delimiter: str) -> BibTeXEntry | str:
    body = raw[raw.find(delimiter) + 1:-1]
    comma = _find_unquoted_comma(body)
    if comma is None:
        return _('The {type} entry at character {position} has no citation key').format(
            type=entry_type, position=start + 1,
        )
    key = body[:comma].strip()
    try:
        key = _validate_key(key)
        parsed = _parse_fields(body[comma + 1:])
    except BibTeXEntryError as error:
        return _('The entry at character {position} cannot be edited safely: {reason}').format(
            position=start + 1, reason=str(error),
        )
    fields = tuple((name, value) for name, (value, _) in parsed.items())
    kinds = tuple((name, kind) for name, (_, kind) in parsed.items())
    return BibTeXEntry(key, entry_type, fields, start, start + len(raw), raw, kinds)


def _find_unquoted_comma(text: str) -> int | None:
    depth = 0
    quote = False
    escaped = False
    for offset, character in enumerate(text):
        if escaped:
            escaped = False
        elif character == '\\':
            escaped = True
        elif quote:
            if character == '"':
                quote = False
        elif character == '"':
            quote = True
        elif character == '{':
            depth += 1
        elif character == '}' and depth:
            depth -= 1
        elif character == ',' and depth == 0:
            return offset
    return None


def _parse_fields(text: str) -> dict[str, tuple[str, str]]:
    '''Return each lowercased field name mapped to its value and source form.'''
    fields: dict[str, tuple[str, str]] = {}
    offset = 0
    length = len(text)
    while offset < length:
        offset = _skip_space_and_commas(text, offset)
        if offset >= length:
            break
        name_start = offset
        while offset < length and (text[offset].isalnum() or text[offset] in '_-'):
            offset += 1
        name = text[name_start:offset].lower()
        if not name or not _IDENTIFIER_PATTERN.fullmatch(name):
            raise BibTeXEntryError(_('A field name is invalid'))
        offset = _skip_whitespace(text, offset)
        if offset >= length or text[offset] != '=':
            raise BibTeXEntryError(_('The field “{field}” has no value').format(field=name))
        offset = _skip_whitespace(text, offset + 1)
        if offset >= length:
            raise BibTeXEntryError(_('The field “{field}” has no value').format(field=name))
        value, kind, offset = _read_field_value(text, offset)
        if name in fields:
            raise BibTeXEntryError(_('The field “{field}” occurs more than once').format(field=name))
        fields[name] = (value, kind)
    return fields


def _read_field_value(text: str, offset: int) -> tuple[str, str, int]:
    '''Return a field value with its form: braced, quoted, or bare.'''
    character = text[offset]
    if character == '{':
        end = _scan_balanced_block(text, offset, '{', '}')
        if end is None:
            raise BibTeXEntryError(_('A braced field value is not closed'))
        return text[offset + 1:end - 1], 'braced', end
    if character == '"':
        end = _scan_quoted_value(text, offset)
        if end is None:
            raise BibTeXEntryError(_('A quoted field value is not closed'))
        return text[offset + 1:end - 1], 'quoted', end
    end = offset
    while end < len(text) and text[end] not in ',\r\n':
        end += 1
    value = text[offset:end].strip()
    if not value:
        raise BibTeXEntryError(_('A field value is empty'))
    return value, 'bare', end


def _scan_quoted_value(text: str, opening_offset: int) -> int | None:
    escaped = False
    for offset in range(opening_offset + 1, len(text)):
        character = text[offset]
        if escaped:
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == '"':
            return offset + 1
    return None


def _skip_whitespace(text: str, offset: int) -> int:
    while offset < len(text) and text[offset].isspace():
        offset += 1
    return offset


def _skip_space_and_commas(text: str, offset: int) -> int:
    while offset < len(text) and (text[offset].isspace() or text[offset] == ','):
        offset += 1
    return offset


def _normalize_fields(fields: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(fields, Mapping):
        raise BibTeXEntryError(_('Fields must be a mapping'))
    result: dict[str, str] = {}
    for name, value in fields.items():
        normalized_name = str(name).strip().lower()
        if not _IDENTIFIER_PATTERN.fullmatch(normalized_name):
            raise BibTeXEntryError(_('The field name “{field}” is invalid').format(field=name))
        if value is None:
            continue
        normalized_value = str(value).strip()
        if not normalized_value:
            continue
        if '\x00' in normalized_value:
            raise BibTeXEntryError(_('A field value contains an invalid character'))
        result[normalized_name] = normalized_value
    return result


def _validate_key(key: str) -> str:
    normalized = str(key).strip()
    if not normalized or not _KEY_PATTERN.fullmatch(normalized):
        raise BibTeXEntryError(_('A citation key must not contain whitespace, commas, or braces'))
    return normalized


def _validate_entry_type(entry_type: str) -> str:
    normalized = str(entry_type).strip().lower()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise BibTeXEntryError(_('The entry type is invalid'))
    return normalized


def _line_ending_for(text: str) -> str:
    return '\r\n' if '\r\n' in text else '\n'

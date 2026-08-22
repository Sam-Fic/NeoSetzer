#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Versioned, gi-free state helpers for the document wizard.

Named wizard presets are user data and can outlive individual wizard fields.
This module defines the accepted state shape in one place, so old, partial or
manually damaged presets are normalised before GTK pages or template generators
consume them. Unknown keys are deliberately not copied into active state: they
cannot affect generated LaTeX and would otherwise make the persisted schema
ambiguous.
"""

from copy import deepcopy


WIZARD_STATE_VERSION = 1

DOCUMENT_CLASSES = frozenset((
    'article', 'report', 'book', 'letter', 'beamer',
    'scrartcl', 'scrreprt', 'scrbook', 'scrlttr2',
))

PAGE_FORMATS = frozenset((
    'US Letter', 'US Legal', 'A4', 'A5', 'B5',
))

SECTIONING_LEVELS = frozenset(('section', 'chapter', 'none'))
ARTICLE_STYLE_CLASSES = frozenset(('article', 'scrartcl'))
CHAPTER_STYLE_CLASSES = frozenset(('report', 'book', 'scrreprt', 'scrbook'))
FONT_PACKAGES = frozenset(('lmodern', 'fontspec', 'none'))
BEAMER_THEMES = frozenset((
    'Warsaw', 'Malmoe', 'Luebeck', 'Copenhagen', 'Szeged', 'Singapore',
    'Frankfurt', 'Darmstadt', 'Dresden', 'Ilmenau', 'Berlin', 'Hannover',
    'Marburg', 'Goettingen', 'PaloAlto', 'Berkeley', 'Montpellier',
    'JuanLesPins', 'Antibes', 'Rochester', 'Pittsburgh', 'EastLansing',
    'CambridgeUS', 'AnnArbor', 'Madrid', 'Boadilla', 'Bergen', 'default',
))

_PACKAGE_DEFAULTS = {
    'ams': True,
    'graphicx': True,
    'color': False,
    'xcolor': False,
    'url': False,
    'theorem': False,
    'textcomp': False,
    'listings': False,
    'hyperref': False,
    'glossaries': False,
    'parskip': True,
}


# The scalar rule table intentionally mirrors generated LaTeX data, rather than
# UI widgets. This keeps preset normalisation usable in headless tests.
_STRING_KEYS = frozenset((
    'title', 'author', 'date', 'custom_packages',
))

_LETTER_TEXT_KEYS = frozenset((
    'sender_name', 'sender_address', 'sender_phone',
    'recipient_name', 'recipient_address', 'recipient_phone',
    'signature', 'opening', 'closing',
))


class WizardStateError(ValueError):
    """Raised only for invalid default-state input supplied by the application."""


def build_default_wizard_state(default_page_format, languages):
    """Return a complete, JSON-compatible state for a new wizard session.

    ``languages`` is supplied by :class:`LaTeXDB` at the application boundary.
    Keeping this function gi-free makes the schema directly testable.
    """
    if default_page_format not in PAGE_FORMATS:
        raise WizardStateError('Unsupported default page format')
    if not _is_languages(languages):
        raise WizardStateError('Languages must be a non-empty mapping of strings')

    page_settings = {
        'page_format': default_page_format,
        'font_size': 10,
        'option_twocolumn': False,
        'option_default_margins': True,
        'margin_left': 3.5,
        'margin_right': 3.5,
        'margin_top': 3.5,
        'margin_bottom': 3.5,
        'is_landscape': False,
    }
    letter_settings = dict(page_settings)
    letter_settings.update({key: '' for key in _LETTER_TEXT_KEYS})
    letter_settings.update({
        'option_window_address': True,
        'option_backaddress': True,
        'option_foldmarks': True,
    })

    return {
        'schema_version': WIZARD_STATE_VERSION,
        'document_class': 'article',
        'title': '',
        'author': '',
        'date': '\\today',
        'custom_packages': '',
        'languages': deepcopy(languages),
        'sectioning': 'section',
        'font_package': 'lmodern',
        'packages': dict(_PACKAGE_DEFAULTS),
        'article': dict(page_settings),
        'report': dict(page_settings),
        'book': dict(page_settings),
        'letter': letter_settings,
        'beamer': {
            'theme': 'default',
            'option_show_navigation': True,
            'option_top_align': True,
        },
    }


def normalise_wizard_state(candidate, defaults):
    """Return a safe complete state using valid values from ``candidate``.

    The function never mutates either argument. Missing or malformed values fall
    back to ``defaults``; existing valid values win. The returned mapping always
    carries the current schema version and only known keys, making repeated calls
    idempotent.
    """
    if not isinstance(defaults, dict):
        raise WizardStateError('Defaults must be a dictionary')
    result = deepcopy(defaults)
    if not isinstance(candidate, dict):
        return result

    result['schema_version'] = WIZARD_STATE_VERSION
    _copy_enum(candidate, result, 'document_class', DOCUMENT_CLASSES)
    _copy_strings(candidate, result, _STRING_KEYS)
    _copy_enum(candidate, result, 'sectioning', SECTIONING_LEVELS)
    _copy_enum(candidate, result, 'font_package', FONT_PACKAGES)

    if _is_languages(candidate.get('languages')):
        result['languages'] = deepcopy(candidate['languages'])

    _normalise_packages(candidate.get('packages'), result['packages'])
    for key in ('article', 'report', 'book', 'letter'):
        _normalise_page_settings(candidate.get(key), result[key],
                                 include_letter_text=(key == 'letter'))
    _normalise_beamer(candidate.get('beamer'), result['beamer'])
    return result


def sectioning_options_for_document_class(document_class):
    '''Return sectioning commands that make sense for the selected class.'''
    if document_class in ARTICLE_STYLE_CLASSES:
        return ('section', 'none')
    if document_class in CHAPTER_STYLE_CLASSES:
        return ('chapter', 'section', 'none')
    return ('none',)


def default_sectioning_for_document_class(document_class):
    '''Return the least surprising first sectioning command for a document class.'''
    return sectioning_options_for_document_class(document_class)[0]


def _copy_enum(candidate, result, key, permitted):
    value = candidate.get(key)
    if isinstance(value, str) and value in permitted:
        result[key] = value


def _copy_strings(candidate, result, keys):
    for key in keys:
        value = candidate.get(key)
        if isinstance(value, str):
            result[key] = value


def _is_languages(value):
    return (isinstance(value, dict) and bool(value)
            and all(isinstance(key, str) and isinstance(name, str)
                    for key, name in value.items()))


def _normalise_packages(candidate, result):
    if not isinstance(candidate, dict):
        return
    for key in result:
        value = candidate.get(key)
        if isinstance(value, bool):
            result[key] = value


def _normalise_page_settings(candidate, result, include_letter_text=False):
    if not isinstance(candidate, dict):
        return
    _copy_enum(candidate, result, 'page_format', PAGE_FORMATS)

    font_size = candidate.get('font_size')
    if isinstance(font_size, int) and not isinstance(font_size, bool) and 6 <= font_size <= 18:
        result['font_size'] = font_size

    for key in ('option_twocolumn', 'option_default_margins', 'is_landscape'):
        value = candidate.get(key)
        if isinstance(value, bool):
            result[key] = value

    for key in ('margin_left', 'margin_right', 'margin_top', 'margin_bottom'):
        value = candidate.get(key)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= value <= 5):
            result[key] = value

    if include_letter_text:
        _copy_strings(candidate, result, _LETTER_TEXT_KEYS)
        for key in ('option_window_address', 'option_backaddress', 'option_foldmarks'):
            value = candidate.get(key)
            if isinstance(value, bool):
                result[key] = value


def _normalise_beamer(candidate, result):
    if not isinstance(candidate, dict):
        return
    _copy_enum(candidate, result, 'theme', BEAMER_THEMES)
    for key in ('option_show_navigation', 'option_top_align'):
        value = candidate.get(key)
        if isinstance(value, bool):
            result[key] = value

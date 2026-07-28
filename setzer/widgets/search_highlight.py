#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
# along with this program. If not, see <http://www.gnu.org/licenses/

'''Helpers to bold search-hit substrings in Adw.ActionRow titles/subtitles.

Adw.ActionRow.set_title()/set_subtitle() only render Pango markup when the
row's ``use-markup`` property is ``True``. These helpers return markup with
the matched part wrapped in ``<b>``; special chars (``& < >``) are escaped so
arbitrary LaTeX/text content is safe to display.
'''

import html
import re


def escape_markup(text):
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))


def highlight(text, query):
    '''Bold the first substring of ``text`` matching ``query`` (case-insensitive).

    Returns plain escaped text when ``query`` is empty or not found.
    '''
    if not query:
        return escape_markup(text)
    text_lower = text.lower()
    query_lower = query.lower()
    index = text_lower.find(query_lower)
    if index < 0:
        return escape_markup(text)
    end = index + len(query_lower)
    return (escape_markup(text[:index]) + '<b>'
            + escape_markup(text[index:end]) + '</b>'
            + escape_markup(text[end:]))


def highlight_fuzzy(text, query):
    '''Bold the characters of ``text`` that match ``query`` as a subsequence.

    Used for fuzzy (scattered-character) matching, e.g. the document switcher.
    Returns plain escaped text when ``query`` is empty or not a subsequence.
    '''
    if not query:
        return escape_markup(text)
    text_lower = text.lower()
    query_lower = query.lower()
    out = []
    qi = 0
    in_bold = False
    for i, ch in enumerate(text):
        if qi < len(query_lower) and text_lower[i] == query_lower[qi]:
            if not in_bold:
                out.append('<b>')
                in_bold = True
            out.append(escape_markup(ch))
            qi += 1
        else:
            if in_bold:
                out.append('</b>')
                in_bold = False
            out.append(escape_markup(ch))
    if in_bold:
        out.append('</b>')
    return ''.join(out)


def highlight_words(text, words, unescape_html=False):
    '''Bold every occurrence of any word in ``words`` (case-insensitive).

    ``words`` is a list of lowercased query words. With ``unescape_html=True``
    the text is first decoded from HTML entities (help-panel content) then
    re-escaped for safe markup, so highlight markers never collide with
    literal ``<``/``>``/``&`` in the source. ``\\x00``/``\\x01`` are control
    chars that cannot appear in the text and mark the bold spans.
    '''
    if unescape_html:
        text = html.unescape(text)
    if words:
        pattern = re.compile('|'.join(re.escape(w) for w in words), re.IGNORECASE)
        text = pattern.sub(lambda m: '\x00' + m.group(0) + '\x01', text)
    text = escape_markup(text)
    text = text.replace('\x00', '<b>').replace('\x01', '</b>')
    return text

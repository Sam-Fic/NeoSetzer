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

'''Pure-text helpers for BibTeX and LaTeX field editing.

This module is intentionally gi-free: it operates on plain strings and is
imported by the bibliography manager dialog, the LaTeX context menu and the
unit test suite without requiring a GTK display.

Three independent helpers are exposed:

* :func:`protect_cases` — wrap case-sensitive words in ``{...}`` so BibTeX
  does not silently lowercase them.  Idempotent on already-protected words.
* :func:`unicode_to_latex` — replace accented letters, Greek letters and
  common special characters with their ``\\command`` LaTeX equivalent.
* :func:`latex_to_unicode` — the inverse of :func:`unicode_to_latex`,
  unescaping ``\\command`` LaTeX sequences back to plain Unicode.
'''

from __future__ import annotations

import re


# --- Mapping tables ------------------------------------------------------
#
# All tables map a single source character to the LaTeX body that should
# replace it (e.g. ``"é"`` -> ``"\\'{e}"``).  The body always uses the
# braced form (``\\'{e}``) so multi-letter arguments are unambiguous.

def _a(letter: str) -> str:
    return "\\'" + "{" + letter + "}"


def _g(letter: str) -> str:
    return "\\`" + "{" + letter + "}"


def _h(letter: str) -> str:
    return "\\^" + "{" + letter + "}"


def _u(letter: str) -> str:
    return '\\"' + "{" + letter + "}"


def _t(letter: str) -> str:
    return "\\~" + "{" + letter + "}"


def _r(letter: str) -> str:
    return "\\r" + "{" + letter + "}"


def _e(letter: str) -> str:
    return "\\=" + "{" + letter + "}"


def _b(letter: str) -> str:
    return "\\u" + "{" + letter + "}"


def _k(letter: str) -> str:
    return "\\k" + "{" + letter + "}"


def _d(letter: str) -> str:
    return "\\." + "{" + letter + "}"


def _c(letter: str) -> str:
    return "\\c" + "{" + letter + "}"


def _lig(name: str) -> str:
    return "\\" + name + "{}"


# Latin accented letters -> LaTeX accent command.  Lower-case first so the
# sharp-s mapping (``ß`` -> ``\\ss{}``) does not shadow a hypothetical
# ``s`` -> ``\\ss`` that we never define.
LATIN_ACCENTS: tuple[tuple[str, str], ...] = (
    ("á", _a("a")), ("à", _g("a")), ("â", _h("a")), ("ä", _u("a")),
    ("ã", _t("a")), ("å", _r("a")), ("ā", _e("a")), ("ă", _b("a")),
    ("ą", _k("a")), ("æ", _lig("ae")), ("ç", _c("c")),
    ("é", _a("e")), ("è", _g("e")), ("ê", _h("e")), ("ë", _u("e")),
    ("ē", _e("e")), ("ė", _d("e")), ("ę", _k("e")),
    ("í", _a("i")), ("ì", _g("i")), ("î", _h("i")), ("ï", _u("i")),
    ("ī", _e("i")), ("į", _k("i")),
    ("ñ", _t("n")),
    ("ó", _a("o")), ("ò", _g("o")), ("ô", _h("o")), ("ö", _u("o")),
    ("õ", _t("o")), ("ø", _lig("o")), ("ō", _e("o")), ("œ", _lig("oe")),
    ("ú", _a("u")), ("ù", _g("u")), ("û", _h("u")), ("ü", _u("u")),
    ("ū", _e("u")),
    ("ý", _a("y")), ("ÿ", _u("y")),
    ("ß", _lig("ss")),
    ("Á", _a("A")), ("À", _g("A")), ("Â", _h("A")), ("Ä", _u("A")),
    ("Ã", _t("A")), ("Å", _r("A")), ("Ā", _e("A")), ("Ă", _b("A")),
    ("Æ", _lig("AE")),
    ("Ç", _c("C")),
    ("É", _a("E")), ("È", _g("E")), ("Ê", _h("E")), ("Ë", _u("E")),
    ("Ē", _e("E")), ("Ė", _d("E")), ("Ę", _k("E")),
    ("Í", _a("I")), ("Ì", _g("I")), ("Î", _h("I")), ("Ï", _u("I")),
    ("Ī", _e("I")),
    ("Ñ", _t("N")),
    ("Ó", _a("O")), ("Ò", _g("O")), ("Ô", _h("O")), ("Ö", _u("O")),
    ("Õ", _t("O")), ("Ø", _lig("O")), ("Ō", _e("O")), ("Œ", _lig("OE")),
    ("Ú", _a("U")), ("Ù", _g("U")), ("Û", _h("U")), ("Ü", _u("U")),
    ("Ū", _e("U")),
    ("Ý", _a("Y")),
)

# Greek alphabet -> LaTeX command.  Both lower-case and upper-case.
GREEK_LETTERS: tuple[tuple[str, str], ...] = (
    ("α", "\\alpha"), ("β", "\\beta"), ("γ", "\\gamma"), ("δ", "\\delta"),
    ("ε", "\\epsilon"), ("ζ", "\\zeta"), ("η", "\\eta"), ("θ", "\\theta"),
    ("ι", "\\iota"), ("κ", "\\kappa"), ("λ", "\\lambda"), ("μ", "\\mu"),
    ("ν", "\\nu"), ("ξ", "\\xi"), ("π", "\\pi"), ("ρ", "\\rho"),
    ("σ", "\\sigma"), ("τ", "\\tau"), ("υ", "\\upsilon"), ("φ", "\\phi"),
    ("χ", "\\chi"), ("ψ", "\\psi"), ("ω", "\\omega"),
    ("Γ", "\\Gamma"), ("Δ", "\\Delta"), ("Θ", "\\Theta"), ("Λ", "\\Lambda"),
    ("Ξ", "\\Xi"), ("Π", "\\Pi"), ("Σ", "\\Sigma"), ("Υ", "\\Upsilon"),
    ("Φ", "\\Phi"), ("Ψ", "\\Psi"), ("Ω", "\\Omega"),
)

# Common punctuation / special characters.  LaTeX treats several ASCII
# characters as syntax and they must be escaped even in regular prose.
SPECIAL_CHARS: tuple[tuple[str, str], ...] = (
    ("&", "\\&"), ("%", "\\%"), ("$", "\\$"), ("#", "\\#"),
    ("_", "\\_"), ("{", "\\{"), ("}", "\\}"),
    ("~", "\\textasciitilde{}"), ("^", "\\textasciicircum{}"),
    ("\\", "\\textbackslash{}"),
)

# Single mapping table used for the forward direction.  Special chars come
# first so a single ``\`` always escapes as ``\\textbackslash{}`` before
# the Greek letter pass ever sees it.
_UNICODE_TO_LATEX_TABLE: tuple[tuple[str, str], ...] = (
    *SPECIAL_CHARS, *LATIN_ACCENTS, *GREEK_LETTERS,
)

# Inverse table for :func:`latex_to_unicode`.  Keys are the LaTeX
# sequences we emit (with ``{...}`` arguments).  Accent commands are
# derived from ``LATIN_ACCENTS`` so the two directions can never drift.
_LATEX_TO_UNICODE_BASE: dict[str, str] = {
    "\\&": "&", "\\%": "%", "\\$": "$", "\\#": "#", "\\_": "_",
    "\\{": "{", "\\}": "}",
    "\\textasciitilde{}": "~", "\\textasciicircum{}": "^",
    "\\textbackslash{}": "\\",
    "\\ae{}": "æ", "\\AE{}": "Æ",
    "\\oe{}": "œ", "\\OE{}": "Œ",
    "\\o{}": "ø", "\\O{}": "Ø",
    "\\c{c}": "ç", "\\c{C}": "Ç",
    "\\ss{}": "ß",
}


# --- protect_cases --------------------------------------------------------

# A word is "case-sensitive" if it contains at least one Latin capital
# letter.  We also require the first character to be a letter so that
# mid-sentence capitals (e.g. "USA") are not matched inside a longer
# word like "because".
_PROTECT_WORD_PATTERN = re.compile(r'\b(?=[A-Za-z0-9]*[A-Z])[A-Za-z][A-Za-z0-9]*')


def protect_cases(text: str) -> str:
    '''Wrap each case-sensitive word in ``text`` with ``{...}``.

    A "case-sensitive" word is one that contains at least one capital
    letter and starts with a letter — for example ``NASA`` or ``LaTeX``.
    The transformation is idempotent: already-protected words
    (``{NASA}``) are not double-wrapped (``{{NASA}}``) and the braces
    that wrap a value are detected so the inner text is not re-bracketed.
    Punctuation and whitespace are passed through verbatim.
    '''
    if not text:
        return text
    return _walk_protect(text)


def _walk_protect(text: str) -> str:
    '''Walk ``text`` skipping brace- and quote-protected regions.

    The walker treats an entire ``{...}`` block — including the braces
    themselves — as already protected.  Its inner text is appended to
    the output verbatim, without re-scanning, so words like ``{NASA}``
    are not double-wrapped into ``{{NASA}}`` on a second pass.
    '''
    pieces: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote = False
    escaped = False
    index = 0
    length = len(text)

    def flush():
        if buffer:
            pieces.append(_protect_in_plain("".join(buffer)))
            buffer.clear()

    while index < length:
        character = text[index]
        if depth or quote:
            pieces.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif quote:
                if character == '"':
                    quote = False
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
        elif escaped:
            buffer.append(character)
            escaped = False
        elif character == "\\":
            buffer.append(character)
            escaped = True
        elif character == '"':
            flush()
            pieces.append('"')
            quote = True
        elif character == "{":
            flush()
            pieces.append("{")
            depth = 1
        else:
            buffer.append(character)
        index += 1
    flush()
    return "".join(pieces)


def _protect_in_plain(plain: str) -> str:
    if not plain:
        return plain
    return _PROTECT_WORD_PATTERN.sub(
        lambda match: "{" + match.group(0) + "}", plain,
    )


# --- Unicode <-> LaTeX ----------------------------------------------------

# A LaTeX command is ``\`` followed by either a one-char accent (e.g.
# ``\'``, ``\"``, ``\^``, ``\```) or an ASCII letter sequence, optionally
# followed by ``{...}`` of arguments.  We accept both ``\'e`` (no braces
# around a one-letter argument) and ``\'{e}`` (the form we emit).  The
# non-letter branch covers all ``\``+non-letter LaTeX escape sequences
# (``\&``, ``\%``, ``\$``, ``\#``, ``\_``, ``\{``, ``\}``, ``\\``, ``\~``
# and ``\^``).
_LATEX_COMMAND_PATTERN = re.compile(
    r"\\(?:"
    r"(?P<name>[A-Za-z]+|[!\"#\$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])"
    r"(?P<args>(?:\{[^}]*\})*)"
    r")"
)


def unicode_to_latex(text: str) -> str:
    '''Replace Unicode characters in ``text`` with their LaTeX equivalents.

    Latin accents, Greek letters and the LaTeX special characters
    (``&%#$_{}~\\``) are translated.  All other characters (including
    spaces, digits and ordinary ASCII letters) are preserved verbatim.
    Already-LaTeX sequences are not touched: the source ``\alpha`` is only
    translated if the corresponding Unicode letter ``α`` is present.
    '''
    if not text:
        return text
    pieces: list[str] = []
    for character in text:
        replacement = _lookup_unicode_replacement(character)
        pieces.append(replacement if replacement is not None else character)
    return "".join(pieces)


def _lookup_unicode_replacement(character: str) -> str | None:
    for source, target in _UNICODE_TO_LATEX_TABLE:
        if source == character:
            return target
    return None


def latex_to_unicode(text: str) -> str:
    '''Replace LaTeX command sequences in ``text`` with their Unicode form.

    The inverse of :func:`unicode_to_latex`: ``\\'{e}`` becomes ``é``,
    ``\\alpha`` becomes ``α``, ``\\&`` becomes ``&``.  Sequences that
    have no Unicode equivalent (e.g. ``\\emph``, ``\\frac``) are passed
    through verbatim.  Trailing ``{}`` argument placeholders introduced
    by our own :func:`unicode_to_latex` (e.g. ``\\ss{}``, ``\\ae{}``)
    are consumed.
    '''
    if not text:
        return text

    def replace(match: re.Match) -> str:
        return _lookup_latex_replacement(
            match.group("name"), match.group("args"),
        )

    return _LATEX_COMMAND_PATTERN.sub(replace, text)


def _lookup_latex_replacement(name: str, args: str) -> str:
    '''Return the Unicode replacement for a LaTeX command with ``args``.

    ``args`` is the literal ``{...}`` argument string (possibly empty).
    ``name`` is the command name without the leading backslash.  Returns
    the original match (rebuilt from the inputs) when no mapping applies.
    '''
    full = "\\" + name + args
    if not args:
        if full in _LATEX_TO_UNICODE_BASE:
            return _LATEX_TO_UNICODE_BASE[full]
        for source, target in GREEK_LETTERS:
            if target == full:
                return source
        return full

    inner = _unwrap_args(args)
    if inner is None:
        return full

    if full in _LATEX_TO_UNICODE_BASE:
        return _LATEX_TO_UNICODE_BASE[full]

    for source, target in LATIN_ACCENTS:
        if target == full:
            return source

    if inner == "":
        for source, target in GREEK_LETTERS:
            if target == full:
                return source
    return full


def _unwrap_args(args: str) -> str | None:
    '''Return the inner text of ``{{...}}`` or ``None`` if the shape is wrong.'''
    if not args or len(args) < 2 or not args.startswith("{") or not args.endswith("}"):
        return None
    depth = 0
    for index, character in enumerate(args):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and index != len(args) - 1:
                return None
            if depth == 0 and index == len(args) - 1:
                return args[1:-1]
    return None


# --- Public surface -------------------------------------------------------

__all__ = (
    "protect_cases",
    "unicode_to_latex",
    "latex_to_unicode",
    "LATIN_ACCENTS",
    "GREEK_LETTERS",
    "SPECIAL_CHARS",
)

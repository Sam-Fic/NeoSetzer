#!/usr/bin/env python3
# coding: utf-8

"""Pure helpers for filtering the document wizard's Beamer themes."""


def filter_theme_names(theme_names, query):
    """Return names containing ``query`` with stable order and safe inputs.

    The wizard has a deliberately short fixed catalogue, so simple Unicode
    case-folded substring matching is predictable, supports keyboard search,
    and does not introduce a fuzzy-ranking surprise when a user presses Enter.
    """
    if not isinstance(query, str):
        query = ''
    needle = query.strip().casefold()
    if not needle:
        return tuple(name for name in theme_names if isinstance(name, str))
    return tuple(
        name for name in theme_names
        if isinstance(name, str) and needle in name.casefold())

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Source snippets used when completing LaTeX ``\begin{...}`` environments."""

from __future__ import annotations


_LIST_ENVIRONMENTS = frozenset(('enumerate',))


def get_environment_completion_tail(environment_name: str) -> str:
    r"""Return the body placeholder and matching ``\end`` for an environment.

    ``enumerate`` is a list environment whose body must begin with ``\item``
    to compile.  Other environments preserve NeoSetzer's historical single
    placeholder behavior.
    """

    item_prefix = '\\item ' if environment_name.casefold() in _LIST_ENVIRONMENTS else ''
    return '\n\t' + item_prefix + '•\n\\end{' + environment_name + '}'

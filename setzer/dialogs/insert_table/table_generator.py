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

'''Pure-Python model and LaTeX renderer for the Insert Table dialog.

The dialog deliberately keeps this module free of GTK dependencies.  The
renderer can therefore be tested in headless Meson runs and reused by a future
TSV/CSV import enhancement without coupling table semantics to widgets.
'''

from dataclasses import dataclass
from typing import Sequence


MIN_ROWS = 1
MAX_ROWS = 30
MIN_COLUMNS = 1
MAX_COLUMNS = 12
ALIGNMENTS = ('l', 'c', 'r')
STYLE_PLAIN = 'plain'
STYLE_BOOKTABS = 'booktabs'
STYLES = (STYLE_PLAIN, STYLE_BOOKTABS)
ENVIRONMENT_TABULAR = 'tabular'
ENVIRONMENT_LONGTABLE = 'longtable'
ENVIRONMENTS = (ENVIRONMENT_TABULAR, ENVIRONMENT_LONGTABLE)
PLACEMENTS = ('htbp', 'ht', 'h', 't', 'b', 'p', 'H', 'h!')


def resize_cells(cells: Sequence[Sequence[str]], rows: int, columns: int) -> tuple[tuple[str, ...], ...]:
    '''Resize a cell matrix while preserving its overlapping top-left area.'''

    _validate_dimensions(rows, columns)
    result = []
    for row_index in range(rows):
        source_row = cells[row_index] if row_index < len(cells) else ()
        result.append(tuple(
            str(source_row[column_index]) if column_index < len(source_row) else ''
            for column_index in range(columns)
        ))
    return tuple(result)


@dataclass(frozen=True)
class TableSpec:
    '''The complete user-configurable description of a simple LaTeX table.'''

    rows: int = 3
    columns: int = 3
    cells: tuple[tuple[str, ...], ...] = ()
    alignments: tuple[str, ...] = ()
    style: str = STYLE_PLAIN
    environment: str = ENVIRONMENT_TABULAR
    header_row: bool = True
    repeat_header: bool = True
    use_table_environment: bool = True
    placement: str = 'htbp'
    centered: bool = True
    caption: str = ''
    label: str = ''

    def __post_init__(self):
        _validate_dimensions(self.rows, self.columns)
        if self.style not in STYLES:
            raise ValueError(f'Unknown table style: {self.style}')
        if self.environment not in ENVIRONMENTS:
            raise ValueError(f'Unknown table environment: {self.environment}')
        if self.placement not in PLACEMENTS:
            raise ValueError(f'Unknown table placement: {self.placement}')
        if self.environment == ENVIRONMENT_LONGTABLE and self.use_table_environment:
            raise ValueError('longtable cannot be wrapped in a table environment')

        normalized_cells = resize_cells(self.cells, self.rows, self.columns)
        if self.alignments:
            if len(self.alignments) != self.columns:
                raise ValueError('The number of alignments must match the number of columns')
            normalized_alignments = tuple(self.alignments)
        else:
            normalized_alignments = tuple(
                'l' if column_index == 0 else 'c'
                for column_index in range(self.columns)
            )
        invalid_alignments = set(normalized_alignments).difference(ALIGNMENTS)
        if invalid_alignments:
            raise ValueError(f'Unsupported table alignment: {sorted(invalid_alignments)!r}')

        object.__setattr__(self, 'cells', normalized_cells)
        object.__setattr__(self, 'alignments', normalized_alignments)
        object.__setattr__(self, 'caption', self.caption.strip())
        object.__setattr__(self, 'label', self.label.strip())

    @property
    def column_specification(self) -> str:
        return ''.join(self.alignments)

    @property
    def uses_repeated_header(self) -> bool:
        return (self.environment == ENVIRONMENT_LONGTABLE
                and self.header_row
                and self.repeat_header
                and self.rows > 1)

    @property
    def required_packages(self) -> tuple[str, ...]:
        packages = []
        if self.style == STYLE_BOOKTABS:
            packages.append('booktabs')
        if self.environment == ENVIRONMENT_LONGTABLE:
            packages.append('longtable')
        if self.use_table_environment and self.placement == 'H':
            packages.append('float')
        return tuple(packages)

    def render(self) -> str:
        '''Render editable LaTeX source without escaping cell content.

        Cell text is intentionally treated as raw LaTeX.  This permits common
        scientific input such as ``$x^2$`` and ``\\textbf{Header}``, and keeps
        the generated source under the author's control.
        '''

        if self.environment == ENVIRONMENT_LONGTABLE:
            return self._render_longtable()
        return self._render_tabular()

    def _render_tabular(self) -> str:
        lines = []
        if self.use_table_environment:
            lines.append(f'\\begin{{table}}[{self.placement}]')
            if self.centered:
                lines.append('\\centering')
            if self.caption:
                lines.append(f'\\caption{{{self.caption}}}')
            if self.label:
                lines.append(f'\\label{{{self.label}}}')

        lines.append(f'\\begin{{tabular}}{{{self.column_specification}}}')
        lines.extend(self._render_rules_before_rows())
        lines.extend(self._render_rows())
        lines.extend(self._render_rules_after_rows())
        lines.append('\\end{tabular}')

        if self.use_table_environment:
            lines.append('\\end{table}')
        return '\n'.join(lines)

    def _render_longtable(self) -> str:
        lines = [f'\\begin{{longtable}}{{{self.column_specification}}}']
        if self.caption:
            caption = f'\\caption{{{self.caption}}}'
            if self.label:
                caption += f'\\label{{{self.label}}}'
            lines.append(caption + r' \\')
        elif self.label:
            lines.append(f'\\label{{{self.label}}}')

        lines.extend(self._render_rules_before_rows())
        if self.uses_repeated_header:
            lines.append(self._render_row(self.cells[0]))
            lines.extend(self._render_separator_after_header())
            lines.append('\\endfirsthead')
            lines.extend(self._render_rules_before_rows())
            lines.append(self._render_row(self.cells[0]))
            lines.extend(self._render_separator_after_header())
            lines.append('\\endhead')
            row_start = 1
        else:
            row_start = 0

        lines.extend(self._render_rows(row_start))
        lines.extend(self._render_rules_after_rows())
        lines.append('\\end{longtable}')
        return '\n'.join(lines)

    def _render_rules_before_rows(self) -> list[str]:
        return ['\\toprule'] if self.style == STYLE_BOOKTABS else ['\\hline']

    def _render_rules_after_rows(self) -> list[str]:
        return ['\\bottomrule'] if self.style == STYLE_BOOKTABS else []

    def _render_separator_after_header(self) -> list[str]:
        if self.style == STYLE_PLAIN:
            return ['\\hline']
        return ['\\midrule']

    def _render_row(self, row: Sequence[str]) -> str:
        return ' & '.join(row) + r' \\'

    def _render_rows(self, start: int = 0) -> list[str]:
        lines = []
        for row_index in range(start, self.rows):
            lines.append(self._render_row(self.cells[row_index]))
            if self.style == STYLE_PLAIN:
                lines.append('\\hline')
            elif self.header_row and row_index == 0 and self.rows > 1:
                lines.extend(self._render_separator_after_header())
        return lines


def _validate_dimensions(rows: int, columns: int):
    if not MIN_ROWS <= rows <= MAX_ROWS:
        raise ValueError(f'Rows must be between {MIN_ROWS} and {MAX_ROWS}')
    if not MIN_COLUMNS <= columns <= MAX_COLUMNS:
        raise ValueError(f'Columns must be between {MIN_COLUMNS} and {MAX_COLUMNS}')

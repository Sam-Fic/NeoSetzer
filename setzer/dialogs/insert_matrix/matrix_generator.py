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

'''Pure-Python model and LaTeX renderer for the Insert Matrix dialog.

Like the table generator, this module is free of GTK dependencies so the
renderer can be unit tested in headless runs. It answers upstream Setzer
issue #152 (matrix creation): pick one of the amsmath/mathtools matrix
environments, choose the dimensions and optionally a column alignment for
``matrix*``, and render an editable skeleton.

Empty cells are rendered as the editor's ``•`` placeholder so users can Tab
through the freshly inserted matrix and fill in the entries.
'''

from dataclasses import dataclass
from typing import Sequence


MIN_ROWS = 1
MAX_ROWS = 20
MIN_COLUMNS = 1
MAX_COLUMNS = 20

ALIGNMENT_CENTER = 'c'
ALIGNMENT_LEFT = 'l'
ALIGNMENT_RIGHT = 'r'
ALIGNMENTS = (ALIGNMENT_CENTER, ALIGNMENT_LEFT, ALIGNMENT_RIGHT)

ENVIRONMENT_MATRIX = 'matrix'
ENVIRONMENT_MATRIX_STAR = 'matrix*'
ENVIRONMENT_PMATRIX = 'pmatrix'
ENVIRONMENT_BMATRIX = 'bmatrix'
ENVIRONMENT_BBMATRIX = 'Bmatrix'
ENVIRONMENT_VMATRIX = 'vmatrix'
ENVIRONMENT_VVMATRIX = 'Vmatrix'

#: Environments in dialog display order: the five bracketed variants first,
#: then the plain forms without built-in delimiters.
ENVIRONMENTS = (
    ENVIRONMENT_PMATRIX,
    ENVIRONMENT_BMATRIX,
    ENVIRONMENT_BBMATRIX,
    ENVIRONMENT_VMATRIX,
    ENVIRONMENT_VVMATRIX,
    ENVIRONMENT_MATRIX,
    ENVIRONMENT_MATRIX_STAR,
)

PLACEHOLDER_CELL = '•'


def resize_cells(cells: Sequence[Sequence[str]], rows: int, columns: int) -> tuple[tuple[str, ...], ...]:
    '''Resize a cell grid while preserving its overlapping top-left area.'''

    return tuple(
        tuple(
            str(cells[row_index][column_index])
            if row_index < len(cells) and column_index < len(cells[row_index])
            else ''
            for column_index in range(columns)
        )
        for row_index in range(rows)
    )


@dataclass(frozen=True)
class MatrixSpec:
    '''The complete user-configurable description of a simple LaTeX matrix.'''

    rows: int = 2
    columns: int = 2
    environment: str = ENVIRONMENT_PMATRIX
    alignment: str = ALIGNMENT_CENTER
    cells: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        if not isinstance(self.rows, int) or not isinstance(self.columns, int):
            raise ValueError('Matrix dimensions must be integers')
        if not MIN_ROWS <= self.rows <= MAX_ROWS:
            raise ValueError(f'Matrix rows must be between {MIN_ROWS} and {MAX_ROWS}')
        if not MIN_COLUMNS <= self.columns <= MAX_COLUMNS:
            raise ValueError(f'Matrix columns must be between {MIN_COLUMNS} and {MAX_COLUMNS}')
        if self.environment not in ENVIRONMENTS:
            raise ValueError(f'Unknown matrix environment: {self.environment}')
        if self.alignment not in ALIGNMENTS:
            raise ValueError(f'Unsupported matrix alignment: {self.alignment}')

        object.__setattr__(self, 'cells', resize_cells(self.cells, self.rows, self.columns))

    @property
    def supports_alignment(self) -> bool:
        return self.environment == ENVIRONMENT_MATRIX_STAR

    @property
    def required_packages(self) -> tuple[str, ...]:
        # mathtools loads amsmath itself, so a single package covers both cases.
        if self.supports_alignment:
            return ('mathtools',)
        return ('amsmath',)

    def render(self) -> str:
        '''Render editable LaTeX source.

        Cell text is treated as raw LaTeX like in the table renderer. Empty
        cells become the editor's ``•`` placeholder so the inserted skeleton
        supports Tab navigation between entries. The optional column
        alignment is only emitted for ``matrix*`` because plain amsmath
        matrices do not accept an optional argument. The final row carries no
        ``\\`` because amsmath would treat it as an extra empty row.
        '''

        if self.supports_alignment:
            lines = ['\\begin{matrix*}[' + self.alignment + ']']
        else:
            lines = ['\\begin{' + self.environment + '}']
        row_lines = []
        for row_index in range(self.rows):
            cells = [
                cell if cell.strip() else PLACEHOLDER_CELL
                for cell in self.cells[row_index]
            ]
            line = ' & '.join(cells)
            if row_index < self.rows - 1:
                line += r' \\'
            row_lines.append(line)
        lines.extend(row_lines)
        lines.append('\\end{' + self.environment + '}')
        return '\n'.join(lines)

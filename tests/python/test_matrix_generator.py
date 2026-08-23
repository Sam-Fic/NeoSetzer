#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.dialogs.insert_matrix.matrix_generator import (
    ALIGNMENT_CENTER,
    ALIGNMENT_LEFT,
    ENVIRONMENT_BBMATRIX,
    ENVIRONMENT_BMATRIX,
    ENVIRONMENT_MATRIX,
    ENVIRONMENT_MATRIX_STAR,
    ENVIRONMENT_PMATRIX,
    ENVIRONMENT_VMATRIX,
    ENVIRONMENT_VVMATRIX,
    MAX_COLUMNS,
    MAX_ROWS,
    MatrixSpec,
    PLACEHOLDER_CELL,
    resize_cells,
)


class MatrixGeneratorTest(unittest.TestCase):

    def test_default_spec_renders_two_by_two_pmatrix_with_placeholders(self):
        spec = MatrixSpec()

        self.assertEqual(spec.environment, ENVIRONMENT_PMATRIX)
        self.assertEqual(spec.required_packages, ('amsmath',))
        self.assertEqual(spec.render(), (
            '\\begin{pmatrix}\n'
            '• & • \\\\\n'
            '• & •\n'
            '\\end{pmatrix}'
        ))

    def test_bracketed_environments_render_their_own_delimiters(self):
        expected_environments = (
            ENVIRONMENT_PMATRIX,
            ENVIRONMENT_BMATRIX,
            ENVIRONMENT_BBMATRIX,
            ENVIRONMENT_VMATRIX,
            ENVIRONMENT_VVMATRIX,
        )
        for environment in expected_environments:
            spec = MatrixSpec(rows=1, columns=1, environment=environment)
            rendered = spec.render()
            self.assertTrue(rendered.startswith('\\begin{' + environment + '}\n'))
            self.assertTrue(rendered.endswith('\n\\end{' + environment + '}'))
            self.assertEqual(spec.required_packages, ('amsmath',))

    def test_matrix_star_emits_alignment_and_requires_mathtools(self):
        for alignment in ('c', 'l', 'r'):
            spec = MatrixSpec(
                rows=2, columns=3,
                environment=ENVIRONMENT_MATRIX_STAR,
                alignment=alignment,
            )
            self.assertEqual(spec.required_packages, ('mathtools',))
            self.assertEqual(spec.render(), (
                '\\begin{matrix*}[' + alignment + ']\n'
                '• & • & • \\\\\n'
                '• & • & •\n'
                '\\end{matrix*}'
            ))

    def test_alignment_is_ignored_for_plain_matrix(self):
        spec = MatrixSpec(environment=ENVIRONMENT_MATRIX, alignment=ALIGNMENT_LEFT)

        self.assertEqual(spec.render(), (
            '\\begin{matrix}\n'
            '• & • \\\\\n'
            '• & •\n'
            '\\end{matrix}'
        ))

    def test_cell_content_is_raw_latex_and_empty_cells_become_placeholders(self):
        spec = MatrixSpec(
            cells=(
                ('a_{11}', ''),
                (r'\sqrt{2}', 'x^2'),
            ),
        )

        self.assertEqual(spec.render(), (
            '\\begin{pmatrix}\n'
            'a_{11} & ' + PLACEHOLDER_CELL + ' \\\\\n'
            '\\sqrt{2} & x^2\n'
            '\\end{pmatrix}'
        ))

    def test_cells_are_normalized_to_requested_dimensions(self):
        spec = MatrixSpec(rows=3, columns=2, cells=(('a', 'b', 'ignored'),))

        self.assertEqual(spec.cells, (('a', 'b'), ('', ''), ('', '')))

    def test_resize_cells_preserves_overlap_and_fills_blanks(self):
        resized = resize_cells((('a', 'b', 'c'), ('d', 'e', 'f')), 3, 2)

        self.assertEqual(resized, (('a', 'b'), ('d', 'e'), ('', '')))

    def test_dimensions_are_validated(self):
        with self.assertRaises(ValueError):
            MatrixSpec(rows=0)
        with self.assertRaises(ValueError):
            MatrixSpec(columns=MAX_COLUMNS + 1)
        with self.assertRaises(ValueError):
            MatrixSpec(rows=MAX_ROWS + 1)
        with self.assertRaises(ValueError):
            MatrixSpec(rows='2')
        with self.assertRaises(ValueError):
            MatrixSpec(environment='tabular')

    def test_alignments_are_validated(self):
        with self.assertRaises(ValueError):
            MatrixSpec(alignment='j')


if __name__ == '__main__':
    unittest.main()

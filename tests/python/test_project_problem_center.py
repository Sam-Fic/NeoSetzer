#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.project.problem_center import ProjectProblemCenter


class ProjectProblemCenterTest(unittest.TestCase):

    def setUp(self):
        self.center = ProjectProblemCenter.from_build_log(
            {
                'items': [
                    ('Warning', 'LaTeX', '/project/chapter.tex', 7, 'Reference is undefined'),
                    ('Error', 'BibTeX', '/project/references.bib', 12, 'Entry type is invalid'),
                    ('Badbox', 'LaTeX', '/project/main.tex', 22, 'Overfull hbox'),
                ],
            },
            missing_files=('/project/figures/diagram.pdf',),
            external_changes=('/project/chapter.tex',),
        )

    def test_normalizes_and_orders_problem_severity(self):
        problems = self.center.problems
        self.assertEqual([problem.severity for problem in problems],
                         ['error', 'error', 'warning', 'warning', 'info'])
        build_error = next(problem for problem in problems if problem.source == 'build'
                           and problem.severity == 'error')
        self.assertIn('jump-to-source', build_error.actions)
        self.assertIn('open-file', build_error.actions)

    def test_filters_by_severity_source_and_text(self):
        warnings = self.center.filter(severities=('warning',))
        self.assertEqual(len(warnings), 2)
        self.assertIn('external-change', {problem.source for problem in warnings})
        references = self.center.filter(query='reference')
        self.assertEqual(len(references), 3)
        dependencies = self.center.filter(sources=('dependency',))
        self.assertEqual(dependencies[0].filename, '/project/figures/diagram.pdf')

    def test_groups_counts_and_actions(self):
        grouped = self.center.grouped_by_file()
        self.assertEqual(len(grouped['/project/chapter.tex']), 2)
        self.assertEqual(self.center.counts(), {'error': 2, 'warning': 2, 'info': 1})
        missing = self.center.filter(sources=('dependency',))[0]
        self.assertEqual(missing.actions, ('open-root-document', 'show-dependency'))


if __name__ == '__main__':
    unittest.main()

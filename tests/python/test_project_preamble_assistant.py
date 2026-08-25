#!/usr/bin/env python3
# coding: utf-8

import unittest

from setzer.project.preamble_assistant import PreambleAssistant


class PreambleAssistantTest(unittest.TestCase):

    def test_detects_existing_packages_from_source_and_parser_data(self):
        existing = PreambleAssistant.existing_packages(
            r'\usepackage[unicode]{hyperref}\n\RequirePackage{graphicx,booktabs}',
            {'siunitx': []})
        self.assertEqual(existing, {'hyperref', 'graphicx', 'booktabs', 'siunitx'})

    def test_suggests_only_missing_packages_with_reasons(self):
        source = r'''\documentclass{article}
\usepackage{graphicx}
\begin{document}
\includegraphics{figure.pdf}
\href{https://example.org}{Example}
\SI{5}{\metre}
\begin{tikzpicture}\end{tikzpicture}
\end{document}
'''
        suggestions = PreambleAssistant.suggest(
            source, packages_dict={'hyperref': {}, 'siunitx': {}, 'tikz': {}})
        self.assertEqual([suggestion.package for suggestion in suggestions],
                         ['hyperref', 'siunitx', 'tikz'])
        self.assertTrue(all(suggestion.available_in_database
                            for suggestion in suggestions))
        self.assertEqual(suggestions[0].insertion, r'\usepackage{hyperref}')
        self.assertIn('Hyperlink', suggestions[0].reason)

    def test_does_not_suggest_or_modify_when_no_match(self):
        source = r'\documentclass{article}\begin{document}Plain text\end{document}'
        self.assertEqual(PreambleAssistant.suggest(source), ())
        self.assertEqual(source, r'\documentclass{article}\begin{document}Plain text\end{document}')


if __name__ == '__main__':
    unittest.main()

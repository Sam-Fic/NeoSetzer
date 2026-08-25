#!/usr/bin/env python3
# coding: utf-8

import os
import tempfile
import unittest

from setzer.project.files import ProjectFileResolver


class ProjectFileResolverTest(unittest.TestCase):

    def _write(self, root, relative, content=''):
        filename = os.path.join(root, relative)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(content)
        return filename

    def test_collects_recursive_safe_project_dependencies(self):
        with tempfile.TemporaryDirectory() as project:
            main = self._write(project, 'main.tex', r'''\documentclass{localclass}
\usepackage{localstyle,graphicx}
\bibliography{references}
\includegraphics{images/figure.pdf}
\LoadLetterOption{letterhead}
\input{chapters/intro}
''')
            self._write(project, 'chapters/intro.tex', r'\subfile{methods}')
            self._write(project, 'chapters/methods.tex', '')
            self._write(project, 'references.bib', '')
            self._write(project, 'images/figure.pdf', 'not-a-real-pdf')
            self._write(project, 'localclass.cls', '')
            self._write(project, 'localstyle.sty', '')
            self._write(project, 'letterhead.lco', '')
            project_files = ProjectFileResolver(main, project).collect()
            relatives = {os.path.relpath(path, project) for path in project_files.files}
            self.assertEqual(relatives, {
                'main.tex', 'chapters/intro.tex', 'chapters/methods.tex',
                'references.bib', 'images/figure.pdf', 'localclass.cls',
                'localstyle.sty', 'letterhead.lco',
            })
            self.assertEqual(project_files.missing_files, ())
            self.assertEqual({os.path.basename(path) for path in project_files.text_files},
                             {'main.tex', 'intro.tex', 'methods.tex', 'references.bib',
                              'localclass.cls', 'localstyle.sty', 'letterhead.lco'})

    def test_ignores_dependency_symlink_to_an_external_target(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, 'project')
            os.mkdir(project)
            external = self._write(parent, 'external.tex', 'outside')
            linked = os.path.join(project, 'linked.tex')
            os.symlink(external, linked)
            main = self._write(project, 'main.tex', r'\input{linked}')

            project_files = ProjectFileResolver(main, project).collect()

            self.assertEqual(project_files.files, (main,))
            self.assertNotIn(linked, project_files.files)
            self.assertNotIn(external, project_files.files)

    def test_reports_missing_and_refuses_paths_above_project_root(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, 'project')
            os.mkdir(project)
            external = self._write(parent, 'external.tex', '')
            main = self._write(project, 'main.tex', r'''\input{missing}
\input{../external}
''')
            project_files = ProjectFileResolver(main, project).collect()
            self.assertEqual(project_files.files, (main,))
            self.assertEqual(project_files.missing_files,
                             (os.path.join(project, 'missing.tex'),))
            self.assertNotIn(external, project_files.files)
            self.assertNotIn(external, project_files.missing_files)


if __name__ == '__main__':
    unittest.main()

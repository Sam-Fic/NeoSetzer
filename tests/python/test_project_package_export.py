#!/usr/bin/env python3
# coding: utf-8

import json
import os
import tempfile
import unittest
import zipfile

from setzer.project.package_export import ProjectPackageExporter


class ProjectPackageExporterTest(unittest.TestCase):

    def _write(self, root, relative, data=''):
        filename = os.path.join(root, relative)
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as file:
            file.write(data)
        return filename

    def test_exports_dependency_closure_configuration_and_manifest(self):
        with tempfile.TemporaryDirectory() as project:
            main = self._write(project, 'main.tex', r'''\documentclass{local}
\usepackage{localstyle}
\input{chapters/intro}
\bibliography{references}
\includegraphics{figures/result.pdf}
''')
            self._write(project, 'chapters/intro.tex', 'Introduction')
            self._write(project, 'references.bib', '@book{x, title={X}}')
            self._write(project, 'figures/result.pdf', 'pdf fixture')
            self._write(project, 'local.cls', '')
            self._write(project, 'localstyle.sty', '')
            self._write(project, '.neosetzer/build.json', '{"version":1}')
            plan = ProjectPackageExporter(main, project).create_plan()
            self.assertEqual(plan.missing_files, ())
            destination = os.path.join(project, 'dist', 'paper.zip')
            os.mkdir(os.path.dirname(destination))
            exported = ProjectPackageExporter(main, project).export(destination, plan)
            self.assertEqual(exported, destination)
            with zipfile.ZipFile(exported) as archive:
                names = set(archive.namelist())
                prefix = os.path.basename(project) + '/'
                self.assertTrue({
                    prefix + 'main.tex', prefix + 'chapters/intro.tex',
                    prefix + 'references.bib', prefix + 'figures/result.pdf',
                    prefix + 'local.cls', prefix + 'localstyle.sty',
                    prefix + '.neosetzer/build.json', prefix + 'MANIFEST.json',
                }.issubset(names))
                manifest = json.loads(archive.read(prefix + 'MANIFEST.json'))
            self.assertEqual(manifest['root_document'], 'main.tex')
            self.assertEqual(manifest['missing_files'], [])
            with self.assertRaises(FileExistsError):
                ProjectPackageExporter(main, project).export(destination, plan)

    def test_does_not_archive_dependency_symlink_to_an_external_target(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, 'project')
            os.mkdir(project)
            external = self._write(parent, 'external.tex', 'external content')
            linked = os.path.join(project, 'linked.tex')
            os.symlink(external, linked)
            main = self._write(project, 'main.tex', r'\input{linked}')

            exporter = ProjectPackageExporter(main, project)
            plan = exporter.create_plan()
            self.assertNotIn(linked, plan.files)
            destination = os.path.join(parent, 'project.zip')
            exporter.export(destination, plan)
            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
            self.assertNotIn(os.path.basename(project) + '/linked.tex', names)

    def test_reports_missing_local_dependencies_without_external_escape(self):
        with tempfile.TemporaryDirectory() as parent:
            project = os.path.join(parent, 'project')
            os.mkdir(project)
            main = self._write(project, 'main.tex', '\\input{missing}\n\\input{../outside}')
            outside = self._write(parent, 'outside.tex', 'outside')
            plan = ProjectPackageExporter(main, project).create_plan()
            self.assertEqual(plan.missing_files,
                             (os.path.join(project, 'missing.tex'),))
            self.assertNotIn(outside, plan.files)
            self.assertNotIn(outside, plan.missing_files)


if __name__ == '__main__':
    unittest.main()

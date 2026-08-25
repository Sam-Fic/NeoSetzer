#!/usr/bin/env python3
# coding: utf-8

import json
import os
import tempfile
import unittest

from setzer.project.build_configuration import ProjectBuildConfiguration


class ProjectBuildConfigurationTest(unittest.TestCase):

    def test_missing_configuration_uses_empty_project_overrides(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            self.assertIsNone(ProjectBuildConfiguration.discover(
                os.path.join(project, 'main.tex')))
            self.assertEqual(configuration.load()['interpreter'], None)
            self.assertEqual(configuration.load()['additional_arguments'], ())

    def test_save_load_and_resolve_project_overrides(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            values = configuration.save({
                'root_document': 'main.tex',
                'interpreter': 'lualatex',
                'use_latexmk': True,
                'cleanup_build_files': False,
                'shell_mode': 'restricted',
                'output_directory': 'build',
                'additional_arguments': ['-interaction=nonstopmode'],
                'bibliography_backend': 'biber',
            })
            self.assertEqual(values['output_directory'], 'build')
            self.assertEqual(configuration.load(), values)
            self.assertEqual(configuration.effective_path('build'),
                             os.path.join(project, 'build'))
            with open(configuration.pathname, encoding='utf-8') as file:
                self.assertEqual(json.load(file)['version'], 1)

    def test_rejects_paths_outside_project_and_unsafe_arguments(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            values = configuration.save({
                'root_document': '../outside.tex',
                'output_directory': '/tmp/build',
                'interpreter': 'unknown-engine',
                'shell_mode': 'dangerous',
                'additional_arguments': [
                    '-safe', 'bad\nargument', '\x00bad',
                    '-output-directory=../outside', '-shell-escape',
                    '-jobname=outside',
                ],
            })
            self.assertIsNone(values['root_document'])
            self.assertIsNone(values['output_directory'])
            self.assertIsNone(values['interpreter'])
            self.assertIsNone(values['shell_mode'])
            self.assertEqual(values['additional_arguments'], ('-safe',))

    def test_invalid_or_corrupt_configuration_does_not_escape_project(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            os.makedirs(os.path.dirname(configuration.pathname))
            with open(configuration.pathname, 'w', encoding='utf-8') as file:
                file.write('{ definitely not json')
            self.assertIsNone(configuration.load()['root_document'])
            with open(configuration.pathname, 'w', encoding='utf-8') as file:
                json.dump({'version': 1, 'output_directory': '../../tmp'}, file)
            self.assertIsNone(configuration.load()['output_directory'])

    def test_discover_finds_nearest_parent_project_configuration(self):
        with tempfile.TemporaryDirectory() as project:
            child = os.path.join(project, 'chapters', 'part')
            os.makedirs(child)
            top = ProjectBuildConfiguration(project)
            top.save({'root_document': 'main.tex'})
            nearest_root = os.path.join(project, 'chapters')
            nearest = ProjectBuildConfiguration(nearest_root)
            nearest.save({'root_document': 'chapters.tex'})
            discovered = ProjectBuildConfiguration.discover(
                os.path.join(child, 'section.tex'))
            self.assertEqual(discovered.project_root, nearest_root)


if __name__ == '__main__':
    unittest.main()

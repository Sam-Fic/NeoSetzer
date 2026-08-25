#!/usr/bin/env python3
# coding: utf-8

import json
import os
import tempfile
import unittest

from setzer.project.build_configuration import (
    ProjectBuildConfiguration,
    project_relative_path,
    DEFAULT_PROFILE_NAME,
)


class ProjectBuildConfigurationTest(unittest.TestCase):

    def _config_path(self, project_root):
        return os.path.join(project_root, '.neosetzer', 'build.json')

    def test_missing_configuration_uses_empty_project_overrides(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            self.assertFalse(configuration.exists)
            self.assertIsNone(ProjectBuildConfiguration.discover(
                os.path.join(project, 'main.tex')))
            values = configuration.load()
            self.assertEqual(values['active_profile'], DEFAULT_PROFILE_NAME)
            self.assertEqual(values['tasks'], ['latex'])
            # 缺失配置：所有 profile 键为 None（含 additional_arguments）。
            self.assertIsNone(values['additional_arguments'])

    def test_save_load_and_resolve_project_overrides(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            # save() 不返回值；写入后用 load() 验证读回结果。
            configuration.save({
                'root_document': 'main.tex',
                'interpreter': 'lualatex',
                'use_latexmk': True,
                'cleanup_build_files': False,
                'shell_mode': 'restricted',
                'output_directory': 'build',
                'additional_arguments': ['-interaction=nonstopmode'],
                'bibliography_backend': 'biber',
            })
            values = configuration.load()
            self.assertEqual(values['root_document'], 'main.tex')
            self.assertEqual(values['output_directory'], 'build')
            self.assertEqual(values['interpreter'], 'lualatex')
            self.assertTrue(values['use_latexmk'])
            self.assertFalse(values['cleanup_build_files'])
            self.assertEqual(values['shell_mode'], 'restricted')
            self.assertEqual(values['bibliography_backend'], 'biber')
            self.assertEqual(values['additional_arguments'],
                             ('-interaction=nonstopmode',))
            self.assertEqual(values['name'], DEFAULT_PROFILE_NAME)
            self.assertEqual(values['tasks'], ['latex'])
            self.assertEqual(values['active_profile'], DEFAULT_PROFILE_NAME)
            # 落盘格式：.neosetzer/build.json 中 profiles + active_profile。
            path = self._config_path(project)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding='utf-8') as file:
                payload = json.load(file)
            self.assertEqual(payload['active_profile'], DEFAULT_PROFILE_NAME)
            self.assertEqual(payload['profiles'][0]['name'],
                             DEFAULT_PROFILE_NAME)
            self.assertEqual(configuration.effective_path('build'),
                             os.path.join(project, 'build'))

    def test_save_profiles_roundtrip(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save_profiles([
                {
                    'name': 'Quick',
                    'root_document': 'main.tex',
                    'tasks': ['latex'],
                    'additional_arguments': ['-shell-escape'],
                    'output_directory': None, 'interpreter': None,
                    'use_latexmk': None, 'cleanup_build_files': None,
                    'shell_mode': None, 'bibliography_backend': None,
                },
                {
                    'name': 'Full',
                    'root_document': 'main.tex',
                    'tasks': ['latex', 'biber', 'latex', 'latex'],
                    'additional_arguments': (),
                    'output_directory': None, 'interpreter': None,
                    'use_latexmk': None, 'cleanup_build_files': None,
                    'shell_mode': None, 'bibliography_backend': None,
                },
            ], 'Full')
            profiles, active = configuration.load_profiles()
            self.assertEqual(active, 'Full')
            self.assertEqual([p['name'] for p in profiles], ['Quick', 'Full'])
            self.assertEqual(profiles[1]['tasks'],
                             ['latex', 'biber', 'latex', 'latex'])
            # active profile 的 load() 反映选中 profile。
            self.assertEqual(configuration.load()['active_profile'], 'Full')
            self.assertEqual(configuration.load()['tasks'],
                             ['latex', 'biber', 'latex', 'latex'])

    def test_load_falls_back_to_first_profile_when_active_missing(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            # 手工写一个 active_profile 指向不存在的 profile。
            os.makedirs(os.path.join(project, '.neosetzer'), exist_ok=True)
            path = self._config_path(project)
            with open(path, 'w', encoding='utf-8') as file:
                json.dump({
                    'active_profile': 'Ghost',
                    'profiles': [{
                        'name': 'Real',
                        'root_document': 'main.tex',
                        'output_directory': None, 'interpreter': None,
                        'use_latexmk': None, 'cleanup_build_files': None,
                        'shell_mode': None, 'bibliography_backend': None,
                        'additional_arguments': None,
                        'tasks': ['latex'],
                    }],
                }, file)
            configuration = ProjectBuildConfiguration(project)
            self.assertEqual(configuration.load()['active_profile'], 'Real')
            self.assertEqual(configuration.load()['root_document'], 'main.tex')

    def test_legacy_flat_configuration_migrates_to_default_profile(self):
        with tempfile.TemporaryDirectory() as project:
            # 旧版为单键扁平结构；新版把它包进 Default profile。
            os.makedirs(os.path.join(project, '.neosetzer'), exist_ok=True)
            with open(self._config_path(project), 'w', encoding='utf-8') as file:
                json.dump({
                    'root_document': 'main.tex',
                    'interpreter': 'pdflatex',
                    'additional_arguments': ['-interaction=nonstopmode'],
                }, file)
            configuration = ProjectBuildConfiguration(project)
            self.assertTrue(configuration.exists)
            profiles, active = configuration.load_profiles()
            self.assertEqual(active, DEFAULT_PROFILE_NAME)
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]['name'], DEFAULT_PROFILE_NAME)
            self.assertEqual(configuration.load()['root_document'], 'main.tex')
            self.assertEqual(configuration.load()['interpreter'], 'pdflatex')
            self.assertEqual(configuration.load()['additional_arguments'],
                             ('-interaction=nonstopmode',))

    def test_invalid_or_corrupt_configuration_is_safe(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            os.makedirs(os.path.dirname(self._config_path(project)))
            with open(self._config_path(project), 'w',
                      encoding='utf-8') as file:
                file.write('{ definitely not json')
            configuration = ProjectBuildConfiguration(project)
            # 损坏 JSON：文件存在但解析失败，load() 返回空默认而不崩溃。
            self.assertTrue(configuration.exists)
            self.assertIsNone(configuration.load()['root_document'])
            self.assertIsNone(configuration.load()['output_directory'])
            self.assertIsNone(configuration.load()['additional_arguments'])

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
            self.assertIsNotNone(discovered)
            self.assertEqual(discovered.folder, nearest_root)
            self.assertEqual(discovered.load()['root_document'],
                             'chapters.tex')

    def test_save_blocks_traversal_absolute_paths_and_unknown_values(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save({
                'root_document': '../evil.tex',
                'output_directory': 'a/../../../outside',
                'interpreter': 'make',
                'shell_mode': 'weird',
                'bibliography_backend': 'latexmk',
                'use_latexmk': 'yes',
                'cleanup_build_files': 'no',
                'additional_arguments': (),
            })
            values = configuration.load()
            self.assertIsNone(values['root_document'])
            self.assertIsNone(values['output_directory'])
            self.assertIsNone(values['interpreter'])
            self.assertIsNone(values['shell_mode'])
            self.assertIsNone(values['bibliography_backend'])
            self.assertIsNone(values['use_latexmk'])
            self.assertIsNone(values['cleanup_build_files'])

    def test_write_rejects_absolute_root_document(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save({'root_document': '/etc/passwd.tex'})
            self.assertIsNone(configuration.load()['root_document'])

    def test_save_filters_dangerous_compiler_arguments(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save({
                'additional_arguments': [
                    '-interaction=nonstopmode',
                    '-shell-escape',
                    '--output-directory=/tmp/build',
                    '-jobname=pwned',
                    '--shell-restricted',
                    '-no-shell-escape',
                    'ok-argument',
                    'with\nnewline',
                    'with\x00nul',
                ],
            })
            values = configuration.load()
            self.assertEqual(values['additional_arguments'],
                             ('-interaction=nonstopmode', 'ok-argument'))

    def test_save_caps_argument_count_and_length(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save({
                'additional_arguments': [f'arg{i:02d}' for i in range(30)],
            })
            arguments = configuration.load()['additional_arguments']
            self.assertEqual(len(arguments), 24)
            self.assertEqual(arguments[0], 'arg00')
            self.assertEqual(arguments[-1], 'arg23')
            configuration.save({
                'additional_arguments': ['x' * 300, 'short'],
            })
            self.assertEqual(configuration.load()['additional_arguments'],
                             ('short',))

    def test_save_accepts_string_arguments_and_filters_them(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            configuration.save({
                'additional_arguments':
                    '-shell-escape -interaction=nonstopmode',
            })
            self.assertEqual(configuration.load()['additional_arguments'],
                             ('-interaction=nonstopmode',))

    def test_load_sanitizes_hostile_disk_file(self):
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, '.neosetzer'), exist_ok=True)
            with open(self._config_path(project), 'w',
                      encoding='utf-8') as file:
                json.dump({
                    'active_profile': 'Default',
                    'profiles': [{
                        'name': 'Default',
                        'root_document': '../escape.tex',
                        'output_directory': '/tmp/x',
                        'interpreter': 'make',
                        'use_latexmk': 1,
                        'cleanup_build_files': 0,
                        'shell_mode': 'banana',
                        'bibliography_backend': 'latexmk',
                        'additional_arguments': [
                            '-shell-escape', '--output-directory=/tmp'],
                        'tasks': ['latex'],
                    }],
                }, file)
            configuration = ProjectBuildConfiguration(project)
            values = configuration.load()
            self.assertIsNone(values['root_document'])
            self.assertIsNone(values['output_directory'])
            self.assertIsNone(values['interpreter'])
            self.assertIsNone(values['use_latexmk'])
            self.assertIsNone(values['cleanup_build_files'])
            self.assertIsNone(values['shell_mode'])
            self.assertIsNone(values['bibliography_backend'])
            self.assertEqual(values['additional_arguments'], ())
            self.assertEqual(values['tasks'], ['latex'])

    def test_project_relative_path_normalizes_absolute_paths(self):
        with tempfile.TemporaryDirectory() as project:
            self.assertEqual(
                project_relative_path(
                    project, os.path.join(project, 'main.tex')),
                'main.tex')
            self.assertEqual(
                project_relative_path(
                    project, os.path.join(project, 'build')),
                'build')
            # 相对路径原样透传（保存层再校验）。
            self.assertEqual(
                project_relative_path(project, 'main.tex'), 'main.tex')
            # 项目外绝对路径被拒绝。
            self.assertIsNone(project_relative_path(project, '/etc/passwd'))
            outside_parent = os.path.join(project, '..', 'outside.tex')
            self.assertIsNone(
                project_relative_path(project, os.path.abspath(outside_parent)))
            # 空值原样返回，调用方先 or None。
            self.assertIsNone(project_relative_path(project, None))
            self.assertEqual(project_relative_path(project, ''), '')

    def test_dialog_style_absolute_paths_survive_save(self):
        with tempfile.TemporaryDirectory() as project:
            configuration = ProjectBuildConfiguration(project)
            # 模拟对话框提交链路：绝对路径 -> project_relative_path -> save。
            configuration.save({
                'root_document': project_relative_path(
                    project, os.path.join(project, 'main.tex')),
                'output_directory': project_relative_path(
                    project, os.path.join(project, 'build')),
                'shell_mode': 'enable',
            })
            values = configuration.load()
            self.assertEqual(values['root_document'], 'main.tex')
            self.assertEqual(values['output_directory'], 'build')
            self.assertEqual(values['shell_mode'], 'enable')


if __name__ == '__main__':
    unittest.main()
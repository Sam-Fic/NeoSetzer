#!/usr/bin/env python3
# coding: utf-8

'''Persistent, UI-independent project build configuration.

A configuration is optional.  Invalid or unreadable values are ignored rather
than preventing a normal document build with the user's global preferences.
'''

import os

from setzer.helpers.persistence import load_json, save_json


CONFIG_DIRECTORY = '.neosetzer'
CONFIG_FILENAME = 'build.json'
SCHEMA_VERSION = 1

_INTERPRETERS = frozenset(('pdflatex', 'xelatex', 'lualatex', 'tectonic'))
_SHELL_MODES = frozenset(('disable', 'restricted', 'enable'))
_BIBLIOGRAPHY_BACKENDS = frozenset(('auto', 'bibtex', 'biber'))
_MAX_ARGUMENTS = 24
_MAX_ARGUMENT_LENGTH = 256
_ARGUMENT_PREFIXES_BLOCKED = (
    '-output-directory', '--output-directory', '-jobname', '--jobname',
    '-shell-escape', '--shell-escape', '-shell-restricted',
    '--shell-restricted', '-no-shell-escape', '--no-shell-escape',
)

_DEFAULTS = {
    'root_document': None,
    'interpreter': None,
    'use_latexmk': None,
    'cleanup_build_files': None,
    'shell_mode': None,
    'output_directory': None,
    'additional_arguments': (),
    'bibliography_backend': None,
}


class ProjectBuildConfiguration:
    '''Read, validate and save one project's optional build settings.'''

    def __init__(self, project_root):
        if not isinstance(project_root, str) or not project_root:
            raise ValueError('A project root is required')
        self.project_root = os.path.abspath(project_root)

    @property
    def pathname(self):
        return os.path.join(self.project_root, CONFIG_DIRECTORY, CONFIG_FILENAME)

    @classmethod
    def discover(cls, source_filename):
        '''Find the closest parent directory containing `.neosetzer/build.json`.

        A missing configuration returns ``None``.  The caller may then fall
        back to document and global preferences without treating an ordinary
        directory as a project.
        '''
        if not isinstance(source_filename, str) or not source_filename:
            return None
        directory = os.path.abspath(os.path.dirname(source_filename))
        while True:
            configuration = cls(directory)
            if os.path.isfile(configuration.pathname):
                return configuration
            parent = os.path.dirname(directory)
            if parent == directory:
                return None
            directory = parent

    def load(self):
        raw = load_json(self.pathname, fallback=None)
        if not isinstance(raw, dict) or raw.get('version') != SCHEMA_VERSION:
            return dict(_DEFAULTS)
        return self._validated_values(raw)

    def save(self, values):
        validated = self._validated_values(values)
        config_directory = os.path.dirname(self.pathname)
        os.makedirs(config_directory, exist_ok=True)
        payload = {'version': SCHEMA_VERSION}
        payload.update(validated)
        payload['additional_arguments'] = list(validated['additional_arguments'])
        save_json(self.pathname, payload)
        return validated

    def effective_path(self, value):
        '''Return an absolute project-contained path or ``None``.

        Configuration paths are deliberately relative to the project root.
        This both makes the file portable and blocks directory traversal.
        '''
        if not isinstance(value, str) or not value or os.path.isabs(value):
            return None
        candidate = os.path.abspath(os.path.join(self.project_root, value))
        try:
            if os.path.commonpath((self.project_root, candidate)) != self.project_root:
                return None
        except ValueError:
            return None
        return candidate

    def _validated_values(self, values):
        values = values if isinstance(values, dict) else {}
        validated = dict(_DEFAULTS)

        root_document = values.get('root_document')
        if self.effective_path(root_document) and root_document.lower().endswith('.tex'):
            validated['root_document'] = self._portable_path(root_document)

        output_directory = values.get('output_directory')
        if self.effective_path(output_directory):
            validated['output_directory'] = self._portable_path(output_directory)

        interpreter = values.get('interpreter')
        if interpreter in _INTERPRETERS:
            validated['interpreter'] = interpreter

        for key in ('use_latexmk', 'cleanup_build_files'):
            if isinstance(values.get(key), bool):
                validated[key] = values[key]

        shell_mode = values.get('shell_mode')
        if shell_mode in _SHELL_MODES:
            validated['shell_mode'] = shell_mode

        bibliography_backend = values.get('bibliography_backend')
        if bibliography_backend in _BIBLIOGRAPHY_BACKENDS:
            validated['bibliography_backend'] = bibliography_backend

        arguments = values.get('additional_arguments', ())
        if isinstance(arguments, (list, tuple)):
            safe_arguments = []
            for argument in arguments[:_MAX_ARGUMENTS]:
                if (isinstance(argument, str) and argument.strip()
                        and len(argument) <= _MAX_ARGUMENT_LENGTH
                        and '\x00' not in argument and '\n' not in argument
                        and '\r' not in argument
                        and not argument.lstrip().startswith(
                            _ARGUMENT_PREFIXES_BLOCKED)):
                    safe_arguments.append(argument)
            validated['additional_arguments'] = tuple(safe_arguments)

        return validated

    def _portable_path(self, value):
        path = self.effective_path(value)
        return os.path.relpath(path, self.project_root)

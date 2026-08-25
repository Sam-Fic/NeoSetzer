#!/usr/bin/env python3
# coding: utf-8

'''Discover, load and persist an optional ``<project>/.neosetzer/build.json``
file that overrides build settings for a LaTeX project.

The configuration is organised as **named build profiles**: each profile is a
set of build settings plus an ordered list of *tasks* (build steps). The
``active_profile`` selects which profile is used when building a document that
belongs to the project. This supports multiple deliverables (arXiv, publisher,
slides, ...) that share the same source tree but need different build chains.

To strictly avoid executing arbitrary scripts, a profile's ``tasks`` may only
reference a fixed whitelist of trusted build backends. No free-form command or
shell string is ever run from a profile.
'''

import json
import os
import shlex

from setzer.helpers.persistence import save_json, load_json


# Fallback profile name used when a project has no explicit profiles (i.e. the
# legacy single-configuration file). Kept stable so backward-compatible files
# keep working.
DEFAULT_PROFILE_NAME = 'Default'

# Ordered list of build-step types a profile may reference. Each maps to an
# existing, trusted builder backend in setzer.document.build_system.builder — no
# arbitrary command is executed. Extending this list requires adding a matching
# backend, never a raw shell invocation.
TASK_TYPE_LATEX = 'latex'
TASK_TYPE_BIBTEX = 'bibtex'
TASK_TYPE_BIBER = 'biber'
TASK_TYPE_MAKEINDEX = 'makeindex'
TASK_TYPE_GLOSSARIES = 'glossaries'

ALLOWED_TASK_TYPES = frozenset({
    TASK_TYPE_LATEX,
    TASK_TYPE_BIBTEX,
    TASK_TYPE_BIBER,
    TASK_TYPE_MAKEINDEX,
    TASK_TYPE_GLOSSARIES,
})

def task_type_label(task_type):
    '''Localised label for a task type (lazy: _() needs gettext.install).'''
    return {
        TASK_TYPE_LATEX: _('LaTeX'),
        TASK_TYPE_BIBTEX: _('BibTeX'),
        TASK_TYPE_BIBER: _('Biber'),
        TASK_TYPE_MAKEINDEX: _('MakeIndex'),
        TASK_TYPE_GLOSSARIES: _('Glossaries'),
    }.get(task_type, task_type)

# Default task sequence: a single LaTeX pass (bibtex/biber etc. are handled
# automatically inside the LaTeX builder when needed). Mirrors the pre-profile
# single-build behaviour.
DEFAULT_TASKS = [TASK_TYPE_LATEX]

# Keys shared by every profile (the legacy single-configuration keys, reused
# as per-profile settings so old files migrate cleanly).
_PROFILE_KEYS = (
    'root_document',
    'output_directory',
    'interpreter',
    'use_latexmk',
    'cleanup_build_files',
    'shell_mode',
    'bibliography_backend',
    'additional_arguments',
)


class ProjectBuildConfiguration:
    '''Reads and writes ``build.json`` for a project root directory.'''

    CONFIG_DIRECTORY = '.neosetzer'
    CONFIG_FILE = 'build.json'

    def __init__(self, project_root):
        self.folder = project_root
        config_path = os.path.join(project_root, self.CONFIG_DIRECTORY,
                                   self.CONFIG_FILE)
        self.exists = os.path.isfile(config_path)
        self.content = load_json(config_path) if self.exists else None
        self.values = self._validate(self.content) if self.exists else None

    @classmethod
    def discover(cls, source_filename):
        '''Walk up from a file to the first ancestor that holds build.json.'''
        if not source_filename:
            return None
        directory = os.path.dirname(os.path.abspath(source_filename))
        root = os.path.abspath(os.sep)
        while True:
            candidate = os.path.join(directory, cls.CONFIG_DIRECTORY,
                                     cls.CONFIG_FILE)
            if os.path.isfile(candidate):
                return cls(os.path.dirname(candidate))
            if directory == root:
                break
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
        return None

    def _validate(self, content):
        '''Coerce raw JSON into a safe dict. Malformed input yields all-None.'''
        if not isinstance(content, dict):
            return {key: None for key in _PROFILE_KEYS} | {
                'profiles': None, 'active_profile': None}
        result = {}
        for key in _PROFILE_KEYS:
            value = content.get(key)
            if key == 'additional_arguments':
                value = self._validate_arguments(value)
            result[key] = value
        profiles, active = self._validate_profiles(content)
        result['profiles'] = profiles
        result['active_profile'] = active
        return result

    @staticmethod
    def _validate_arguments(value):
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(argument) for argument in value)
        if isinstance(value, str):
            text = value.strip()
            return tuple(shlex.split(text)) if text else ()
        return ()

    def _validate_profiles(self, content):
        '''Return (profiles_list, active_name) from raw content dict.

        A legacy file (no ``profiles`` key) is wrapped into a single Default
        profile so existing projects keep working unchanged.
        '''
        raw_profiles = content.get('profiles')
        active = content.get('active_profile')
        if not isinstance(raw_profiles, list) or not raw_profiles:
            legacy = {key: content.get(key) for key in _PROFILE_KEYS}
            legacy['additional_arguments'] = self._validate_arguments(
                content.get('additional_arguments'))
            legacy['name'] = DEFAULT_PROFILE_NAME
            legacy['tasks'] = list(DEFAULT_TASKS)
            return [legacy], DEFAULT_PROFILE_NAME

        cleaned = []
        seen = set()
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                continue
            profile = {key: raw.get(key) for key in _PROFILE_KEYS}
            profile['additional_arguments'] = self._validate_arguments(
                raw.get('additional_arguments'))
            name = (raw.get('name') or DEFAULT_PROFILE_NAME)
            if name in seen:
                # De-duplicate silently; names must stay unique for switching.
                index = 1
                while f'{name} ({index})' in seen:
                    index += 1
                name = f'{name} ({index})'
            seen.add(name)
            profile['name'] = name
            profile['tasks'] = self._validate_tasks(raw.get('tasks'))
            cleaned.append(profile)

        if not cleaned:
            cleaned = [{
                'name': DEFAULT_PROFILE_NAME,
                'tasks': DEFAULT_TASKS,
                **{key: None for key in _PROFILE_KEYS},
            }]

        if active not in seen:
            active = cleaned[0]['name']
        return cleaned, active

    @staticmethod
    def _validate_tasks(value):
        if not isinstance(value, list) or not value:
            return list(DEFAULT_TASKS)
        tasks = [task for task in value
                 if isinstance(task, str) and task in ALLOWED_TASK_TYPES]
        return tasks or list(DEFAULT_TASKS)

    def load(self):
        '''Return the active profile's effective settings.

        The result always carries an ``active_profile`` key (the profile name)
        and a ``tasks`` key (the ordered build-step list) so callers can show
        the active configuration and drive the build pipeline.
        '''
        if self.values is None:
            return {
                'active_profile': DEFAULT_PROFILE_NAME,
                'tasks': list(DEFAULT_TASKS),
                **{key: None for key in _PROFILE_KEYS},
            }
        profiles = self.values['profiles']
        active = self.values['active_profile']
        for profile in profiles:
            if profile['name'] == active:
                data = dict(profile)
                data['active_profile'] = active
                return data
        # Active profile disappeared; fall back to the first one.
        data = dict(profiles[0])
        data['active_profile'] = profiles[0]['name']
        return data

    def load_profiles(self):
        '''Return the full list of profiles and the active profile name.'''
        if self.values is None:
            return [{
                'name': DEFAULT_PROFILE_NAME,
                'tasks': list(DEFAULT_TASKS),
                **{key: None for key in _PROFILE_KEYS},
            }], DEFAULT_PROFILE_NAME
        return list(self.values['profiles']), self.values['active_profile']

    def save(self, data):
        '''Persist a single legacy-style configuration (backward compatible).

        ``data`` uses the original flat keys. It is stored as the Default
        profile so mixed usage keeps working.
        '''
        self._write_profile({**data, 'name': DEFAULT_PROFILE_NAME},
                            DEFAULT_PROFILE_NAME)

    def save_profiles(self, profiles, active_name):
        '''Persist a list of profiles and the active profile name.'''
        cleaned = []
        seen = set()
        for profile in profiles:
            name = profile.get('name') or DEFAULT_PROFILE_NAME
            if name in seen:
                index = 1
                while f'{name} ({index})' in seen:
                    index += 1
                name = f'{name} ({index})'
            seen.add(name)
            entry = {'name': name}
            for key in _PROFILE_KEYS:
                entry[key] = profile.get(key)
            entry['tasks'] = self._validate_tasks(profile.get('tasks'))
            cleaned.append(entry)
        if active_name not in seen:
            active_name = cleaned[0]['name'] if cleaned else DEFAULT_PROFILE_NAME
        content = {'profiles': cleaned, 'active_profile': active_name}
        self._write_content(content)

    def _write_profile(self, profile, active_name):
        self._write_content({
            'profiles': [profile],
            'active_profile': active_name,
        })

    def _write_content(self, content):
        path = os.path.join(self.folder, self.CONFIG_DIRECTORY,
                            self.CONFIG_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_json(path, content)
        self.exists = True
        self.content = content
        self.values = self._validate(content)

    def effective_path(self, path):
        '''Resolve a project-relative path against the project root.'''
        if not path:
            return None
        return os.path.normpath(os.path.join(self.folder, path))

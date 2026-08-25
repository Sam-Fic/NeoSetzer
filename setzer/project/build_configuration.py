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

# White-lists for free-form string values. Anything outside these sets is
# dropped to None when a profile is saved or loaded, so a hostile project
# file cannot steer the build towards arbitrary engines or shells.
_INTERPRETERS = frozenset(('pdflatex', 'xelatex', 'lualatex', 'tectonic'))
_SHELL_MODES = frozenset(('disable', 'restricted', 'enable'))
_BIBLIOGRAPHY_BACKENDS = frozenset(('auto', 'bibtex', 'biber'))

# additional_arguments hardening: cap count/length and block engine flags
# that escape the project or enable shell execution. A compiler argument
# starting with one of these prefixes is rejected entirely.
_MAX_ARGUMENTS = 24
_MAX_ARGUMENT_LENGTH = 256
_ARGUMENT_PREFIXES_BLOCKED = (
    '-output-directory', '--output-directory', '-jobname', '--jobname',
    '-shell-escape', '--shell-escape', '-shell-restricted',
    '--shell-restricted', '-no-shell-escape', '--no-shell-escape',
)


def project_relative_path(project_root, path):
    '''Normalize a user-supplied path to project-relative form.

    Absolute paths that live inside the project root are converted to
    relative form (file-chooser dialogs return absolute paths); absolute
    paths that escape the project are dropped (None) so the save-side
    sanitizer keeps rejecting them. Relative paths pass through unchanged
    and are validated later by ``effective_path``.

    Designed for the dialog layer, which must not silently lose a valid
    selection just because the user picked it with a chooser.
    '''
    if path is None or not isinstance(path, str) or not path.strip():
        return path
    folder = os.path.abspath(project_root)
    if not os.path.isabs(path):
        return path
    candidate = os.path.abspath(path)
    try:
        if os.path.commonpath((folder, candidate)) == folder:
            return os.path.relpath(candidate, folder)
    except ValueError:
        pass
    return None


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
                # candidate 是 <dir>/.neosetzer/build.json，项目根是 directory
                # 本身（不是 dirname(candidate)，那会多剥一层到 .neosetzer）。
                return cls(directory)
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
            raw = value
        elif isinstance(value, str):
            text = value.strip()
            raw = shlex.split(text) if text else ()
        else:
            return ()
        safe = []
        for argument in raw[:_MAX_ARGUMENTS]:
            if (isinstance(argument, str) and argument.strip()
                    and len(argument) <= _MAX_ARGUMENT_LENGTH
                    and '\x00' not in argument and '\n' not in argument
                    and '\r' not in argument
                    and not argument.lstrip().startswith(
                        _ARGUMENT_PREFIXES_BLOCKED)):
                safe.append(argument)
        return tuple(safe)

    def _validate_profiles(self, content):
        '''Return (profiles_list, active_name) from raw content dict.

        A legacy file (no ``profiles`` key) is wrapped into a single Default
        profile so existing projects keep working unchanged.
        '''
        raw_profiles = content.get('profiles')
        active = content.get('active_profile')
        if not isinstance(raw_profiles, list) or not raw_profiles:
            legacy = self._sanitize_values(content)
            legacy['name'] = DEFAULT_PROFILE_NAME
            legacy['tasks'] = list(DEFAULT_TASKS)
            return [legacy], DEFAULT_PROFILE_NAME

        cleaned = []
        seen = set()
        for raw in raw_profiles:
            if not isinstance(raw, dict):
                continue
            profile = dict(self._sanitize_values(raw))
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
        profiles = self.values['profiles'] or []
        active = self.values['active_profile'] or DEFAULT_PROFILE_NAME
        for profile in profiles:
            if profile['name'] == active:
                data = dict(profile)
                data['active_profile'] = active
                return data
        # Active profile disappeared / no profiles; fall back to the first one.
        if not profiles:
            return {
                'active_profile': DEFAULT_PROFILE_NAME,
                'tasks': list(DEFAULT_TASKS),
                **{key: None for key in _PROFILE_KEYS},
            }
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

    def _sanitize_values(self, values):
        '''White-list / black-list a profile's free-form fields.

        Runs on save (writers) to keep hostile input out of build.json.
        Values outside the white-lists collapse to None; blocked compiler
        arguments are dropped; paths are kept project-relative and
        verified not to escape the project root.

        ``name`` / ``tasks`` are intentionally left untouched (handled by
        the callers via _validate_tasks / name de-dup).
        '''
        if not isinstance(values, dict):
            values = {}
        sanitized = {}
        for key in _PROFILE_KEYS:
            value = values.get(key)
            if key == 'root_document':
                if (isinstance(value, str) and value
                        and value.lower().endswith('.tex')
                        and self.effective_path(value)):
                    sanitized[key] = self._portable_path(value)
                else:
                    sanitized[key] = None
            elif key == 'output_directory':
                if (isinstance(value, str) and value
                        and self.effective_path(value)):
                    sanitized[key] = self._portable_path(value)
                else:
                    sanitized[key] = None
            elif key == 'interpreter':
                sanitized[key] = value if value in _INTERPRETERS else None
            elif key in ('use_latexmk', 'cleanup_build_files'):
                sanitized[key] = value if isinstance(value, bool) else None
            elif key == 'shell_mode':
                sanitized[key] = value if value in _SHELL_MODES else None
            elif key == 'bibliography_backend':
                sanitized[key] = (value if value in _BIBLIOGRAPHY_BACKENDS
                                  else None)
            elif key == 'additional_arguments':
                sanitized[key] = self._validate_arguments(value)
            else:
                sanitized[key] = value
        return sanitized

    def _portable_path(self, path):
        '''Return the project-relative form of a project-contained path.

        Re-resolves through effective_path first (mirrors the legacy
        helper), so callers may pass either a raw relative path or the
        absolute path returned by effective_path.
        '''
        return os.path.relpath(self.effective_path(path),
                               os.path.abspath(self.folder))

    def save(self, data):
        '''Persist a single legacy-style configuration (backward compatible).

        ``data`` uses the original flat keys. It is stored as the Default
        profile so mixed usage keeps working. Values are sanitized before
        they hit disk.
        '''
        profile = dict(self._sanitize_values(data))
        profile['name'] = DEFAULT_PROFILE_NAME
        profile['tasks'] = list(DEFAULT_TASKS)
        self._write_profile(profile, DEFAULT_PROFILE_NAME)

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
            entry = dict(self._sanitize_values(profile))
            entry['name'] = name
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
        '''Resolve a project-relative path inside the project root.

        Returns ``None`` when the path is unusable or would escape the
        project folder. Configuration paths are deliberately relative to the
        project root: this keeps the file portable and blocks directory
        traversal, including absolute paths (which os.path.join would
        otherwise let through).
        '''
        if (path is None
                or not isinstance(path, str)
                or not path.strip()
                or os.path.isabs(path)):
            return None
        folder = os.path.abspath(self.folder)
        candidate = os.path.abspath(os.path.join(folder, path))
        try:
            if os.path.commonpath((folder, candidate)) != folder:
                return None
        except ValueError:
            return None
        return candidate

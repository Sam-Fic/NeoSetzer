#!/usr/bin/env python3
# coding: utf-8

'''A project-wide, UI-neutral problem model.

The build log, missing project files, and externally changed files all describe
work that needs user attention.  This module turns them into one deterministic
collection so GTK views can filter and group without reimplementing parser
semantics.
'''

from dataclasses import dataclass
from collections import Counter


SEVERITIES = ('error', 'warning', 'info')


@dataclass(frozen=True)
class ProjectProblem:
    severity: str
    source: str
    stage: str
    filename: str | None
    line: int | None
    description: str
    actions: tuple[str, ...]


class ProjectProblemCenter:
    '''Build and query a normalized collection of project problems.'''

    def __init__(self, problems=()):
        self._problems = tuple(problems)

    @classmethod
    def from_build_log(cls, build_log_data, missing_files=(), external_changes=()):
        problems = []
        for item in build_log_data.get('items', ()):
            severity_name, stage, filename, line, description = item
            severity = {
                'Error': 'error',
                'Warning': 'warning',
                'Badbox': 'info',
            }.get(severity_name, 'info')
            actions = ['show-build-log']
            if filename:
                actions.append('open-file')
            if isinstance(line, int) and line > 0:
                actions.append('jump-to-source')
            problems.append(ProjectProblem(
                severity, 'build', stage or 'LaTeX', filename,
                line if isinstance(line, int) and line > 0 else None,
                description, tuple(actions)))

        for filename in missing_files:
            problems.append(ProjectProblem(
                'error', 'dependency', 'Project', filename, None,
                'Referenced project file is missing',
                ('open-root-document', 'show-dependency')))

        for filename in external_changes:
            problems.append(ProjectProblem(
                'warning', 'external-change', 'Workspace', filename, None,
                'File changed outside NeoSetzer',
                ('open-file', 'reload-file')))

        return cls(sorted(problems, key=cls._sort_key))

    @staticmethod
    def _sort_key(problem):
        order = {'error': 0, 'warning': 1, 'info': 2}
        return (order[problem.severity], problem.filename or '',
                problem.line or -1, problem.description)

    @property
    def problems(self):
        return self._problems

    def filter(self, severities=None, sources=None, query=''):
        severity_set = set(severities or SEVERITIES)
        source_set = set(sources) if sources is not None else None
        query = query.casefold().strip()
        result = []
        for problem in self._problems:
            if problem.severity not in severity_set:
                continue
            if source_set is not None and problem.source not in source_set:
                continue
            searchable = ' '.join((problem.stage, problem.filename or '',
                                   problem.description)).casefold()
            if query and query not in searchable:
                continue
            result.append(problem)
        return tuple(result)

    def grouped_by_file(self, problems=None):
        grouped = {}
        for problem in self._problems if problems is None else problems:
            grouped.setdefault(problem.filename or problem.stage, []).append(problem)
        return {key: tuple(value) for key, value in grouped.items()}

    def counts(self):
        return Counter(problem.severity for problem in self._problems)

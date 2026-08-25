#!/usr/bin/env python3
# coding: utf-8

'''Explainable, non-destructive LaTeX preamble package suggestions.'''

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PackageSuggestion:
    package: str
    reason: str
    command: str
    available_in_database: bool
    insertion: str


_COMMAND_RECOMMENDATIONS = (
    (r'\\includegraphics\b', 'graphicx', '\\includegraphics',
     'Image inclusion commands require the graphicx package.'),
    (r'\\href\b|\\url\b', 'hyperref', '\\href / \\url',
     'Hyperlink commands are provided by the hyperref package.'),
    (r'\\SI\b|\\qty\b|\\num\b|\\unit\b', 'siunitx', '\\SI / \\qty',
     'Consistent number-and-unit commands are provided by siunitx.'),
    (r'\\ce\b', 'mhchem', '\\ce',
     'Chemical formula commands are provided by mhchem.'),
    (r'\\begin\{tikzpicture\}', 'tikz', 'tikzpicture',
     'The tikzpicture environment is provided by TikZ.'),
    (r'\\todo\b', 'todonotes', '\\todo',
     'Inline todo commands are provided by todonotes.'),
    (r'\\toprule\b|\\midrule\b|\\bottomrule\b', 'booktabs',
     '\\toprule / \\midrule / \\bottomrule',
     'Professional table rule commands are provided by booktabs.'),
)
_USEPACKAGE_RE = re.compile(
    r'\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\{([^{}]+)\}')


class PreambleAssistant:
    '''Suggest packages but leave adding/removing them to the user interface.'''

    @staticmethod
    def existing_packages(source_text, packages_detailed=None):
        packages = set(packages_detailed or ())
        if isinstance(source_text, str):
            for match in _USEPACKAGE_RE.finditer(source_text):
                packages.update(name.strip() for name in match.group(1).split(',')
                                if name.strip())
        return frozenset(packages)

    @classmethod
    def suggest(cls, source_text, packages_detailed=None, packages_dict=None):
        if not isinstance(source_text, str):
            return ()
        installed = cls.existing_packages(source_text, packages_detailed)
        known_packages = set(packages_dict or ())
        suggestions = []
        for expression, package, command, reason in _COMMAND_RECOMMENDATIONS:
            if package in installed or not re.search(expression, source_text):
                continue
            suggestions.append(PackageSuggestion(
                package, reason, command, package in known_packages,
                '\\usepackage{' + package + '}'))
        return tuple(suggestions)

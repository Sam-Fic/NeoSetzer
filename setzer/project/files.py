#!/usr/bin/env python3
# coding: utf-8

'''Safe, source-level project file discovery for LaTeX projects.'''

from dataclasses import dataclass
import os
import re


TEXT_EXTENSIONS = frozenset(('.tex', '.bib', '.cls', '.sty', '.lco', '.loc'))
MAX_SOURCE_BYTES = 2 * 1024 * 1024

_COMMAND_RE = re.compile(
    r'\\(?P<command>input|include|subfile|bibliography|addbibresource|'
    r'includegraphics|documentclass|usepackage|LoadLetterOption)'
    r'(?:\[[^\]]*\])?\{(?P<value>[^{}]+)\}')


def scan_document_dependencies(filename):
    '''离线扫描单个 .tex 文件的 include / 项目依赖 / bib 引用，带 offset。

    与 ParserLaTeX.parse_symbols 用同一套模块级正则（惰性导入，避免把
    parser_latex 的 gi 依赖拉进本模块导入图），保证未打开文件与增量解析
    提取到的符号行为一致。供 DataProvider 在主文档未打开时构建 Files 列表。

    返回 (latex_includes, project_files, bibliographies)：
    前两者为 [(带扩展名文件名, offset)]，bibliographies 为 set。
    '''
    from setzer.document.parser import parser_latex as _parser

    try:
        if os.path.getsize(filename) > MAX_SOURCE_BYTES:
            return (), (), frozenset()
        with open(filename, 'r', encoding='utf-8', errors='replace') as file:
            source = file.read()
    except OSError:
        return (), (), frozenset()

    latex_includes = []
    project_files = []
    bibliographies = set()
    for match in re.compile(_parser._OTHER_SYMBOLS_REGEX_PATTERN).finditer(source):
        offset = match.start()
        group1 = match.group(1)
        if group1 in ('include', 'input', 'subfile', 'subimport'):
            value = match.group(2).strip()
            if not value:
                continue
            if not value.endswith('.tex'):
                value += '.tex'
            latex_includes.append((value, offset))
        elif group1 == 'bibliography':
            for entry in match.group(2).split(','):
                entry = entry.strip()
                if entry:
                    bibliographies.add(entry + '.bib')
        elif group1 == 'addbibresource':
            for entry in match.group(2).split(','):
                entry = entry.strip()
                if entry:
                    bibliographies.add(entry)
    for match in re.compile(_parser._PROJECT_DEPENDENCIES_REGEX_PATTERN).finditer(source):
        command = match.group(1)
        value = match.group(2).strip()
        if command == 'LoadLetterOption' and not value.endswith(('.lco', '.loc')):
            value += '.lco'
        elif command == 'documentclass' and not value.endswith('.cls'):
            value += '.cls'
        project_files.append((value, match.start()))
    return latex_includes, project_files, frozenset(bibliographies)


@dataclass(frozen=True)
class ProjectFiles:
    root_filename: str
    files: tuple[str, ...]
    missing_files: tuple[str, ...]

    @property
    def text_files(self):
        return tuple(path for path in self.files
                     if os.path.splitext(path)[1].lower() in TEXT_EXTENSIONS)


class ProjectFileResolver:
    '''Resolve common LaTeX dependencies without executing TeX or shell code.'''

    def __init__(self, root_filename, project_root=None):
        if not isinstance(root_filename, str) or not root_filename:
            raise ValueError('A root filename is required')
        self.root_filename = os.path.abspath(root_filename)
        self.project_root = os.path.abspath(project_root or os.path.dirname(self.root_filename))
        self._real_project_root = os.path.realpath(self.project_root)

    def collect(self):
        pending = [self.root_filename]
        seen = set()
        files = []
        missing = set()
        while pending:
            filename = os.path.abspath(pending.pop())
            if filename in seen:
                continue
            seen.add(filename)
            if not self._inside_project(filename):
                continue
            if not os.path.isfile(filename):
                missing.add(filename)
                continue
            files.append(filename)
            if os.path.splitext(filename)[1].lower() != '.tex':
                continue
            for dependency, command in self._dependencies_from_tex(filename):
                if os.path.isfile(dependency):
                    if dependency not in seen:
                        pending.append(dependency)
                elif command not in ('documentclass', 'usepackage', 'LoadLetterOption'):
                    # Classes, packages and letter options can legitimately be
                    # installed in TEXMF rather than the project directory.
                    # Without kpathsea resolution, reporting each absent local
                    # candidate would create false project diagnostics.
                    missing.add(dependency)
        return ProjectFiles(
            self.root_filename, tuple(sorted(files)), tuple(sorted(missing)))

    def _dependencies_from_tex(self, filename):
        try:
            if os.path.getsize(filename) > MAX_SOURCE_BYTES:
                return ()
            with open(filename, 'r', encoding='utf-8', errors='replace') as file:
                source = file.read()
        except OSError:
            return ()
        directory = os.path.dirname(filename)
        dependencies = []
        for match in _COMMAND_RE.finditer(source):
            command = match.group('command')
            values = match.group('value').split(',') if command in ('bibliography', 'usepackage') else (match.group('value'),)
            for value in values:
                value = value.strip()
                if not value:
                    continue
                extension = self._extension_for(command, value)
                candidate = os.path.abspath(os.path.join(directory, value + extension))
                if self._inside_project(candidate):
                    dependencies.append((candidate, command))
        return tuple(dependencies)

    @staticmethod
    def _extension_for(command, value):
        if os.path.splitext(value)[1]:
            return ''
        if command in ('input', 'include', 'subfile'):
            return '.tex'
        if command in ('bibliography', 'addbibresource'):
            return '.bib'
        if command == 'documentclass':
            return '.cls'
        if command == 'usepackage':
            return '.sty'
        if command == 'LoadLetterOption':
            return '.lco'
        return ''

    def _inside_project(self, filename):
        '''Return whether ``filename`` resolves to a target inside the project.'''
        try:
            return (os.path.commonpath((self._real_project_root,
                                       os.path.realpath(filename)))
                    == self._real_project_root)
        except ValueError:
            return False

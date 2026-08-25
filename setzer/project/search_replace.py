#!/usr/bin/env python3
# coding: utf-8

'''Safe project-wide text search and previewed replacement.'''

from dataclasses import dataclass
import hashlib
import os
import re
import tempfile

from setzer.project.files import MAX_SOURCE_BYTES, TEXT_EXTENSIONS, ProjectFileResolver


MAX_MATCHES = 10000


@dataclass(frozen=True)
class SearchMatch:
    filename: str
    start: int
    end: int
    line: int
    column: int
    matched_text: str
    preview: str


@dataclass(frozen=True)
class ReplacementFile:
    filename: str
    original_digest: str
    original_text: str
    replacement_text: str
    replacement_count: int


@dataclass(frozen=True)
class ReplacementPlan:
    search_text: str
    replacement_text: str
    files: tuple[ReplacementFile, ...]
    blocked_files: tuple[str, ...]

    @property
    def replacement_count(self):
        return sum(file.replacement_count for file in self.files)

    def apply(self):
        '''Atomically replace each unchanged planned file.

        Every file is re-hashed before any write.  This avoids silently
        replacing a document that was edited after the preview was produced.
        Per-file atomic replacement protects each write from interruption; a
        cross-filesystem transaction is not possible, so callers retain this
        immutable plan as the preview/audit record.
        '''
        if self.blocked_files:
            raise ValueError('The replacement plan contains blocked files')
        current = {}
        for replacement_file in self.files:
            data = _read_project_text(replacement_file.filename)
            if data is None or _digest(data) != replacement_file.original_digest:
                raise ValueError('A project file changed after replacement preview')
            current[replacement_file.filename] = data
        for replacement_file in self.files:
            _atomic_write_text(replacement_file.filename,
                               replacement_file.replacement_text)
        return tuple(file.filename for file in self.files)


class ProjectSearchReplace:
    '''Search local project text files without touching editor buffers.'''

    def __init__(self, root_filename, project_root=None):
        self.root_filename = os.path.abspath(root_filename)
        self.project_root = os.path.abspath(project_root or
                                            os.path.dirname(self.root_filename))

    def search(self, query, *, case_sensitive=False, regex=False,
               whole_word=False, maximum_matches=MAX_MATCHES):
        pattern = _compile_pattern(query, case_sensitive, regex, whole_word)
        if pattern is None:
            return ()
        matches = []
        for filename in self._text_filenames():
            text = _read_project_text(filename)
            if text is None:
                continue
            for match in _nonempty_matches(pattern, text):
                line = text.count('\n', 0, match.start()) + 1
                line_start = text.rfind('\n', 0, match.start()) + 1
                preview_start = max(line_start, match.start() - 48)
                preview_end = text.find('\n', match.end())
                if preview_end == -1:
                    preview_end = min(len(text), match.end() + 48)
                preview = text[preview_start:preview_end].replace('\t', '    ')
                matches.append(SearchMatch(
                    filename, match.start(), match.end(), line,
                    match.start() - line_start, match.group(0), preview))
                if len(matches) >= maximum_matches:
                    return tuple(matches)
        return tuple(matches)

    def create_replacement_plan(self, query, replacement, *,
                                case_sensitive=False, regex=False,
                                whole_word=False, blocked_files=()):
        pattern = _compile_pattern(query, case_sensitive, regex, whole_word)
        if pattern is None:
            return ReplacementPlan(query, replacement, (), ())
        blocked = {os.path.abspath(filename) for filename in blocked_files}
        candidates = []
        for filename in self._text_filenames():
            text = _read_project_text(filename)
            if text is None:
                continue
            replacement_text, replacement_count = _replace_nonempty_matches(
                pattern, replacement, text)
            if replacement_count:
                candidates.append(ReplacementFile(
                    filename, _digest(text), text, replacement_text,
                    replacement_count))
        blocked = {candidate.filename for candidate in candidates
                   if candidate.filename in blocked}
        files = tuple(candidate for candidate in candidates
                      if candidate.filename not in blocked)
        return ReplacementPlan(query, replacement, files, tuple(sorted(blocked)))

    def _text_filenames(self):
        filenames = set(ProjectFileResolver(
            self.root_filename, self.project_root).collect().text_files)
        for directory, folders, names in os.walk(self.project_root):
            folders[:] = [folder for folder in folders
                          if folder not in ('.git', '.neosetzer', 'build', 'out')]
            for name in names:
                filename = os.path.abspath(os.path.join(directory, name))
                if (os.path.splitext(name)[1].lower() in TEXT_EXTENSIONS
                        and _inside(self.project_root, filename)):
                    filenames.add(filename)
        return tuple(sorted(filenames))


def _compile_pattern(query, case_sensitive, regex, whole_word):
    if not isinstance(query, str) or not query:
        return None
    expression = query if regex else re.escape(query)
    if whole_word:
        expression = r'(?<!\w)' + expression + r'(?!\w)'
    try:
        return re.compile(expression, 0 if case_sensitive else re.IGNORECASE)
    except re.error:
        return None


def _nonempty_matches(pattern, text):
    '''Yield matches that replace visible source text rather than empty positions.'''
    for match in pattern.finditer(text):
        if match.start() != match.end():
            yield match


def _replace_nonempty_matches(pattern, replacement, text):
    '''Apply a regular-expression replacement while ignoring zero-length matches.'''
    parts = []
    offset = 0
    count = 0
    for match in _nonempty_matches(pattern, text):
        parts.append(text[offset:match.start()])
        parts.append(match.expand(replacement))
        offset = match.end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[offset:])
    return ''.join(parts), count


def _read_project_text(filename):
    try:
        if os.path.getsize(filename) > MAX_SOURCE_BYTES:
            return None
        with open(filename, 'rb') as file:
            return file.read().decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def _atomic_write_text(filename, text):
    directory = os.path.dirname(filename)
    descriptor, temporary = tempfile.mkstemp(prefix='.neosetzer-replace-',
                                            dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(text.encode('utf-8'))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, filename)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _inside(root, filename):
    try:
        return os.path.commonpath((root, filename)) == root
    except ValueError:
        return False

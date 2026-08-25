#!/usr/bin/env python3
# coding: utf-8

'''Safe, reproducible export of a LaTeX project as a ZIP package.'''

from dataclasses import dataclass
import hashlib
import json
import os
import tempfile
import zipfile

from setzer.project.files import ProjectFileResolver


@dataclass(frozen=True)
class ProjectPackagePlan:
    project_root: str
    root_filename: str
    files: tuple[str, ...]
    missing_files: tuple[str, ...]
    configuration_file: str | None

    @property
    def archive_root(self):
        return os.path.basename(os.path.normpath(self.project_root)) or 'project'

    def manifest(self):
        return {
            'format_version': 1,
            'root_document': os.path.relpath(self.root_filename, self.project_root),
            'files': [
                {
                    'path': os.path.relpath(filename, self.project_root),
                    'sha256': _sha256(filename),
                }
                for filename in self.files
            ],
            'missing_files': [
                os.path.relpath(filename, self.project_root)
                for filename in self.missing_files
            ],
            'configuration_file': (
                os.path.relpath(self.configuration_file, self.project_root)
                if self.configuration_file else None),
        }


class ProjectPackageExporter:
    '''Collect and export only project-contained source-level dependencies.'''

    def __init__(self, root_filename, project_root=None):
        self.root_filename = os.path.abspath(root_filename)
        self.project_root = os.path.abspath(project_root or
                                            os.path.dirname(self.root_filename))

    def create_plan(self, include_configuration=True):
        project_files = ProjectFileResolver(
            self.root_filename, self.project_root).collect()
        configuration_file = os.path.join(
            self.project_root, '.neosetzer', 'build.json')
        if not include_configuration or not os.path.isfile(configuration_file):
            configuration_file = None
        files = set(project_files.files)
        if configuration_file:
            files.add(configuration_file)
        return ProjectPackagePlan(
            self.project_root, self.root_filename, tuple(sorted(files)),
            project_files.missing_files, configuration_file)

    def export(self, destination, plan=None):
        '''Write a new ZIP atomically; never overwrite an existing user file.'''
        plan = plan or self.create_plan()
        destination = os.path.abspath(destination)
        if not destination.lower().endswith('.zip'):
            destination += '.zip'
        if os.path.exists(destination):
            raise FileExistsError(destination)
        if not _inside(self.project_root, destination) and not os.path.isdir(
                os.path.dirname(destination)):
            raise FileNotFoundError(os.path.dirname(destination))
        for filename in plan.files:
            if not _inside(plan.project_root, filename) or not os.path.isfile(filename):
                raise ValueError('Project package plan contains an invalid file')
        descriptor, temporary = tempfile.mkstemp(
            prefix='.neosetzer-export-', suffix='.zip',
            dir=os.path.dirname(destination))
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for filename in plan.files:
                    archive.write(filename, self._archive_name(plan, filename))
                archive.writestr(self._archive_name(plan, 'MANIFEST.json'),
                                 json.dumps(plan.manifest(), indent=2,
                                            ensure_ascii=False) + '\n')
            os.replace(temporary, destination)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return destination

    @staticmethod
    def _archive_name(plan, filename):
        if filename == 'MANIFEST.json':
            relative = filename
        else:
            relative = os.path.relpath(filename, plan.project_root)
        return plan.archive_root + '/' + relative.replace(os.sep, '/')


def _sha256(filename):
    digest = hashlib.sha256()
    with open(filename, 'rb') as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _inside(root, filename):
    try:
        return os.path.commonpath((root, filename)) == root
    except ValueError:
        return False

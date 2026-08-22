#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''Create user-writable copies of the bundled NeoSetzer example project.'''

from __future__ import annotations

import builtins
import os
import shutil
from typing import Final


EXAMPLE_PROJECT_DIRECTORY: Final = 'example_project'
MAIN_DOCUMENT_FILENAME: Final = 'main.tex'
DEFAULT_PROJECT_NAME: Final = 'NeoSetzer Example'


def _(message: str) -> str:
    '''Translate lazily after the application installs its gettext function.'''
    return getattr(builtins, '_', lambda value: value)(message)


class ExampleProjectError(ValueError):
    '''A user-safe error raised when an example project cannot be copied.'''


class ExampleProjectStore:
    '''Copy a read-only bundled example project into a unique user directory.'''

    def __init__(self, resources_directory: str, destination_directory: str):
        self.source_directory = self._validated_directory(resources_directory)
        self.destination_directory = self._validated_directory(destination_directory)

    def create(self) -> str:
        '''Create a unique project copy and return its root ``main.tex`` path.'''
        self._validate_source()
        project_directory = self._reserve_project_directory()
        try:
            self._copy_source_to(project_directory)
            main_document = os.path.join(project_directory, MAIN_DOCUMENT_FILENAME)
            if not os.path.isfile(main_document):
                raise ExampleProjectError(_('The example project is incomplete'))
            return main_document
        except Exception as error:
            shutil.rmtree(project_directory, ignore_errors=True)
            if isinstance(error, ExampleProjectError):
                raise
            raise ExampleProjectError(_('The example project could not be created')) from error

    @staticmethod
    def _validated_directory(path: str) -> str:
        if not isinstance(path, str) or not path.strip():
            raise ValueError(_('A directory is required'))
        return os.path.abspath(path)

    def _validate_source(self):
        if not os.path.isdir(self.source_directory):
            raise ExampleProjectError(_('The bundled example project is missing'))
        main_document = os.path.join(self.source_directory, MAIN_DOCUMENT_FILENAME)
        if not os.path.isfile(main_document) or os.path.islink(main_document):
            raise ExampleProjectError(_('The example project is incomplete'))
        for root, directories, filenames in os.walk(self.source_directory):
            if os.path.islink(root):
                raise ExampleProjectError(_('The bundled example project is invalid'))
            for name in directories + filenames:
                if os.path.islink(os.path.join(root, name)):
                    raise ExampleProjectError(_('The bundled example project is invalid'))

    def _reserve_project_directory(self) -> str:
        try:
            os.makedirs(self.destination_directory, mode=0o700, exist_ok=True)
        except OSError as error:
            raise ExampleProjectError(_('The selected folder could not be used')) from error
        if not os.path.isdir(self.destination_directory):
            raise ExampleProjectError(_('The selected folder could not be used'))

        for index in range(1, 10_000):
            name = DEFAULT_PROJECT_NAME if index == 1 else f'{DEFAULT_PROJECT_NAME} {index}'
            project_directory = os.path.join(self.destination_directory, name)
            try:
                os.mkdir(project_directory, mode=0o700)
            except FileExistsError:
                continue
            except OSError as error:
                raise ExampleProjectError(_('The example project could not be created')) from error
            return project_directory
        raise ExampleProjectError(_('Too many example project copies already exist'))

    def _copy_source_to(self, project_directory: str):
        for name in os.listdir(self.source_directory):
            source_path = os.path.join(self.source_directory, name)
            destination_path = os.path.join(project_directory, name)
            if os.path.isdir(source_path):
                shutil.copytree(source_path, destination_path, copy_function=shutil.copy2)
            else:
                shutil.copy2(source_path, destination_path)

        # Resources installed by a package may be mode 0444. The copied project
        # belongs to the user and is expressly intended for editing, so restore
        # owner write access for every copied directory and regular file.
        for root, directories, filenames in os.walk(project_directory):
            os.chmod(root, os.stat(root).st_mode | 0o700)
            for name in directories:
                path = os.path.join(root, name)
                os.chmod(path, os.stat(path).st_mode | 0o700)
            for name in filenames:
                path = os.path.join(root, name)
                os.chmod(path, os.stat(path).st_mode | 0o600)

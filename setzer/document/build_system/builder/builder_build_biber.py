#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
# along with this program. If not, see <http://www.gnu.org/licenses/>

import os
import os.path
import shutil
import subprocess

import setzer.document.build_system.builder.builder_build as builder_build
from setzer.app.service_locator import ServiceLocator


class BuilderBuildBiber(builder_build.BuilderBuild):

    def __init__(self):
        builder_build.BuilderBuild.__init__(self)

    def run(self, query):
        tex_filename = query.tex_filename
        filename = os.path.splitext(os.path.basename(tex_filename))[0]

        arguments = ['biber']
        arguments.append(filename)

        query.biber_data['ran_on_files'].append(filename)

        custom_env = builder_build.build_env()
        source_directory = os.path.dirname(tex_filename)
        custom_env['BIBINPUTS'] = source_directory + os.pathsep + custom_env.get('BIBINPUTS', '')
        try:
            self.process = builder_build.popen_no_window(
                arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=self.get_output_directory(query), env=custom_env)
        except FileNotFoundError:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_not_working', 'biber missing')
            return
        self.process.wait()

        self.parse_biber_log(query, self.get_output_filename(query, '.blg'))

        query.jobs.insert(0, 'build_latex')

    def stop_running(self):
        if self.process != None:
            self.process.kill()
            self.process = None

    def parse_biber_log(self, query, log_filename):
        pass



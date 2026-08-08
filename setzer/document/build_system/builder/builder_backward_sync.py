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

import os.path
import subprocess

import setzer.document.build_system.builder.builder_build as builder_build
from setzer.app.service_locator import ServiceLocator
from setzer.helpers.synctex_folder import synctex_folder


class BuilderBackwardSync(builder_build.BuilderBuild):

    def __init__(self):
        builder_build.BuilderBuild.__init__(self)

        self.config_folder = ServiceLocator.get_config_folder()
        self.backward_synctex_regex = ServiceLocator.get_regex_object(r'\nOutput:.*\nInput:(.*\.tex)\nLine:([0-9]+)\nColumn:(?:[0-9]|-)+\nOffset:(?:[0-9]|-)+\nContext:.*\n')

        self.process = None

    def run(self, query):
        tex_filename = query.tex_filename

        if not query.can_sync:
            query.backward_sync_result = None
            return

        synctex_dir = synctex_folder(self.config_folder, query.tex_filename)
        arguments = ['synctex', 'edit', '-o']
        arguments.append(str(query.backward_sync_data['page']) + ':' + str(query.backward_sync_data['x']) + ':' + str(query.backward_sync_data['y']) + ':' + os.path.splitext(query.tex_filename)[0] + '.pdf')
        arguments.append('-d')
        arguments.append(synctex_dir)
        try:
            process = builder_build.popen_no_window(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_not_working', 'synctex missing')
            return
        # 暴露给 stop_running 以便外部中止；本方法用局部 process 操作，
        # 避免 stop_running 置 self.process=None 后 wait/communicate 崩。
        self.process = process

        process.wait()

        result = None
        if process != None:
            raw = process.communicate()[0].decode('utf-8')
            self.process = None

            match = self.backward_synctex_regex.search(raw)
            if match != None:
                result = dict()
                result['filename'] = match.group(1)
                result['line'] = max(int(match.group(2)) - 1, 0)
                result['word'] = query.backward_sync_data['word']
                result['context'] = query.backward_sync_data['context']
                result['pdf_line_offset'] = query.backward_sync_data.get('pdf_line_offset')
                result['pdf_line_text'] = query.backward_sync_data.get('pdf_line_text')

        query.backward_sync_result = result

    def stop_running(self):
        if self.process != None:
            self.process.kill()
            self.process = None

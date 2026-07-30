#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
import sys
import base64
import shutil
import pexpect
from operator import itemgetter

import setzer.document.build_system.builder.builder_build as builder_build
import setzer.document.build_system.latex_log_parser.latex_log_parser as latex_log_parser
from setzer.app.service_locator import ServiceLocator


class BuilderBuildLaTeX(builder_build.BuilderBuild):

    def __init__(self):
        builder_build.BuilderBuild.__init__(self)

        self.config_folder = ServiceLocator.get_config_folder()
        self.latex_log_parser = latex_log_parser.LaTeXLogParser()

    def run(self, query):
        build_command_defaults = dict()
        build_command_defaults['pdflatex'] = 'pdflatex -synctex=1 -interaction=nonstopmode'
        build_command_defaults['xelatex'] = 'xelatex -synctex=1 -interaction=nonstopmode'
        build_command_defaults['lualatex'] = 'lualatex --synctex=1 --interaction=nonstopmode'
        build_command_defaults['tectonic'] = 'tectonic --synctex --keep-logs'

        latex_interpreter = query.build_data['latex_interpreter']
        if latex_interpreter == 'tectonic':
            build_command = build_command_defaults[latex_interpreter]
            build_command += ' --outdir "' + os.path.dirname(query.tex_filename) + '" "' 
        elif query.build_data['use_latexmk']:
            if latex_interpreter == 'pdflatex':
                interpreter_option = 'pdf'
            else:
                interpreter_option = latex_interpreter
            build_command = 'latexmk -' + interpreter_option + ' -synctex=1 -interaction=nonstopmode'
            build_command += query.build_data['additional_arguments']
            build_command += ' -output-directory="' + os.path.dirname(query.tex_filename) + '" "'
        else:
            build_command = build_command_defaults[latex_interpreter]
            build_command += query.build_data['additional_arguments']
            build_command += ' -output-directory="' + os.path.dirname(query.tex_filename) + '" "'
        build_command += query.tex_filename + '"'

        try:
            self.process = pexpect.spawn(build_command, cwd=os.path.dirname(query.tex_filename))
        except pexpect.exceptions.ExceptionPexpect:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_missing', latex_interpreter)
            return

        while True:
            try:
                out = self.process.expect(['\r\n\r\n', pexpect.TIMEOUT, pexpect.EOF], timeout=20)
            except AttributeError:
                break
            if out == 0:
                pass
            elif out == 1:
                for line in self.process.before.split(b'\n'):
                    if line.startswith(b'!'):
                        self.process.sendcontrol('c')
                        self.process.sendline('x')
            else:
                break

        # parse results
        try:
            if self.parse_build_log(query):
                return
        except FileNotFoundError as e:
            self.cleanup_files(query)
            self.throw_build_error(query, 'interpreter_not_working', 'log file missing')
            return

        query.can_sync = self.copy_synctex_file(query)
        self.cleanup_files(query)

        pdf_filename = query.tex_filename.rsplit('.tex', 1)[0] + '.pdf'
        if query.error_count > 0:
            if os.path.isfile(pdf_filename):
                os.remove(pdf_filename)
            pdf_filename = None
        # 修复：LaTeX 日志解析器只匹配 `!` 错误行，但 xelatex 的 xdvipdfmx
        # / lualatex 的 fontloader 等子工具链 fatal 错误不会写 `!` 错误
        # （只在 stdout 写 "fatal: ..." 之类），导致 error_count==0 但 PDF
        # 实际没生成，setzer 误报"成功"。这里在 error_count==0 时再检查
        # 一次 PDF 文件是否真的存在 + 大小合理，缺则合成一个 error 让上层
        # 状态栏显示失败 + 弹出 Build failed toast。
        elif not os.path.isfile(pdf_filename):
            query.error_count = 1
            # 尝试从 .log 末尾捞最后几行作为错误信息，常见为
            # "! xdvipdfmx:fatal: ..." 或类似提示。
            synthesized_msg = self._extract_silent_failure_reason(query)
            query.log_messages = {
                query.tex_filename: {
                    'error': [{'message': synthesized_msg, 'line_no': 0}],
                    'warning': [],
                    'badbox': [],
                }
            }
            pdf_filename = None

        with query.build_result_lock:
            query.build_result = {'pdf_filename': pdf_filename, 
                                  'has_synctex_file': query.can_sync,
                                  'log_messages': query.log_messages,
                                  'bibtex_log_messages': query.bibtex_log_messages,
                                  'error': None,
                                  'error_arg': None}

    def stop_running(self):
        if self.process != None:
            self.process.sendcontrol('c')
            self.process.sendline('x')
            self.process.terminate(True)
            self.process = None

    def parse_build_log(self, query):
        query.log_messages = list()
        query.error_count = 0

        log_items = self.latex_log_parser.parse_build_log(query.tex_filename)
        additional_jobs = self.latex_log_parser.get_additional_jobs(log_items, query)
        file_no = 0

        for job in additional_jobs:
            query.jobs.insert(0, job)
            return True

        for filename, items in log_items.items():
            query.error_count += len(items['error'])
            items['error'].sort(key=itemgetter(1))
            items['warning'].sort(key=itemgetter(1))
            items['badbox'].sort(key=itemgetter(1))
        query.log_messages = log_items

        return False

    def copy_synctex_file(self, query):
        move_from = os.path.splitext(query.tex_filename)[0] + '.synctex.gz'
        folder = self.config_folder + '/' + base64.urlsafe_b64encode(str.encode(query.tex_filename)).decode()
        move_to = folder + '/' + os.path.splitext(os.path.basename(query.tex_filename))[0] + '.synctex.gz'

        if not os.path.exists(folder):
            os.makedirs(folder)

        try: shutil.copyfile(move_from, move_to)
        except FileNotFoundError: return False
        else: return True

    def _extract_silent_failure_reason(self, query):
        '''engine 退出 0 但 PDF 没生成时，从 .log 末尾 / stdout 抓取错误线索。

        xelatex 的 xdvipdfmx 致命错误不会出现在 LaTeX 日志的 `!` 错误里，
        但会写进 .log 末尾（"xdvipdfmx:fatal: ..."）。在无 .log 或
        无匹配时回落到通用提示，避免用户面对一个空错误。'''
        log_path = query.tex_filename.rsplit('.tex', 1)[0] + '.log'
        try:
            with open(log_path, 'rb') as f:
                # 只读末尾 8KB 避免大日志拖累
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode('utf-8', errors='replace')
        except (OSError, FileNotFoundError):
            return 'No PDF file was produced. The LaTeX engine exited with code 0 but no output file was written. This often indicates a toolchain failure (e.g. xdvipdfmx/fontloader) that the LaTeX log parser does not detect.'

        # 优先级匹配：xdvipdfmx fatal > 任何 fatal > kpathsea 严重警告
        for pattern in ('xdvipdfmx:fatal', 'dvipdfmx:fatal',
                        'fatal:', 'luaotfload', 'kpathsea'):
            idx = tail.lower().rfind(pattern)
            if idx != -1:
                # 截取错误行（去首尾空白）
                line_end = tail.find('\n', idx)
                snippet = tail[idx:line_end if line_end != -1 else len(tail)].strip()
                if len(snippet) > 200:
                    snippet = snippet[:200] + '…'
                return snippet

        return 'No PDF file was produced. The LaTeX engine exited with code 0 but no output file was written. Check the build log for toolchain-level errors (xdvipdfmx, fontloader, kpathsea).'



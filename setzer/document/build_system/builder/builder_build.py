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
import sys
import subprocess

from setzer.app.service_locator import ServiceLocator


def popen_no_window(args, **kwargs):
    '''跨平台 subprocess.Popen 包装：Windows 上设 CREATE_NO_WINDOW
    避免每次调用 LaTeX 工具链时弹出控制台窗口；Unix 上透传。
    '''
    if sys.platform == 'win32':
        kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
    return subprocess.Popen(args, **kwargs)


def build_env():
    '''构建子进程环境（上游 issue #182）：保证个人 TeX 树可见。

    kpathsea 靠 TEXMFHOME 定位用户的 .cls/.sty/.bst 等文件。从终端启动时
    该变量继承自 shell，一切正常；从桌面菜单/Flatpak 启动时不经过 shell，
    环境里没有它 → 个人宏包"命令行能编译、Setzer 里找不到"。优先级：

    1. 偏好 texmf_home 非空 → 注入（用户显式指定，~ 会展开）
    2. 环境已有 TEXMFHOME（终端启动）→ 原样沿用
    3. 都没有 → 注入 TeX Live 默认值 ~/texmf

    返回 os.environ 的拷贝，绝不原地修改父进程环境。
    '''
    env = os.environ.copy()
    texmf_home = ''
    try:
        settings = ServiceLocator.get_settings()
        if settings is not None:
            value = settings.get_value('preferences', 'texmf_home')
            if isinstance(value, str):
                texmf_home = value.strip()
    except Exception:
        # 设置系统尚未初始化（极端时序）→ 退回纯继承行为
        pass
    if texmf_home:
        env['TEXMFHOME'] = os.path.expanduser(texmf_home)
    elif 'TEXMFHOME' not in env:
        env['TEXMFHOME'] = os.path.expanduser('~/texmf')
    return env


class BuilderBuild(object):

    def __init__(self):
        self.process = None

    def throw_build_error(self, query, error, error_arg):
        with query.build_result_lock:
            query.build_result = {'error': error,
                                 'error_arg': error_arg}

    def get_output_directory(self, query):
        '''Return the validated project output directory or the source folder.'''
        output_directory = query.build_data.get('output_directory')
        if isinstance(output_directory, str) and output_directory:
            return output_directory
        return os.path.dirname(query.tex_filename)

    def get_output_filename(self, query, ending):
        basename = os.path.splitext(os.path.basename(query.tex_filename))[0]
        return os.path.join(self.get_output_directory(query), basename + ending)

    def cleanup_files(self, query):
        if query.build_data['do_cleanup']:
            self.cleanup_build_files(query)
            self.cleanup_glossaries_files(query)

    def cleanup_build_files(self, query):
        file_endings = ['.aux', '.blg', '.bbl', '.dvi', '.xdv', '.fdb_latexmk', '.fls', '.idx' , '.ilg',
                        '.ind', '.log', '.nav', '.out', '.snm', '.synctex.gz', '.toc',
                        '.ist', '.glo', '.glg', '.acn', '.alg',
                        '.bcf', '.run.xml', '.out.ps',
                        # latexmk -pdfps（issue #223）在输出目录留下 jobname.ps
                        # （.out.ps 是 hyperref 的，覆盖不到它）
                        '.ps']
        for ending in file_endings:
            try: os.remove(self.get_output_filename(query, ending))
            except FileNotFoundError: pass

    def cleanup_glossaries_files(self, query):
        for ending in ['.gls', '.acr']:
            try: os.remove(self.get_output_filename(query, ending))
            except FileNotFoundError: pass



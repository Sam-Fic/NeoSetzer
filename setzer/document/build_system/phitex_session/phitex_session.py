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
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''PhiTeX 常驻 watch（session）的控制面。

PhiTeX 的增量依赖图活在进程内存里：`phitex build` 每次冷启动，实测 62 页
文档改一行要 1.25 s，比 pdflatex 的 0.26 s 还慢；而同一文档的常驻
`phitex watch` 只要 6–14 ms，并且精确报出哪一页变了。所以适配 PhiTeX 的关键
不是换一条命令，而是换进程生命周期：start 一次，此后每轮构建只做
wait → 取 PDF → 取问题。

本模块只在 worker 线程里被调用（builder 的 run 不在主线程），因此全部用
阻塞式 subprocess，不引入新的异步管道。

所有子命令都显式带 `--session <pid>`：同目录下放多个 main 文件时，不带该
参数会解析到"这里在跑的那个 watch"，实测会把上一次构建的 PDF 路径当成本次
结果返回。
'''

import json
import os
import re
import shlex
import shutil
import subprocess
import sys

from setzer.document.build_system.builder.builder_build import build_env, popen_no_window

_LAUNCH_RE = re.compile(r'(?:started|running) pid (\d+)')

# start 等首轮完整构建；wait 只等增量，取一个保守上限即可。
START_TIMEOUT = '10m'
WAIT_TIMEOUT = '2m'

# session 子命令的进程开销实测约 6 ms/次，相对增量构建的 6–14 ms 不可忽略，
# 因此一次构建只调用必需的三条。
_CALL_TIMEOUT_S = 180


class PhitexSession:
    '''一个文档对应一个常驻 watch 进程。'''

    def __init__(self, tex_filename, output_directory, enable_synctex=True,
                 additional_arguments=''):
        self.tex_filename = tex_filename
        self.output_directory = output_directory
        self.enable_synctex = enable_synctex
        self.additional_arguments = additional_arguments
        self.pid = None

    @property
    def config_signature(self):
        '''watch 的启动配置指纹。

        PhiTeX 只在启动那一刻读入这些参数（-o、--synctex、--shell-escape、
        项目额外参数，以及进程环境里的 TEXMFHOME），之后改设置对正在跑的
        session 完全无效。构建前比对指纹，不一致就先停掉旧的再起新的——
        否则用户会对着一个"改了没反应"的开关（假生效）。

        引擎名不在指纹里：PhiTeX 自己从文档判断（fontspec → xelatex），也自己
        读 `% !TEX program` 注释，所以不需要 NeoSetzer 转译成 --engine。
        '''
        return (self.tex_filename, self.output_directory, self.enable_synctex,
                self.additional_arguments)

    @staticmethod
    def available():
        return shutil.which('phitex') is not None

    @staticmethod
    def run(*args, timeout=_CALL_TIMEOUT_S):
        '''直接跑一条 phitex 子命令，返回 (returncode, stdout)。

        给"读版本 / 读写用户配置 / 升级"这类非构建路径的控制命令用。它们在
        偏好页里被调用，那里没有 query 对象，也不该复用实例的 _call。
        调用方负责放到后台线程——每条都是子进程，实测约 4 ms。
        '''
        executable = shutil.which('phitex')
        if executable is None:
            return None, None
        try:
            kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace')
            if sys.platform == 'win32':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen([executable] + list(args), **kwargs)
        except (FileNotFoundError, OSError):
            return None, None
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
            return None, None
        return process.returncode, stdout or ''

    @classmethod
    def installed_version(cls):
        '''`phitex 1.0.2` 里的版本号，取不到返回 None（本地命令，不联网）。'''
        returncode, stdout = cls.run('--version')
        if returncode != 0 or not stdout:
            return None
        parts = stdout.split()
        if len(parts) >= 2 and parts[0] == 'phitex':
            return parts[1]
        return None

    @classmethod
    def update_check_enabled(cls):
        '''用户配置里 update-check 是否开启。

        PhiTeX 自己每日后台检查并把提示给编辑器；默认关。这里读它的值，
        不替它做检查——NeoSetzer 不联网获取第三方版本信息。
        '''
        returncode, stdout = cls.run('config', 'update-check')
        if returncode != 0 or not stdout:
            return False
        # 输出形如 "  update-check  true (yours)" 或 "  update-check  off (default)"。
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith('update-check'):
                value = stripped[len('update-check'):].strip().split()[:1]
                if value:
                    return value[0].lower() in ('true', 'on')
        return False

    @classmethod
    def set_update_check(cls, enabled):
        '''写用户级配置（`phitex config update-check on|off`），可逆。

        刻意只走 PhiTeX 自己的命令、不自己改它的 toml：键的写法、注释保留、
        与项目级配置的优先级都是它的职责范围，我们重复实现一遍就是漂移点。
        '''
        returncode, stdout = cls.run('config', 'update-check',
                                     'on' if enabled else 'off')
        return returncode == 0

    @classmethod
    def upgrade(cls):
        '''跑 `phitex upgrade -y`，返回 (returncode, 输出)。

        必须显式 -y：非终端环境下（我们这里 stdout 是管道）PhiTeX 什么都不问、
        也什么都不装，不带 -y 会静默变成空操作——界面上看起来"升级成功"而二进制
        没变。下载与校验都由 PhiTeX 自己做，NeoSetzer 只是触发它。
        '''
        return cls.run('upgrade', '-y', timeout=600)


    def start(self):
        '''拉起（或复用）该文档的 watch，成功返回 True。

        带 --exact：PhiTeX 默认只在编辑器空闲时写完整 PDF，中间状态是"追加
        更新段"；--exact 让每次增量构建都把完整 PDF 落盘，且这一步发生在
        增量构建内部（实测 62 页 6–14 ms）。若不带它、改用 `session pdf`
        现场物化 PDF，则每次要多花 ~285 ms（它重走一遍完整写出），增量优势
        被全部吃掉——实测数据见 build()。

        引擎自己的参数放在 `--` 之后（session start 的约定）。用 shlex 拆
        附加参数而不是 shell=True：这些字符串来自项目配置文件，不能执行。
        '''
        executable = shutil.which('phitex')
        if executable is None:
            return False
        watch_args = ['--exact']
        try:
            watch_args += shlex.split(self.additional_arguments or '')
        except ValueError:
            # 引号不闭合等项目配置里的畸形值：丢掉附加参数继续构建，
            # 而不是让整次构建起不来。
            pass
        if self.output_directory:
            watch_args += ['-o', self.output_directory]
        if self.enable_synctex:
            watch_args += ['--synctex']
        command = ([executable, 'session', 'start', '--timeout', START_TIMEOUT,
                    self.tex_filename, '--'] + watch_args)
        returncode, stdout = self._call(command)
        if returncode not in (0, 1) or not stdout:
            return False
        match = _LAUNCH_RE.search(stdout)
        if match is None:
            return False
        self.pid = int(match.group(1))
        return True

    def build(self, timeout=WAIT_TIMEOUT):
        '''等增量构建读入磁盘现状，返回 (是否成功, 状态 dict 或 None)。'''
        if self.pid is None:
            return False, None
        returncode, _ = self._call(self._session_args('wait', '--timeout', timeout))
        # wait 的退出码沿用引擎语义：0 无错、1 文档有错，两者都已构建完成；
        # 2/3 才是 PhiTeX 自己没跑起来，此时不拿旧状态冒充本次结果。
        if returncode not in (0, 1):
            return False, None
        return True, self.status()

    def status(self):
        _, stdout = self._call(self._session_args('status', '--json'))
        return self._first_json_object(stdout)

    def pdf_path(self, status=None):
        '''本次构建的完整 PDF 绝对路径。

        --exact 下 watch 已把 PDF 写好，路径直接取自 status，省掉一次
        `session pdf`（它现场物化完整 PDF，实测多花 ~285 ms）。只有状态里
        没有路径时才退回显式写出。
        '''
        if isinstance(status, dict):
            candidate = status.get('pdf')
            if candidate:
                if not os.path.isabs(candidate):
                    candidate = os.path.join(status.get('root') or '', candidate)
                path = os.path.abspath(os.path.normpath(candidate))
                if os.path.isfile(path):
                    return path
        _, stdout = self._call(self._session_args('pdf'))
        for line in (stdout or '').splitlines():
            candidate = line.strip()
            if candidate.endswith('.pdf'):
                return os.path.abspath(os.path.normpath(candidate))
        return None

    def problems(self):
        _, stdout = self._call(self._session_args('problems', '--json'))
        payload = self._first_json_object(stdout)
        if not isinstance(payload, dict):
            return None
        return payload.get('problems')

    def stop(self):
        if self.pid is None:
            return
        # -y：watch 退出前会问"要不要写出完整 PDF"，后台终止时无人应答。
        self._call(self._session_args('stop', '-y'))
        self.pid = None

    def _session_args(self, subcommand, *extra):
        return [shutil.which('phitex'), 'session', subcommand,
                '--session', str(self.pid)] + list(extra)

    def _call(self, command):
        '''在 worker 线程里同步跑一条 phitex 子命令，返回 (returncode, stdout)。'''
        try:
            process = popen_no_window(
                command, cwd=os.path.dirname(self.tex_filename) or None,
                env=build_env(), stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, errors='replace')
        except (FileNotFoundError, OSError):
            return None, None
        try:
            stdout, _ = process.communicate(timeout=_CALL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, None
        return process.returncode, stdout

    @staticmethod
    def _first_json_object(stdout):
        for line in (stdout or '').splitlines():
            line = line.strip()
            if line.startswith('{'):
                try:
                    return json.loads(line)
                except ValueError:
                    continue
        return None


# PhiTeX 的 problem.kind 到 NeoSetzer 日志分桶的映射。overfull/underfull 归
# badbox（与 latex_log_parser 的输出一致，构建日志按同一列渲染），reference/
# font/other 这类非致命项归 warning。
_BADBOX_KINDS = frozenset(('overfull', 'underfull'))


def to_log_messages(problems, project_root, main_tex_filename):
    '''把 `session problems --json` 转成 log_messages 的既有结构。

    结构必须是 {filename: {'error': [...], 'warning': [...], 'badbox': [...]}}，
    每个元素是 (error_type_or_None, line_number, text) 三元组——构建日志、
    行内波浪线和跳转都按它取值（见 builder_build_latex 里的同构注释）。

    PhiTeX 报的 file 是相对项目根的路径（实测 `bad.tex`），而 NeoSetzer 全
    按绝对路径索引，故在此展开；解析不出根目录时退回主文件所在目录。
    '''
    log_items = dict()
    for problem in problems or []:
        if not isinstance(problem, dict):
            continue
        kind = (problem.get('kind') or '').lower()
        severity = (problem.get('severity') or '').lower()
        if severity == 'error' or kind == 'error':
            bucket = 'error'
        elif kind in _BADBOX_KINDS:
            bucket = 'badbox'
        else:
            bucket = 'warning'
        text = problem.get('message') or ''
        detail = (problem.get('detail') or '').strip()
        if detail and detail not in text:
            text = '{}\n{}'.format(text, detail)
        line_number = problem.get('line')
        filename = problem.get('file') or main_tex_filename
        if not os.path.isabs(filename):
            base = project_root or os.path.dirname(main_tex_filename)
            filename = os.path.abspath(os.path.join(base, filename))
        log_items.setdefault(
            filename, {'error': [], 'warning': [], 'badbox': []})[bucket].append(
            (None, line_number if isinstance(line_number, int) else -1, text))

    for items in log_items.values():
        for bucket in items.values():
            bucket.sort(key=lambda item: item[1])
    return log_items


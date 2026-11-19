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

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试（本模块有纯解析函数单测）。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gio

import os
import subprocess
import sys
import threading

from setzer.helpers.observable import Observable

# 单文件超过该行数时不做逐行 diff 映射（性能降级，见上游 #216 讨论）。
DIFF_LINE_LIMIT = 5000
# diff 变更行数超过该值时同样降级（超大 rename/重排场景）。
DIFF_CHANGED_LIMIT = 2000
# 侧栏线性历史列表显示的提交条数上限（一次 `git log -n` 取回，避免分页）。
HISTORY_LIMIT = 12


def git_available():
    return GLib.find_program_in_path('git') is not None


def run_git(args, cwd, timeout=15):
    '''同步执行 git 子进程，返回 (returncode, combined_output)。

    stdout/stderr 合并返回，与 document_stats 的 texcount 模式一致。
    任何启动失败/超时都返回 (None, '')，调用方按失败处理。'''
    popen_kwargs = dict(
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if sys.platform == 'win32':
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(['git'] + list(args), **popen_kwargs)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, ''
    return result.returncode, result.stdout.decode('utf-8', errors='replace')


def parse_status_porcelain(output):
    '''解析 `git status --porcelain=v1 -b` 输出。

    返回 (branch, upstream, ahead, behind, changed_files)：
    - branch/upstream: 字符串或 None（空仓库 / 无 upstream）
    - ahead/behind: int
    - changed_files: [{'status': 'XY', 'path': 相对路径, 'untracked': bool}, ...]
      .gitignore 排除项 porcelain 默认不输出，无需额外过滤。
    '''
    branch = None
    upstream = None
    ahead = 0
    behind = 0
    files = []

    for line in output.splitlines():
        if line.startswith('## '):
            branch_line = line[3:]
            # 尚无 commit 的仓库：`## No commits yet on main`
            if branch_line.startswith('No commits yet on '):
                branch = branch_line[len('No commits yet on '):]
                continue
            if '...' in branch_line:
                branch_part, rest = branch_line.split('...', 1)
                branch = branch_part
                if ' [' in rest:
                    up, info = rest.split(' [', 1)
                    upstream = up or None
                    for part in info.rstrip(']').split(','):
                        part = part.strip()
                        try:
                            if part.startswith('ahead '):
                                ahead = int(part[6:])
                            elif part.startswith('behind '):
                                behind = int(part[7:])
                        except ValueError:
                            pass
                else:
                    upstream = rest or None
            else:
                branch = branch_line
        elif len(line) >= 4 and line[2] == ' ':
            status = line[:2]
            path = line[3:]
            # 重命名条目：`R  old -> new`，面向用户只显示新路径
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            files.append({'status': status, 'path': path, 'untracked': status == '??'})

    return branch, upstream, ahead, behind, files


def parse_log_history(output):
    '''解析 `git log --pretty=format:%h%x1f%s%x1f%an%x1f%ct` 输出。

    返回 [{'hash': 短哈希, 'subject': 标题, 'author': 作者,
    'timestamp': int}, ...]，顺序与 git log 一致（新 → 旧）。
    字段数不符（作者名里混入分隔符、空行等）的条目直接跳过。
    '''
    commits = list()
    for line in output.splitlines():
        parts = line.split('\x1f')
        if len(parts) != 4:
            continue
        short_hash, subject, author, stamp = parts
        try:
            timestamp = int(stamp)
        except ValueError:
            timestamp = 0
        commits.append({
            'hash': short_hash,
            'subject': subject,
            'author': author,
            'timestamp': timestamp,
        })
    return commits


def parse_num_diff(output):
    '''解析 `git diff HEAD -U0 -- file` 输出为行级标记。

    返回 dict：
    - 'added': set(1-based 行号) — HEAD 中不存在的新增行
    - 'modified': set(1-based 行号) — HEAD 中存在且有改动的行
    - 'deleted_after': set(1-based 行号) — 该行之前有 N 行被删除（红三角标记位置）
    - 'changed_count': int
    - 'degraded': bool — 变更量超过阈值，调用方应放弃逐行映射
    '''
    added = set()
    modified = set()
    deleted_after = set()
    changed = 0

    for line in output.splitlines():
        if not line.startswith('@@'):
            continue
        try:
            header = line[line.index('@@') + 2:line.rindex('@@')].split()
            old = header[0][1:]
            new = header[1][1:]
            old_parts = old.split(',')
            new_parts = new.split(',')
            old_count = int(old_parts[1]) if len(old_parts) > 1 else 1
            new_start = int(new_parts[0])
            new_count = int(new_parts[1]) if len(new_parts) > 1 else 1
        except (ValueError, IndexError):
            continue

        changed += old_count + new_count
        if new_count > 0:
            kind = added if old_count == 0 else modified
            for ln in range(new_start, new_start + new_count):
                kind.add(ln)
        elif old_count > 0:
            # `+0,0`（文件开头删行）时 new_start 为 0，标记落在第 1 行。
            deleted_after.add(max(new_start, 1))

        if changed > DIFF_CHANGED_LIMIT:
            break

    return {
        'added': added,
        'modified': modified,
        'deleted_after': deleted_after,
        'changed_count': changed,
        'degraded': changed > DIFF_CHANGED_LIMIT,
    }


class GitRepository(Observable):
    '''单个 git 仓库的共享状态层：gutter diff 与侧栏 Git 面板共用。

    - 按仓库根路径缓存实例（多文档同仓库只跑一份 git 命令）。
    - 状态刷新在后台线程执行，结果经 GLib.idle_add 回到主线程后 emit：
      'state_changed'(repo) / 'diff_changed'(repo, filename)。
    - .git/index 与 .git/HEAD 挂 Gio.FileMonitor：终端里的 commit/pull/
      stash 等操作自动触发刷新，无需轮询。
    '''

    _cache = dict()          # repo_root -> GitRepository
    _negative_cache = set()  # 已探测、确认不在任何 repo 内的目录

    def __init__(self, root):
        Observable.__init__(self)
        self.root = root
        self.state = None
        self._state_inflight = False
        self._state_dirty = False
        self._diffs = dict()          # filename -> diff marks dict
        self._diff_inflight = set()
        self._monitors = []
        self._monitor_debounce_id = None
        self._setup_monitors()
        self.refresh()

    @classmethod
    def get_for_path(cls, path):
        '''返回 path（文件或目录）所属仓库的 GitRepository；不在 repo 内
        或 git 不可用时返回 None。首次探测同步执行（git rev-parse 本地
        约 5ms，带超时保护），结果按仓库根缓存。'''
        if path is None or not git_available():
            return None
        directory = path if os.path.isdir(path) else os.path.dirname(path)
        if not directory:
            return None
        directory = os.path.realpath(directory)

        for root, repo in cls._cache.items():
            if directory == root or directory.startswith(root + os.sep):
                return repo
        if directory in cls._negative_cache:
            # 之前探测不在 repo 内。若该目录后来出现了 .git（git init），
            # 需重新探测；否则沿用负缓存。
            if not os.path.exists(os.path.join(directory, '.git')):
                return None
            cls._negative_cache.discard(directory)

        code, out = run_git(['rev-parse', '--show-toplevel'], directory, timeout=5)
        root = out.strip() if code == 0 else None
        if not root:
            cls._negative_cache.add(directory)
            return None
        root = os.path.realpath(root)

        repo = cls._cache.get(root)
        if repo is None:
            repo = GitRepository(root)
            cls._cache[root] = repo
        return repo

    @classmethod
    def init_repository(cls, directory, callback):
        '''在 directory 下执行 `git init`，完成后在主线程回调
        callback(error_message 或 None, repo 或 None)。

        不需要仓库信任确认：此刻仓库还不存在，不可能有预置的 hooks。'''
        threading.Thread(target=cls._init_thread, args=(directory, callback), daemon=True).start()

    @classmethod
    def _init_thread(cls, directory, callback):
        code, out = run_git(['init'], directory, timeout=60)
        if code != 0:
            error = out.strip() or _('Could not initialize the git repository.')
            GLib.idle_add(callback, error, None)
            return False
        # 探测时记下的「不在仓库内」负缓存会挡住刚建好的仓库，必须清掉
        # （realpath 与原始路径都清，避免调用方传入符号链接路径）。
        cls._negative_cache.discard(directory)
        cls._negative_cache.discard(os.path.realpath(directory))
        repo = cls.get_for_path(directory)
        error = None if repo is not None else _('Repository created but could not be opened.')
        GLib.idle_add(callback, error, repo)
        return False

    def _setup_monitors(self):
        git_dir = os.path.join(self.root, '.git')
        for name in ('index', 'HEAD'):
            git_file = Gio.File.new_for_path(os.path.join(git_dir, name))
            try:
                monitor = git_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            monitor.connect('changed', self.on_git_file_changed)
            self._monitors.append(monitor)

    def on_git_file_changed(self, monitor, git_file, other_file, event_type):
        # .git 操作期间 index/HEAD 会连续变化，500ms 去抖合并为一次刷新。
        if self._monitor_debounce_id is not None:
            return
        self._monitor_debounce_id = GLib.timeout_add(500, self.on_monitor_debounce)

    def on_monitor_debounce(self):
        self._monitor_debounce_id = None
        self.refresh()
        return False

    def refresh(self):
        '''异步刷新分支/提交/改动状态。并发请求合并为一次。'''
        if self._state_inflight:
            self._state_dirty = True
            return
        self._state_inflight = True
        threading.Thread(target=self.refresh_thread, daemon=True).start()

    def refresh_thread(self):
        code, out = run_git(['status', '--porcelain=v1', '-b'], self.root)
        branch = upstream = None
        ahead = behind = 0
        files = []
        history = []
        remotes = []

        if code == 0:
            branch, upstream, ahead, behind, files = parse_status_porcelain(out)
            # 一次取回历史，最新一条同时充当「最近提交」摘要（原来单独跑 log -1）。
            _, log_out = run_git(
                ['log', '-n', str(HISTORY_LIMIT),
                 '--pretty=format:%h%x1f%s%x1f%an%x1f%ct'], self.root)
            history = parse_log_history(log_out)
            # 没有远程仓库的项目（纯本地版本管理）同样要能用：push 需据此禁用。
            _, remote_out = run_git(['remote'], self.root)
            remotes = [line.strip() for line in remote_out.splitlines() if line.strip()]

        latest = history[0] if history else None
        state = {
            'branch': branch,
            'upstream': upstream,
            'ahead': ahead,
            'behind': behind,
            'commit_hash': latest['hash'] if latest else None,
            'commit_subject': latest['subject'] if latest else None,
            'commit_timestamp': latest['timestamp'] if latest else 0,
            'history': history,
            'remotes': remotes,
            'changed_files': files,
            'dirty': bool(files),
        }
        GLib.idle_add(self.refresh_done, state)

    def refresh_done(self, state):
        self._state_inflight = False
        self.state = state
        # HEAD 可能已变化，旧 diff 全部失效；消费者收到 state_changed 后重新请求。
        self._diffs.clear()
        self.add_change_code('state_changed')
        if self._state_dirty:
            self._state_dirty = False
            self.refresh()
        return False

    def request_file_diff(self, filename):
        '''异步计算 filename 相对 HEAD 的 diff，完成后 emit
        'diff_changed'(repo, filename)。已缓存/进行中的请求直接跳过。'''
        if filename is None or not os.path.isabs(filename):
            return
        if filename in self._diffs or filename in self._diff_inflight:
            return
        self._diff_inflight.add(filename)
        threading.Thread(target=self.diff_thread, args=(filename,), daemon=True).start()

    def diff_thread(self, filename):
        code, out = run_git(['diff', 'HEAD', '-U0', '--', filename], self.root)
        marks = parse_num_diff(out) if code == 0 else None
        GLib.idle_add(self.diff_done, filename, marks)

    def diff_done(self, filename, marks):
        self._diff_inflight.discard(filename)
        self._diffs[filename] = marks
        self.add_change_code('diff_changed', filename)
        return False

    def get_file_diff(self, filename):
        if filename is None:
            return None
        return self._diffs.get(filename)

    def is_file_untracked(self, filename):
        state = self.state
        if state is None or filename is None:
            return False
        for entry in state['changed_files']:
            if not entry['untracked']:
                continue
            if os.path.realpath(os.path.join(self.root, entry['path'])) == filename:
                return True
        return False

    def pull(self, callback):
        '''git pull --ff-only。callback(error_message 或 None) 在主线程回调。'''
        threading.Thread(target=self.pull_thread, args=(callback,), daemon=True).start()

    def pull_thread(self, callback):
        code, out = run_git(['pull', '--ff-only'], self.root, timeout=120)
        error = None
        if code != 0:
            error = classify_git_error(out, 'pull') or out.strip() or _('git pull failed.')
        GLib.idle_add(callback, error)
        return False

    def commit(self, files, message, callback):
        '''add 指定文件 → commit。不推送：很多项目纯本地历史，推送是单独动作。

        files 为 porcelain 输出的仓库相对路径。callback(error_message 或 None)
        在主线程回调。'''
        threading.Thread(target=self.commit_thread, args=(files, message, callback), daemon=True).start()

    def commit_thread(self, files, message, callback):
        error = None
        if files:
            code, out = run_git(['add', '--'] + list(files), self.root)
            if code != 0:
                error = (classify_git_error(out, 'add') or out.strip()
                         or _('Staging files failed.'))
        if error is None:
            code, out = run_git(['commit', '-m', message], self.root)
            if code != 0:
                error = (classify_git_error(out, 'commit') or out.strip()
                         or _('Commit failed.'))
        GLib.idle_add(callback, error)
        return False

    def push(self, callback):
        '''git push（无 upstream 时自动 --set-upstream origin <branch>）。'''
        threading.Thread(target=self.push_thread, args=(callback,), daemon=True).start()

    def push_thread(self, callback):
        error = None
        state = self.state
        branch = state['branch'] if state else None
        remotes = state['remotes'] if state else []
        args = ['push']
        if state is not None and state['upstream'] is None and branch and remotes:
            # 首次推送建立 upstream。远程不一定叫 origin，优先用它、否则取第一个。
            remote = 'origin' if 'origin' in remotes else remotes[0]
            args = ['push', '--set-upstream', remote, branch]
        code, out = run_git(args, self.root, timeout=120)
        if code != 0:
            error = (classify_git_error(out, 'push') or out.strip()
                     or _('Push failed.'))
        GLib.idle_add(callback, error)
        return False

    def file_versions(self, path, callback):
        '''单个文件的「HEAD 版本 / 工作区版本」全文，供并排（VSCode 式）diff。
        callback(error_message 或 None, old_text, new_text) 在主线程回调。

        旧版走 `git show HEAD:<path>`（文件不在 HEAD 里——untracked / 刚
        staged 的新文件——旧版为空串）；新版直接读工作区，文件已删除（D）
        时新版为空串。二进制（含 NUL）不尝试展示，报错让调用方提示。'''
        threading.Thread(target=self._file_versions_thread, args=(path, callback),
                         daemon=True).start()

    def _file_versions_thread(self, path, callback):
        code, old_text = run_git(['show', 'HEAD:' + path], self.root, timeout=60)
        if code != 0:
            old_text = ''
        absolute = os.path.join(self.root, path)
        new_text = ''
        if os.path.isfile(absolute):
            try:
                with open(absolute, 'rb') as f:
                    new_text = f.read().decode('utf-8', errors='replace')
            except OSError:
                new_text = ''
        if '\x00' in old_text or '\x00' in new_text:
            error = _('Binary files cannot be displayed as text.')
        else:
            error = None
        GLib.idle_add(callback, error, old_text, new_text)
        return False

    def discard(self, path, callback):
        '''丢弃单个文件的工作区改动。callback(error_message 或 None) 主线程回调。

        tracked 文件 → `git checkout -- <path>`（恢复到 index/HEAD 版本）；
        untracked 文件 → `git clean -f -- <path>`（直接删除，.aux/.log 等生成物
        是主要场景）。调用方必须先弹确认框并过信任门。'''
        threading.Thread(target=self.discard_thread, args=(path, callback),
                         daemon=True).start()

    def discard_thread(self, path, callback):
        untracked = False
        state = self.state
        if state is not None:
            for entry in state['changed_files']:
                if entry['path'] == path:
                    untracked = entry['untracked']
                    break

        if untracked:
            args = ['clean', '-f', '--', path]
            operation = 'discard-untracked'
        else:
            args = ['checkout', '--', path]
            operation = 'discard'
        code, out = run_git(args, self.root, timeout=60)
        if code != 0:
            error = classify_git_error(out, operation) or out.strip() or _('Discard failed.')
        else:
            error = None
        GLib.idle_add(callback, error)
        return False


def classify_git_error(output, operation):
    '''把常见 git 失败翻译成用户可操作的提示；无法识别返回 None（调用方
    回退到显示原始输出）。凭据问题绝不落盘，提示用户在终端配置
    credential helper。'''
    if not output:
        return None
    text = output.strip()
    lowered = text.lower()
    if operation == 'pull':
        if 'not possible to fast-forward' in lowered or 'divergent' in lowered:
            return _('Local and remote histories diverged. NeoSetzer only '
                     'supports fast-forward pulls — please resolve this in a '
                     'terminal (git pull / rebase).')
        if 'your local changes' in lowered or 'would be overwritten' in lowered:
            return _('Working tree has uncommitted changes. Commit them first '
                     'or use a terminal to stash.')
        if 'cannot lock ref' in lowered or 'unable to access' in lowered:
            return _('Could not reach the remote repository. Check your '
                     'network connection.')
    if operation == 'push':
        # 先判「没有远程」：缺远程时 git 也会报 "Could not read from remote
        # repository"，若先判凭据会把用户引向错误的排查方向。
        if ('no configured push destination' in lowered
                or 'does not appear to be a git repository' in lowered
                or 'no such remote' in lowered):
            return _('No remote repository is set up for this project. Add one '
                     'in a terminal: git remote add origin <url>')
        if 'authentication' in lowered or 'could not read from remote' in lowered or 'terminal prompts disabled' in lowered:
            return _('Push failed: no stored credentials for this remote. '
                     'Please configure a credential helper in a terminal '
                     '(git config credential.helper). NeoSetzer never stores '
                     'tokens or passwords.')
        if 'rejected' in lowered and 'fetch first' in lowered:
            return _('Push rejected: the remote has new commits. Pull first.')
    if operation == 'commit':
        if 'please tell me who you are' in lowered:
            return _('Git user name and email are not configured. Set them '
                     'with: git config --global user.name / user.email')
        if 'nothing to commit' in lowered:
            return _('Nothing to commit: no files were selected.')
    return None

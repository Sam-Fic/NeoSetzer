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

'''PhiTeX 实时预览的影子 SyncTeX：呈现与定位分给两个引擎。

phitex 的增量构建（实测 6–14 ms）继续负责 PDF 呈现，但它产出的 synctex
行号粗（align 多行盒记到上方正文行、公式自身行零记录，见
synctex_line_repair 与 pdf_annotation_mapper 的兜底链）。本模块在每次
phitex 构建成功后，于独立影子目录用原生引擎（暖 aux 单遍实测约 2.5 s）
重新生成精确 synctex，校验布局一致后原子替换 synctex_folder 缓存里的
活动 gz——消费端（正/反向跳转、选区映射）统一从该目录读 gz，零改动。

安全边界：
- 影子目录是 synctex_folder/shadow/，绝不写入 phitex 的输出目录；
- 安装前比对页数与逐页尺寸（私有 Poppler 实例），不一致即放弃本轮，
  活动 gz 保持 phitex 产物，现有兜底修复链原样生效；
- 安装前重算源码指纹，与构建时不一致（跑的过程中用户又改了文件）则
  丢弃结果并立即重新调度，绝不把陈旧定位数据装进缓存；
- 引擎不可用、编译失败、校验失败都静默降级，绝不影响主构建链。

sidecar（synctex_source.json）记录活动 gz 的来源与指纹：
- source='shadow'：门控按指纹比对（见 build_system.synctex_is_fresh），
  且 phitex 行号修复链停用（数据已是原生引擎的精确记录）；
- source='phitex'：完全沿用现状（can_sync 只看 has_synctex_file）。
'''

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback

from gi.repository import GLib

from setzer.app.service_locator import ServiceLocator
from setzer.document.build_system.builder.builder_build import build_env, popen_no_window
import setzer.document.build_system.phitex_session.phitex_session as phitex_session
from setzer.document.magic_comments import parse_magic_comments

# 构建成功到影子编译的防抖窗口：phitex live 每 150 ms 一轮，逐轮跟着跑
# 原生引擎等于把增量优势全吃掉；合并连续脏标记，停顿一秒后才跑一轮。
DEBOUNCE_MS = 1000
# 单遍引擎超时与收敛上限（aux 内容不动即收敛；交叉引用多的文档两三遍）。
PASS_TIMEOUT_S = 120
MAX_PASSES = 3
# 指纹递归收集的文件数上限（防畸形项目把整棵 TeX 树拉进来）。
MAX_FINGERPRINT_FILES = 100

SIDECAR_NAME = 'synctex_source.json'
BACKUP_NAME = 'phitex.synctex.gz'
SHADOW_DIRNAME = 'shadow'

_COMMENT_RE = re.compile(r'(?<!\\)%.*$')
_INCLUDE_RE = re.compile(r'\\(?:input|include)\s*\{([^{}]+)\}')
_BIB_RE = re.compile(r'\\bibliography\s*\{([^{}]+)\}')
_FONTSPEC_RE = re.compile(r'\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bfontspec\b[^}]*\}')
# `phitex config engine` 输出形如 "  engine  xelatex (yours)"；只有显式
# 设置（yours）才算用户意志，(default) 走后续判定链。
_CONFIG_ENGINE_RE = re.compile(r'^\s*engine\s+([A-Za-z]+)\s+\(yours\)', re.MULTILINE)
# session.log 头部形如 "PhiTeX 1.0.2  main.tex → main.pdf  pdflatex · 1 thread"，
# 引擎是 · 前最后一个词。日志跨 session 追加，取最后一次匹配。
_LOG_ENGINE_RE = re.compile(r'^PhiTeX\s+\S+[^\n]*→[^\n]*\s([A-Za-z]+)\s*·', re.MULTILINE)
# phitex 的引擎列表含 latex/pdftex/tex（DVI 路线）；影子编译只跑直出 PDF
# 的引擎，DVI 路线映射到 pdflatex，布局校验兜住可能的差异。
_ENGINE_ALIASES = {'pdflatex': 'pdflatex', 'xelatex': 'xelatex',
                   'lualatex': 'lualatex', 'latex': 'pdflatex',
                   'pdftex': 'pdflatex', 'tex': 'pdflatex'}


def setting_enabled():
    '''偏好 phitex_shadow_synctex（默认开）：是否启用影子 synctex。'''
    try:
        settings = ServiceLocator.get_settings()
        if settings is None:
            return True
        return bool(settings.get_value('preferences', 'phitex_shadow_synctex'))
    except Exception:
        return True


# —— 路径与 sidecar ——

def shadow_directory(folder):
    return os.path.join(folder, SHADOW_DIRNAME)


def active_gz_path(folder, tex_filename):
    '''活动 gz：消费端（synctex_query 等）实际读取的位置。'''
    stem = os.path.splitext(os.path.basename(tex_filename))[0]
    return os.path.join(folder, stem + '.synctex.gz')


def backup_gz_path(folder):
    return os.path.join(folder, BACKUP_NAME)


def read_sidecar(folder):
    try:
        with open(os.path.join(folder, SIDECAR_NAME), encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_sidecar(folder, source, fingerprint=None, engine=None):
    payload = {'source': source, 'fingerprint': fingerprint,
               'engine': engine, 'time': time.time()}
    path = os.path.join(folder, SIDECAR_NAME)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def mark_phitex_source(folder, tex_filename):
    '''非影子 gz 装入活动位置后调用（phitex 构建复制 / 原生引擎构建复制）：
    备份该 gz 供关开关时恢复，并把 sidecar 标为 phitex。

    每次成功复制都要调，与开关无关：否则上次会话留下的 shadow sidecar
    会把本次的 phitex gz 误标成精确数据，修复链被错误停用。
    '''
    active = active_gz_path(folder, tex_filename)
    try:
        if os.path.isfile(active):
            shutil.copyfile(active, backup_gz_path(folder))
    except OSError:
        pass
    write_sidecar(folder, 'phitex')


def restore_phitex(folder, tex_filename):
    '''关开关时把备份的 phitex gz 恢复为活动 gz。无备份不动（活动 gz
    可能仍是有效的影子产物，指纹门控继续兜住）。'''
    backup = backup_gz_path(folder)
    active = active_gz_path(folder, tex_filename)
    try:
        if os.path.isfile(backup):
            shutil.copyfile(backup, active)
            write_sidecar(folder, 'phitex')
            return True
    except OSError:
        pass
    return False


# —— 源码指纹 ——

def _resolve_include(directory, name):
    name = name.strip()
    if not os.path.splitext(name)[1]:
        name += '.tex'
    return os.path.normpath(os.path.join(directory, name))


def _resolve_bib(directory, name):
    name = name.strip()
    if not os.path.splitext(name)[1]:
        name += '.bib'
    return os.path.normpath(os.path.join(directory, name))


def source_fingerprint(tex_filename):
    '''主 tex + 递归 \\input/\\include + \\bibliography 引用的 .bib 的内容
    指纹（sha256）。缺失文件跳过，递归上限 MAX_FINGERPRINT_FILES。
    主文件读不到返回 None。按路径排序后哈希，与发现顺序无关。'''
    try:
        with open(tex_filename, 'rb') as f:
            f.read(1)
    except OSError:
        return None
    visited = set()
    queue = [os.path.abspath(tex_filename)]
    contents = {}
    while queue and len(visited) < MAX_FINGERPRINT_FILES:
        path = queue.pop(0)
        real = os.path.realpath(path)
        if real in visited:
            continue
        visited.add(real)
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            continue
        contents[real] = data
        directory = os.path.dirname(path)
        for line in data.decode('utf-8', errors='replace').splitlines():
            line = _COMMENT_RE.sub('', line)
            for match in _INCLUDE_RE.finditer(line):
                queue.append(_resolve_include(directory, match.group(1)))
            for match in _BIB_RE.finditer(line):
                for name in match.group(1).split(','):
                    if name.strip():
                        queue.append(_resolve_bib(directory, name))
    digest = hashlib.sha256()
    for real in sorted(contents):
        digest.update(real.encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(contents[real]).digest())
    return digest.hexdigest()


# —— 引擎判定 ——

def _engine_from_session_log(tex_filename, output_directory):
    candidates = [os.path.join(os.path.dirname(tex_filename), '.phitex', 'session.log')]
    if output_directory:
        candidates.append(os.path.join(output_directory, '.phitex', 'session.log'))
    for path in candidates:
        try:
            with open(path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 65536))
                text = f.read().decode('utf-8', errors='replace')
        except OSError:
            continue
        matches = _LOG_ENGINE_RE.findall(text)
        if matches:
            engine = _ENGINE_ALIASES.get(matches[-1].lower())
            if engine:
                return engine
    return None


def detect_engine(tex_filename, output_directory=None, tex_text=None):
    '''影子编译用哪个原生引擎。优先级：phitex config 显式设置 >
    session.log 头部（最近一次实际构建）> % !TEX program 注释 >
    fontspec 探测 > pdflatex。'''
    returncode, stdout = phitex_session.PhitexSession.run('config', 'engine')
    if returncode == 0 and stdout:
        match = _CONFIG_ENGINE_RE.search(stdout)
        if match:
            engine = _ENGINE_ALIASES.get(match.group(1).lower())
            if engine:
                return engine
    engine = _engine_from_session_log(tex_filename, output_directory)
    if engine:
        return engine
    if tex_text is None:
        try:
            with open(tex_filename, encoding='utf-8', errors='replace') as f:
                tex_text = f.read()
        except OSError:
            tex_text = ''
    magic = parse_magic_comments(tex_text)
    if magic.program in ('xelatex', 'pdflatex', 'lualatex'):
        return magic.program
    if _FONTSPEC_RE.search(tex_text):
        return 'xelatex'
    return 'pdflatex'


# —— 布局校验 ——

def _pdf_page_sizes(path):
    '''私有 Poppler 实例读逐页尺寸（不碰预览正在用的 document）。
    加载失败/解析异常返回 None。'''
    try:
        import gi
        gi.require_version('Poppler', '0.18')
        from gi.repository import Poppler
        document = Poppler.Document.new_from_file(
            GLib.filename_to_uri(os.path.abspath(path), None), None)
        if document is None:
            return None
        return [document.get_page(index).get_size()
                for index in range(document.get_n_pages())]
    except Exception:
        return None


def layout_matches(shadow_pdf, presented_pdf):
    '''影子 PDF 与 phitex 呈现 PDF 的页数与逐页尺寸一致才允许安装。
    任何异常按不一致处理（宁可降级也不装可能错位的定位数据）。'''
    shadow = _pdf_page_sizes(shadow_pdf)
    presented = _pdf_page_sizes(presented_pdf)
    if shadow is None or presented is None or len(shadow) != len(presented):
        return False
    return all(abs(sw - pw) <= 0.5 and abs(sh - ph) <= 0.5
               for (sw, sh), (pw, ph) in zip(shadow, presented))


def _file_digest(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


class ShadowSynctexRunner:
    '''一个文档对应一个影子运行器。notify_dirty 由构建 worker 线程调用，
    防抖定时器挂在默认主上下文，编译在独立守护线程里跑（阻塞式
    subprocess，与 phitex_session 同风格）。stop 后永久停用，调用方
    丢弃引用、按需重建。'''

    def __init__(self, tex_filename, output_directory, folder, on_installed=None):
        self.tex_filename = tex_filename
        self.output_directory = output_directory or os.path.dirname(tex_filename)
        self.folder = folder
        self.on_installed = on_installed
        self.stopped = False
        self._lock = threading.Lock()
        self._dirty = False
        self._running = False
        self._timer_id = None
        self._generation = 0
        self._process = None

    def notify_dirty(self):
        with self._lock:
            if self.stopped:
                return
            self._dirty = True
            if self._running or self._timer_id is not None:
                return
            self._timer_id = GLib.timeout_add(DEBOUNCE_MS, self._on_timer)

    def _on_timer(self):
        with self._lock:
            self._timer_id = None
            if self.stopped or not self._dirty or self._running:
                return False
            self._dirty = False
            self._running = True
            self._generation += 1
            generation = self._generation
        threading.Thread(target=self._work, args=(generation,), daemon=True).start()
        return False

    def _aborted(self, generation):
        with self._lock:
            return self.stopped or generation != self._generation

    def _work(self, generation):
        try:
            self._build_and_install(generation)
        except Exception:
            # 影子旁路绝不影响主构建链：异常只记录，本轮放弃。
            print('Setzer: shadow synctex worker raised ({}):'.format(
                self.tex_filename))
            traceback.print_exc()
        finally:
            with self._lock:
                self._running = False
                if self._dirty and not self.stopped and self._timer_id is None:
                    self._timer_id = GLib.timeout_add(DEBOUNCE_MS, self._on_timer)

    def _build_and_install(self, generation):
        fingerprint = source_fingerprint(self.tex_filename)
        if fingerprint is None:
            return
        engine = detect_engine(self.tex_filename, self.output_directory)
        if shutil.which(engine) is None:
            return
        shadow = shadow_directory(self.folder)
        try:
            os.makedirs(shadow, exist_ok=True)
        except OSError:
            return
        self._seed_helper_files(shadow)
        stem = os.path.splitext(os.path.basename(self.tex_filename))[0]
        shadow_gz = os.path.join(shadow, stem + '.synctex.gz')
        shadow_pdf = os.path.join(shadow, stem + '.pdf')
        aux_path = os.path.join(shadow, stem + '.aux')
        # aux 内容 hash 比对判收敛：首遍建 aux、次遍确认不动。跑满
        # MAX_PASSES 仍未收敛也继续——布局校验才是真正的安装门槛。
        for _ in range(MAX_PASSES):
            if self._aborted(generation):
                return
            aux_before = _file_digest(aux_path)
            if self._run_pass(engine, shadow) != 0:
                return
            if not os.path.isfile(shadow_gz) or not os.path.isfile(shadow_pdf):
                return
            if _file_digest(aux_path) == aux_before:
                break
        if self._aborted(generation):
            return
        presented_pdf = os.path.join(self.output_directory, stem + '.pdf')
        if not layout_matches(shadow_pdf, presented_pdf):
            return
        if self._aborted(generation):
            return
        if source_fingerprint(self.tex_filename) != fingerprint:
            # 跑的过程中源码又变了：丢弃本轮，立即重新调度。
            with self._lock:
                self._dirty = True
            return
        self._install(shadow_gz, fingerprint, engine)

    def _seed_helper_files(self, shadow):
        '''复用 phitex 编排好的 BibTeX/makeindex 产物，不重跑 bibtex/biber。'''
        stem = os.path.splitext(os.path.basename(self.tex_filename))[0]
        for ending in ('.bbl', '.ind', '.toc'):
            source = os.path.join(self.output_directory, stem + ending)
            if os.path.isfile(source):
                try:
                    shutil.copyfile(source, os.path.join(shadow, stem + ending))
                except OSError:
                    pass

    def _run_pass(self, engine, shadow):
        command = [engine, '-interaction=nonstopmode', '-synctex=1',
                   '-output-directory=' + shadow,
                   os.path.basename(self.tex_filename)]
        try:
            process = popen_no_window(
                command, cwd=os.path.dirname(self.tex_filename) or None,
                env=build_env(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        except (FileNotFoundError, OSError):
            return 1
        with self._lock:
            if self.stopped:
                try:
                    process.kill()
                except OSError:
                    pass
                return 1
            self._process = process
        try:
            return process.wait(timeout=PASS_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return 1
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

    def _install(self, shadow_gz, fingerprint, engine):
        active = active_gz_path(self.folder, self.tex_filename)
        try:
            os.makedirs(self.folder, exist_ok=True)
            sidecar = read_sidecar(self.folder)
            if (sidecar is None or sidecar.get('source') != 'shadow') \
                    and os.path.isfile(active) \
                    and not os.path.isfile(backup_gz_path(self.folder)):
                shutil.copyfile(active, backup_gz_path(self.folder))
            tmp = active + '.tmp'
            shutil.copyfile(shadow_gz, tmp)
            os.replace(tmp, active)
        except OSError:
            return
        write_sidecar(self.folder, 'shadow', fingerprint, engine)
        if self.on_installed is not None:
            GLib.idle_add(self.on_installed)

    def stop(self):
        '''终止在途进程、取消定时器、置 stopped。在途 worker 靠代数
        计数自行放弃结果，不会把陈旧 gz 装进缓存。'''
        with self._lock:
            if self.stopped:
                return
            self.stopped = True
            self._generation += 1
            timer_id = self._timer_id
            self._timer_id = None
            process = self._process
        if timer_id is not None:
            try:
                GLib.Source.remove(timer_id)
            except (ValueError, RuntimeError):
                pass
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
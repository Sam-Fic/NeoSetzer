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

'''片段预览的 root 文档解析（纯逻辑，无 gi 依赖，headless 可测）。

片段单独编译时需要 root 文件的 preamble（章节文件里没有
``\\begin{document}``，也没有 tikz 宏包），以及 root 所在目录作为子进程
工作目录（片段里的 ``\\includegraphics{figs/x.png}`` 相对 root 解析）。

解析顺序（先便宜后昂贵）：
1. 本文含 ``\\begin{document}`` ⇒ 自足，零磁盘 IO；
2. ``%!TeX root`` magic comment（resolve_root_filename 已校验存在性）；
3. 项目配置 ``.neosetzer/build.json`` 的 root_document；
4. 都失败 ⇒ text 为空，调用方回落最小 preamble。

刻意不复用 workspace.get_magic_root_or_active_latex_document()：那条路径
会**打开**引用的 root 文件（workspace.py 的 _open_build_root_if_needed），
而预览只是读一下文本，不该改变用户的工作区状态。

已打开的文档文本经 open_sources 注入（未保存的 root 编辑优先于磁盘）；
磁盘回落到主线程读一次，带 (mtime_ns, size) 校验的少量缓存，稳态下每次
编辑只付一次 os.stat。
'''

import os
from collections import OrderedDict
from typing import NamedTuple, Optional

from setzer.document.magic_comments import parse_magic_comments, resolve_root_filename
from setzer.project.build_configuration import ProjectBuildConfiguration

_BEGIN_DOCUMENT = '\\begin{document}'

_READ_CACHE_MAX = 8

# filename -> (mtime_ns, size, text)；仅主线程访问，无需加锁。
_READ_CACHE = OrderedDict()


class RootContext(NamedTuple):
    filename: Optional[str]    # 解析到的 root 文件绝对路径；None = 无法解析
    text: str                  # root 文件全文；'' = 不可用（回落最小 preamble）
    build_dir: Optional[str]   # 编译子进程工作目录；None = 用引擎的临时目录
    signature: tuple           # (filename, mtime_ns, size)，用于失败冷却等的比对


def clear_read_cache():
    '''测试用：清空磁盘读取缓存。'''
    _READ_CACHE.clear()


def _read_from_disk(filename):
    '''读文件全文，带 (mtime_ns, size) 校验缓存；失败返回 None。'''
    try:
        stat = os.stat(filename)
        cached = _READ_CACHE.get(filename)
        if (cached is not None
                and cached[0] == stat.st_mtime_ns
                and cached[1] == stat.st_size):
            _READ_CACHE.move_to_end(filename)
            return cached[2]
        with open(filename, encoding='utf-8') as file_object:
            text = file_object.read()
    except (OSError, UnicodeDecodeError):
        return None
    _READ_CACHE[filename] = (stat.st_mtime_ns, stat.st_size, text)
    _READ_CACHE.move_to_end(filename)
    while len(_READ_CACHE) > _READ_CACHE_MAX:
        _READ_CACHE.popitem(last=False)
    return text


def _project_root_filename(document_filename):
    '''项目配置里的 root_document（绝对路径）；无配置或无效返回 None。'''
    configuration = ProjectBuildConfiguration.discover(document_filename)
    if configuration is None:
        return None
    candidate = configuration.effective_path(
        configuration.load().get('root_document'))
    if candidate and os.path.isfile(candidate):
        return candidate
    return None


def resolve_root_context(document_filename, document_text,
                         open_sources=None, read_text=None):
    '''按上面的顺序解析出 RootContext。'''
    open_sources = open_sources or {}
    read = read_text or _read_from_disk

    # 1) 自足文档：自身就是可编译单元，连 stat 都不做。
    if document_text and _BEGIN_DOCUMENT in document_text:
        build_dir = None
        if document_filename:
            build_dir = os.path.dirname(os.path.abspath(document_filename))
        signature = (document_filename, None, len(document_text))
        return RootContext(document_filename, document_text, build_dir, signature)

    if not document_filename:
        return RootContext(None, '', None, (None, None, 0))

    # 2) %!TeX root（resolve_root_filename 只接受存在且非绝对路径的 .tex）
    root_filename = None
    magic = parse_magic_comments(document_text or '')
    if magic.root:
        root_filename = resolve_root_filename(document_filename, magic.root)

    # 3) 项目配置
    if root_filename is None:
        root_filename = _project_root_filename(document_filename)

    if root_filename is None:
        return RootContext(None, '', None, (None, None, 0))

    if root_filename in open_sources:
        # 未保存的编辑优先：工作区里打开的 root 可能与磁盘不一致。
        text = open_sources[root_filename]
        signature = (root_filename, None, len(text))
    else:
        text = read(root_filename)
        if text is None:
            return RootContext(None, '', None, (None, None, 0))
        try:
            stat = os.stat(root_filename)
            signature = (root_filename, stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = (root_filename, None, len(text))

    return RootContext(root_filename, text, os.path.dirname(root_filename), signature)

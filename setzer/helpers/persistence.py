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

'''持久化辅助：JSON 读写 + 一次性 pickle 迁移 + 受限反序列化。

刻意不 import gi，使其可在无 GTK 的 unittest 环境下直接测试。

设计要点：
- JSON 是首选持久化格式（安全、可读、跨 Python 版本兼容）。
- pickle 仅用于读取旧文件的一次性迁移：用户自己的配置文件可信，用
  ``load_pickle_trusted``；用户交换的 .stzs 文件不可信，用
  ``load_pickle_restricted``（仅允许 builtins 容器类型，阻断 RCE）。
- 写入用原子替换（先写 .tmp 再 os.replace），避免崩溃留下半写文件。
'''

import json
import os
import pickle
import tempfile


JSON_ENSURE_ASCII = False  # 直接 UTF-8，便于 .stzs/diff 可读
JSON_INDENT = None         # 紧凑写，文件已不大；如需人读可改 2


def _fsync_directory(directory):
    '''Best-effort flush of a directory entry after ``os.replace``.

    POSIX requires syncing the containing directory for an atomic rename to be
    durable across a sudden power loss.  Directory descriptors are unavailable
    or unsupported on some platforms, where the file-level fsync remains the
    strongest portable guarantee.
    '''
    directory_flag = getattr(os, 'O_DIRECTORY', 0)
    try:
        descriptor = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path, data):
    '''Durably replace ``path`` with bytes using a unique sibling temp file.'''
    directory = os.path.dirname(os.path.abspath(path))
    basename = os.path.basename(path)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f'.{basename}.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _fsync_directory(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_text(path, text, encoding='utf-8'):
    '''Durably replace ``path`` with encoded text using ``atomic_write_bytes``.'''
    atomic_write_bytes(path, text.encode(encoding))


# .stzs 是用户交换文件，可能含恶意 pickle payload。受限 Unpickler
# 只允许 builtins 中的不可变/容器类型。合法 .stzs 数据结构仅含
# dict/list/str/int/float/bool/None/tuple，这些类型 pickle 用原生
# opcode 反序列化，根本不会调 find_class；find_class 仅在 pickle
# 流中含 GLOBAL/STACK_GLOBAL 指令（即引用某 class）时触发，此时
# 非 builtins 一律拒绝，阻断 RCE。
_SAFE_BUILTINS = frozenset({
    'dict', 'list', 'str', 'int', 'float', 'bool', 'tuple',
    'NoneType', 'set', 'frozenset',
})


class RestrictedUnpickler(pickle.Unpickler):
    '''仅允许 builtins 容器类型的 Unpickler，用于读不可信 pickle 文件。'''

    def find_class(self, module, name):
        if module == 'builtins' and name in _SAFE_BUILTINS:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(
            'unsafe global: {}.{}'.format(module, name))


def load_pickle_restricted(path):
    '''读取可能不可信的 pickle 文件，仅允许 builtins 容器类型。

    用于读取用户交换的 .stzs 文件：旧版 Setzer 用 pickle 序列化，
    新版优先 JSON，回退到此函数读取旧 .stzs。
    '''
    with open(path, 'rb') as f:
        return RestrictedUnpickler(f).load()


def load_pickle_trusted(path):
    '''读取可信 pickle 文件（用户自己的 settings/workspace 旧文件）。

    用于一次性迁移：用户自己的配置文件不视为攻击面，但仍建议尽快
    迁移到 JSON 以解除 Python 版本耦合。
    '''
    with open(path, 'rb') as f:
        return pickle.load(f)


def load_json(path, fallback=None):
    '''读取 JSON 文件。文件不存在或解析失败时返回 fallback。

    注意：pickle 字节流不是合法 UTF-8，会抛 UnicodeDecodeError
    （是 ValueError 的子类），需显式捕获以让 try_migrate_session_file_pickle
    顺利回退到 pickle 路径。
    '''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return fallback


def save_json(path, data, *, indent=JSON_INDENT):
    '''Durably save JSON using file and parent-directory fsyncs.

    JSON is serialized before any temporary file is created, so encoding errors
    cannot leave a stale replacement candidate beside the destination.
    '''
    serialized = json.dumps(data, ensure_ascii=JSON_ENSURE_ASCII, indent=indent)
    atomic_write_text(path, serialized)


def migrate_pickle_to_json(json_path, pickle_path, migrate_value=None):
    '''一次性迁移：若 JSON 不存在且 pickle 存在，读 pickle 写 JSON。

    migrate_value(data) 可选，用于在落盘前修正结构（如把嵌套 pickle
    bytes 解为 dict）。返回迁移后的 data，或 None 表示无需迁移
    （JSON 已存在、pickle 不存在、或迁移失败）。
    '''
    if os.path.exists(json_path) or not os.path.exists(pickle_path):
        return None
    try:
        data = load_pickle_trusted(pickle_path)
    except (EOFError, pickle.UnpicklingError, ValueError, AttributeError):
        return None
    if migrate_value is not None:
        data = migrate_value(data)
    try:
        save_json(json_path, data)
    except (TypeError, ValueError, OSError):
        return None
    return data


def try_migrate_session_file_pickle(path):
    '''读 .stzs：先试 JSON，失败回退到受限 pickle。

    返回 (data, was_pickle)：
    - (data, False)：JSON 成功读取
    - (data, True)：旧 pickle 文件，受限反序列化成功
    - (None, True/False)：读不到或解析失败
    '''
    data = load_json(path)
    if data is not None:
        return data, False
    # JSON 文件不存在时 load_json 返回 None；但若 JSON 文件存在但内容
    # 是 null/空，load_json 也会返回 None，此时仍尝试 pickle 以兼容。
    if not os.path.exists(path):
        return None, False
    try:
        return load_pickle_restricted(path), True
    except (pickle.UnpicklingError, EOFError, AttributeError, ValueError):
        return None, True
    except OSError:
        return None, True

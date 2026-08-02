#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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

'''文档状态持久化文件名计算（gi-free，可在无 GTK 环境下测试）。

设计要点：
- 新方案：``<sha256_16hex>__<sanitized_basename>.{json,pickle}``
  - hash 保证不同路径即使 basename 相同也不冲突
  - basename 让用户/调试者能从文件名识别对应文档
  - 固定长度避免长路径 base64 编码后超出文件系统名长上限
- 旧方案：``<base64-urlsafe(full_path)>.{json,pickle}``，仅用于一次性迁移
'''

import base64
import hashlib
import os.path


def state_paths(document_filename, config_folder):
    '''返回 (json_path, pickle_path) 用于文档状态持久化。

    见模块 docstring 了解命名方案。``config_folder`` 由调用方传入
    （生产代码从 ServiceLocator.get_config_folder() 取，测试中传 tempdir）。
    '''
    basename = os.path.basename(document_filename)
    safe_basename = ''.join(
        c if (c.isalnum() or c in '._-') else '_' for c in basename)
    hash_prefix = hashlib.sha256(
        document_filename.encode('utf-8')).hexdigest()[:16]
    stem = '{}__{}'.format(hash_prefix, safe_basename)
    return (
        os.path.join(config_folder, stem + '.json'),
        os.path.join(config_folder, stem + '.pickle'),
    )


def legacy_state_paths(document_filename, config_folder):
    '''旧版 base64 编码文件名（仅用于一次性迁移读取）。'''
    filename = base64.urlsafe_b64encode(
        str.encode(document_filename)).decode()
    return (
        os.path.join(config_folder, filename + '.json'),
        os.path.join(config_folder, filename + '.pickle'),
    )

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

'''公式 snippet 编译（纯逻辑，无 gi 依赖，runner 可注入 headless 测试）。

把包装好的完整 LaTeX 文档用用户配置的引擎单遍编译成单页 PDF。
刻意不走 latexmk：单公式没有引用/交叉编译需求，latexmk 的多遍逻辑与
perl 启动开销对 hover 场景完全不可接受。

子进程细节仿 builder_build_latex._spawn_process：Windows 用
CREATE_NO_WINDOW 防控制台闪现；超时强杀防引擎卡死拖垮 hover 体验。
stdout/stderr 直接丢弃——预览不展示编译日志，失败即静默收起弹窗。
'''

import os
import subprocess
import sys

# 单次公式编译超时（秒）。普通公式 <1s；重 preamble（大量宏包）可能数秒；
# tectonic 首次运行还要解包 bundle，统一放宽到 30s。
_COMPILE_TIMEOUT = 30.0


def build_compile_command(engine, tex_filename, out_dir):
    '''构造编译命令（参数列表形态，不经过 shell）。

    xelatex / pdflatex / lualatex 共用参数形态；tectonic 是单二进制
    工作流，用 --outdir。返回列表便于测试断言，也避免 shell 注入。
    '''
    if engine == 'tectonic':
        return ['tectonic', '--outdir', out_dir, tex_filename]
    return [
        engine,
        '-interaction=nonstopmode',
        '-halt-on-error',
        '-output-directory=' + out_dir,
        tex_filename,
    ]


def _run(cmd, cwd, timeout):
    kwargs = dict(
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )
    if sys.platform == 'win32':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)


def compile_snippet(wrapped_text, engine, work_dir, cache, key,
                    runner=None, timeout=_COMPILE_TIMEOUT):
    '''编译 wrapped_text，成功则把 PDF 移入缓存并返回其路径；失败返回 None。

    work_dir 为会话级临时目录（控制器持有，刻意不在文档目录内——避免
    触发文档目录的文件监视/重建逻辑）。runner 可注入用于测试，签名同
    _run(cmd, cwd, timeout)。
    '''
    run = runner or _run
    try:
        os.makedirs(work_dir, exist_ok=True)
        tex_path = os.path.join(work_dir, key + '.tex')
        with open(tex_path, 'w', encoding='utf-8') as f:
            f.write(wrapped_text)
        cmd = build_compile_command(engine, tex_path, work_dir)
        run(cmd, work_dir, timeout)
        pdf_tmp = os.path.join(work_dir, key + '.pdf')
        if not os.path.isfile(pdf_tmp):
            return None
        target = cache.store_pdf(pdf_tmp, key)
        try:
            os.remove(tex_path)
        except OSError:
            pass
        return target
    except (subprocess.TimeoutExpired, OSError):
        return None

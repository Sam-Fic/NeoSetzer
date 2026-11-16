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

'''`synctex view` 的源文件行 → PDF 矩形查询。

原先这段解析只长在 builder_forward_sync.run() 里。增量编译的改动高亮要做
同样的事（拿一批改动行换成页面上的矩形），正则与输出格式抄两份必然漂移：
它匹配的是 synctex CLI 的九行输出块，任何一方改了而另一方没跟上，表现都是
静默拿到空列表（高亮没了 / 正向同步不动），而不是报错。
'''

import os.path
import subprocess
import sys

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.decode_output import decode_process_output

# Output/Page/x/y/h/v/W/H/before/offset/middle/after 一整块。
# group 1=页(1-based) 2=h(x) 3=v(框底边距页顶) 4=W(宽) 5=H(高)，单位 PDF pt。
_VIEW_REGEX = None


def _regex():
    global _VIEW_REGEX
    if _VIEW_REGEX is None:
        _VIEW_REGEX = ServiceLocator.get_regex_object(
            r'\nOutput:.*\nPage:([0-9]+)\nx:.*\ny:.*\nh:((?:[0-9]|\.)+)\nv:((?:[0-9]|\.)+)\nW:((?:[0-9]|\.)+)\nH:((?:[0-9]|\.)+)\nbefore:.*\noffset:.*\nmiddle:.*\nafter:.*')
    return _VIEW_REGEX


def parse_view_output(raw):
    '''解析 `synctex view` 的输出为矩形列表（与 query_rectangles 同一格式）。
    '''
    rectangles = list()
    for match in _regex().finditer(raw or ''):
        rectangles.append({'page': int(match.group(1)),
                          'h': float(match.group(2)),
                          'v': float(match.group(3)),
                          'width': float(match.group(4)),
                          'height': float(match.group(5))})
    return rectangles


def query_rectangles(tex_filename, pdf_filename, line, synctex_directory, offset=0,
                     timeout=5):
    '''把 tex_filename 的第 line 行（1-based）换成 PDF 上的矩形列表。

    返回 [{'page': 1-based, 'h', 'v', 'width', 'height'}]，查不到时为空列表。
    synctex 缺件、超时无输出都按空列表处理：调用方全是"锦上添花"的用法
    （正向同步、改动高亮），不该因为定位不到就把构建或预览判成失败。
    '''
    arguments = ['synctex', 'view', '-i',
                 '{}:{}:{}'.format(line, offset, tex_filename),
                 '-o', pdf_filename, '-d', synctex_directory]
    try:
        kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if sys.platform == 'win32':
            # 与 builder_build.popen_no_window 同理：不弹控制台窗口。
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(arguments, **kwargs)
    except (FileNotFoundError, OSError):
        return []
    try:
        process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
    return parse_view_output(decode_process_output(process.communicate()[0]))


def synctex_directory_for(pdf_filename):
    '''PhiTeX/texlive 把 .synctex.gz 写在 PDF 旁边，直接用产物目录。'''
    return os.path.dirname(pdf_filename) or '.'

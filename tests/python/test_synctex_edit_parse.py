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

'''synctex edit 反查解析单测。

契约：line 1-based；Column/Offset 为 -1 时置 None（焦点子串映射据此降级）；
Input 的 `./` 段必须 normpath（与正向/反向跳转同一匹配约定）；缺件/超时/
无匹配一律 None，调用方自行降级。
'''

import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from setzer.helpers import synctex_query


def edit_output(filename='/p/Ex/main.tex', line=5, column=3, offset=10):
    return ('This is SyncTeX command line utility, version 1.21\n'
            'Output:pdf\n'
            'Input:{}\n'
            'Line:{}\n'
            'Column:{}\n'
            'Offset:{}\n'
            'Context:mid\n').format(filename, line, column, offset)


class ParseEditOutputTest(unittest.TestCase):

    def test_full_output(self):
        pos = synctex_query.parse_edit_output(edit_output())
        self.assertEqual(pos, {'filename': '/p/Ex/main.tex', 'line': 5,
                               'column': 3, 'offset': 10})

    def test_dot_segment_is_normalized(self):
        pos = synctex_query.parse_edit_output(edit_output('/p/Ex/./main.tex'))
        self.assertEqual(pos['filename'], '/p/Ex/main.tex')

    def test_minus_one_column_offset_become_none(self):
        pos = synctex_query.parse_edit_output(edit_output(column=-1, offset=-1))
        self.assertIsNone(pos['column'])
        self.assertIsNone(pos['offset'])
        self.assertEqual(pos['line'], 5)

    def test_zero_column_offset_are_kept(self):
        pos = synctex_query.parse_edit_output(edit_output(column=0, offset=0))
        self.assertEqual(pos['column'], 0)
        self.assertEqual(pos['offset'], 0)

    def test_no_match_returns_none(self):
        self.assertIsNone(synctex_query.parse_edit_output('SyncTeX warning: no match'))
        self.assertIsNone(synctex_query.parse_edit_output(''))
        self.assertIsNone(synctex_query.parse_edit_output(None))

    def test_empty_input_returns_none(self):
        self.assertIsNone(synctex_query.parse_edit_output(edit_output(filename='')))


class QuerySourcePositionTest(unittest.TestCase):

    def test_missing_synctex_binary_returns_none(self):
        with mock.patch.object(synctex_query.subprocess, 'Popen',
                               side_effect=FileNotFoundError):
            self.assertIsNone(synctex_query.query_source_position(
                '/p/out.pdf', 1, 100, 200, '/p'))

    def test_arguments_use_page_x_y_pdf_order(self):
        process = mock.Mock()
        process.communicate.return_value = (edit_output().encode('utf-8'), b'')
        with mock.patch.object(synctex_query.subprocess, 'Popen',
                               return_value=process) as popen:
            pos = synctex_query.query_source_position('/p/out.pdf', 2, 100, 200, '/cache')
        args = popen.call_args[0][0]
        self.assertEqual(args[:3], ['synctex', 'edit', '-o'])
        self.assertEqual(args[3], '2:100:200:/p/out.pdf')
        self.assertEqual(args[-2:], ['-d', '/cache'])
        self.assertEqual(pos['line'], 5)

    def test_timeout_kills_and_returns_none(self):
        import subprocess
        process = mock.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired('synctex', 5)
        process.communicate.return_value = (b'', b'')
        with mock.patch.object(synctex_query.subprocess, 'Popen',
                               return_value=process):
            self.assertIsNone(synctex_query.query_source_position(
                '/p/out.pdf', 1, 0, 0, '/p'))
        process.kill.assert_called_once()


class DirectoryResolutionTest(unittest.TestCase):

    def test_prefers_build_cache_when_gz_exists(self):
        with mock.patch.object(synctex_query.ServiceLocator, 'get_config_folder',
                               return_value='/cfg'), \
                mock.patch.object(synctex_query, 'synctex_folder',
                                  return_value='/cfg/synctex/abc__main'), \
                mock.patch.object(synctex_query.os.path, 'exists', return_value=True):
            directory = synctex_query.synctex_directory_for_source(
                '/p/main.tex', '/p/build/main.pdf')
        self.assertEqual(directory, '/cfg/synctex/abc__main')

    def test_falls_back_to_pdf_directory(self):
        with mock.patch.object(synctex_query.ServiceLocator, 'get_config_folder',
                               return_value='/cfg'), \
                mock.patch.object(synctex_query, 'synctex_folder',
                                  return_value='/cfg/synctex/abc__main'), \
                mock.patch.object(synctex_query.os.path, 'exists', return_value=False):
            directory = synctex_query.synctex_directory_for_source(
                '/p/main.tex', '/p/build/main.pdf')
        self.assertEqual(directory, '/p/build')


class RealSynctexOutputContractTest(unittest.TestCase):
    '''synctex CLI 实测输出契约（2026-11 本机 synctex 1.5，pdflatex 相对/
    绝对路径与 latexmk -output-directory 三种编译方式验证）：

    - Input 恒为绝对路径，可能带 `./` 段；
    - .synctex.gz 复制到独立缓存目录后经 -d 查询，Input 仍按编译时的
      路径解析为绝对路径（gz 头里记录了编译目录，与 gz 所在目录无关）；
    - -o 指向不存在的 PDF 也能靠 -d 的 <stem>.synctex.gz 命中。

    workspace.jump_to_synctex_position 对 Input 的「绝对路径假设」
    （isfile / same_file 比较）依赖本契约。'''

    REAL_SUBFILE_OUTPUT = (
        'This is SyncTeX command line utility, version 1.5\n'
        'SyncTeX result begin\n'
        'Output:main.pdf\n'
        'Input:/tmp/proj/./sub/intro.tex\n'
        'Line:2\n'
        'Column:-1\n'
        'Offset:0\n'
        'Context:\n'
        'SyncTeX result end\n')

    def test_real_output_block_parses(self):
        pos = synctex_query.parse_edit_output(self.REAL_SUBFILE_OUTPUT)
        self.assertEqual(pos, {'filename': '/tmp/proj/sub/intro.tex',
                               'line': 2, 'column': None, 'offset': 0})

    def test_parsed_input_matches_document_filename_after_normpath(self):
        # 反查同文件判定（jump_to_synctex_position）依赖：解析结果
        # normpath 后必须与文档记录的文件名字符串相等。
        pos = synctex_query.parse_edit_output(self.REAL_SUBFILE_OUTPUT)
        self.assertEqual(os.path.normpath(pos['filename']),
                         os.path.normpath('/tmp/proj/sub/intro.tex'))


if __name__ == '__main__':
    unittest.main()
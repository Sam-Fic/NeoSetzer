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

'''GitRepository 纯解析函数的单元测试（不执行任何 git 子进程）。'''

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from setzer.vcs.git_repository import (
    parse_status_porcelain,
    parse_num_diff,
    DIFF_CHANGED_LIMIT,
)


class TestParseStatusPorcelain:

    def test_branch_with_upstream_and_counts(self):
        output = (
            '## main...origin/main [ahead 2, behind 3]\n'
            ' M document.tex\n'
            '?? output.log\n'
        )
        branch, upstream, ahead, behind, files = parse_status_porcelain(output)
        assert branch == 'main'
        assert upstream == 'origin/main'
        assert ahead == 2
        assert behind == 3
        assert files == [
            {'status': ' M', 'path': 'document.tex', 'untracked': False},
            {'status': '??', 'path': 'output.log', 'untracked': True},
        ]

    def test_branch_without_upstream(self):
        branch, upstream, ahead, behind, files = parse_status_porcelain('## feature\n')
        assert branch == 'feature'
        assert upstream is None
        assert ahead == 0 and behind == 0
        assert files == []

    def test_no_commits_yet(self):
        branch, *_ = parse_status_porcelain('## No commits yet on main\n')
        assert branch == 'main'

    def test_rename_shows_new_path(self):
        _, _, _, _, files = parse_status_porcelain('R  old.tex -> new.tex\n')
        assert files[0]['path'] == 'new.tex'
        assert files[0]['untracked'] is False

    def test_empty_output(self):
        branch, upstream, ahead, behind, files = parse_status_porcelain('')
        assert branch is None
        assert upstream is None
        assert files == []


class TestParseNumDiff:

    def test_modified_lines(self):
        # 旧 5-7 行（3 行）替换为新 5-8 行（4 行）：全部算 modified（蓝条）
        output = '@@ -5,3 +5,4 @@\n context\n-old\n+new\n+added\n context\n'
        marks = parse_num_diff(output)
        assert marks['modified'] == {5, 6, 7, 8}
        assert marks['added'] == set()
        assert marks['deleted_after'] == set()
        assert not marks['degraded']

    def test_added_block(self):
        output = '@@ -0,0 +1,3 @@\n+line1\n+line2\n+line3\n'
        marks = parse_num_diff(output)
        assert marks['added'] == {1, 2, 3}
        assert marks['modified'] == set()

    def test_deleted_lines_marker_position(self):
        # 旧行 5-7 被删，新文件坐标下标记落在第 4 行之后（VS Code 惯例：
        # 三角画在删除点之后的第一行上）
        output = '@@ -5,3 +4,0 @@\n-old1\n-old2\n-old3\n'
        marks = parse_num_diff(output)
        assert marks['deleted_after'] == {4}
        assert marks['added'] == set() and marks['modified'] == set()

    def test_deletion_at_file_start(self):
        # +0,0：文件开头删行，标记应落在第 1 行
        output = '@@ -1,2 +0,0 @@\n-a\n-b\n'
        marks = parse_num_diff(output)
        assert marks['deleted_after'] == {1}

    def test_single_line_hunk_without_count(self):
        # hunk 头省略 count 表示 1 行：`@@ -3 +3 @@`
        output = '@@ -3 +3 @@\n-old\n+new\n'
        marks = parse_num_diff(output)
        assert marks['modified'] == {3}

    def test_degraded_on_huge_diff(self):
        lines = ['+x'] * (DIFF_CHANGED_LIMIT + 10)
        output = '@@ -1,{} +1,{} @@\n'.format(DIFF_CHANGED_LIMIT + 10, DIFF_CHANGED_LIMIT + 10)
        output += '\n'.join(lines) + '\n'
        marks = parse_num_diff(output)
        assert marks['degraded']

    def test_empty_output(self):
        marks = parse_num_diff('')
        assert marks['added'] == set()
        assert marks['modified'] == set()
        assert marks['deleted_after'] == set()
        assert marks['changed_count'] == 0

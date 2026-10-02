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

'''build_status_text 纯逻辑测试：成功判定、错误/警告组合、英文回退。

测试环境未注入 builtins._ / builtins.ngettext，走英文回退分支；
翻译本身由 po 校验保障，此处验证结构与单复数逻辑。'''

import unittest

from setzer.helpers.build_status_text import format_build_status


class TestFormatBuildStatus(unittest.TestCase):

    def test_zero_and_zero_is_success(self):
        self.assertEqual(format_build_status(0, 0), 'Build succeeded')

    def test_errors_only(self):
        self.assertEqual(format_build_status(1, 0), '1 error')
        self.assertEqual(format_build_status(2, 0), '2 errors')

    def test_warnings_only(self):
        self.assertEqual(format_build_status(0, 1), '1 warning')
        self.assertEqual(format_build_status(0, 3), '3 warnings')

    def test_errors_and_warnings(self):
        self.assertEqual(format_build_status(2, 3), '2 errors, 3 warnings')
        self.assertEqual(format_build_status(1, 1), '1 error, 1 warning')

    def test_negative_counts_treated_as_zero(self):
        self.assertEqual(format_build_status(-1, 0), 'Build succeeded')
        self.assertEqual(format_build_status(-1, -1), 'Build succeeded')


if __name__ == '__main__':
    unittest.main()

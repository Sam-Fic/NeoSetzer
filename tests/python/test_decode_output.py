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

# 单元测试：子进程输出解码的逐级容错
#
# 背景（Windows 用户实测崩溃）：synctex 等工具在中文 Windows 上出错时
# 输出 GBK 文本（含 0xD0 等字节），builder 硬按 UTF-8 解码抛
# UnicodeDecodeError 使后台线程崩溃。
#
# 覆盖：
# - UTF-8 字节正常解码（绝大多数场景）
# - GBK 字节回退到系统代码页解码（模拟中文 Windows）
# - 混乱字节最终回退 errors='replace'，永不抛异常
# - str 透传、空输入

import unittest
from unittest import mock

from setzer.helpers.decode_output import decode_process_output


class TestDecodeProcessOutput(unittest.TestCase):

    def test_utf8_normal_output(self):
        # synctex 正常输出是 ASCII 标签 + UTF-8 文本
        raw = b'\nOutput: pdf\nInput:\xe6\x96\x87\xe6\xa1\xa3.tex\nLine:12\n'
        self.assertEqual(
            decode_process_output(raw),
            '\nOutput: pdf\nInput:文档.tex\nLine:12\n')

    def test_str_passthrough(self):
        # 上游已是 str 时原样返回，不做二次编码假设
        self.assertEqual(decode_process_output('already decoded'), 'already decoded')

    def test_empty_bytes(self):
        self.assertEqual(decode_process_output(b''), '')

    def test_gbk_fallback(self):
        # 中文 Windows ANSI 代码页（cp936）的错误提示：0xD0 是典型 GBK 首字节
        # （'不' = b'\xb2\xbb'，'文件' = b'\xce\xc4\xbc\xfe'）。
        # 用 mock 把 preferred encoding 指到 cp936，模拟中文 Windows 环境
        # （Linux/UTF-8 容器里实际返回 UTF-8，GBK 分支不会触发）。
        gbk_bytes = b"'foo' " + '不是内部或外部命令'.encode('gbk') + b'\xd0\xa7\xb9\xfb'
        with mock.patch('setzer.helpers.decode_output.locale.getpreferredencoding',
                        return_value='cp936'):
            decoded = decode_process_output(gbk_bytes)
        # 结果必须是 str（不抛异常），且经 GBK 路径解码出正确汉字
        self.assertIsInstance(decoded, str)
        self.assertIn('不是内部或外部命令', decoded)
        self.assertIn('效果', decoded)

    def test_utf8_preferred_skips_second_try(self):
        # preferred encoding 为 UTF-8 时（Linux/UTF-8 模式），与第一层重复，
        # 应跳过第二层直接走 replace 兜底——GBK 字节不允许被误判成功
        gbk_bytes = '不是内部或外部命令'.encode('gbk')
        decoded = decode_process_output(gbk_bytes)
        self.assertIsInstance(decoded, str)
        self.assertNotIn('不是内部或外部命令', decoded)

    def test_undecodable_never_raises(self):
        # 两级解码都失败的混乱字节流：必须走 replace 兜底，永不抛
        hostile = b'\nOutput:\xff\xfe\x81\x8d\x00\x9d\nInput:x.tex\n'
        decoded = decode_process_output(hostile)
        self.assertIsInstance(decoded, str)
        self.assertIn('Input:x.tex', decoded)

    def test_utf8_label_survives_fallback(self):
        # 关键契约：正则关心的 ASCII 结构在兜底路径下依然完整可匹配
        raw = b'\nOutput: pdf\nInput:m\xfc\xe4in.tex\nLine:7\n'
        decoded = decode_process_output(raw)
        self.assertIn('\nLine:7\n', decoded)


if __name__ == '__main__':
    unittest.main()

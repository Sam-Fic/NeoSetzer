#!/usr/bin/env python3
# coding: utf-8

# 单元测试：换行符检测与保留
#
# 覆盖：
# - _detect_line_ending 对 LF / CRLF / CR / 空文件 / 混合的检测
# - 通过模拟文件读写验证换行符保留流程
#
# 注意：完整的 Document 类需要 GTK，这里只测试可独立运行的逻辑。

import os
import tempfile
import unittest

# 将 _detect_line_ending 逻辑复制为独立函数，避免 GTK 依赖
# 实际生产代码在 setzer/document/document.py 的 Document 类中

def detect_line_ending(raw_bytes):
    """检测文件的换行符格式。

    优先级：CRLF > CR > LF（CRLF 包含 CR 子串，需先判定）。
    空文件返回 LF。
    """
    if b'\r\n' in raw_bytes:
        return '\r\n'
    if b'\r' in raw_bytes:
        return '\r'
    return '\n'


def convert_line_endings(text, target_le):
    """将文本中的所有换行符统一为 LF，然后转换为目标换行符。"""
    # 先统一所有换行符为 LF（防御性处理）
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if target_le == '\n':
        return text
    return text.replace('\n', target_le)


class TestDetectLineEnding(unittest.TestCase):
    """测试换行符检测逻辑。"""

    def test_lf_file(self):
        """纯 LF 文件应检测为 LF。"""
        data = b'line1\nline2\nline3\n'
        self.assertEqual(detect_line_ending(data), '\n')

    def test_crlf_file(self):
        """纯 CRLF 文件应检测为 CRLF。"""
        data = b'line1\r\nline2\r\nline3\r\n'
        self.assertEqual(detect_line_ending(data), '\r\n')

    def test_cr_file(self):
        """纯 CR 文件应检测为 CR。"""
        data = b'line1\rline2\rline3\r'
        self.assertEqual(detect_line_ending(data), '\r')

    def test_empty_file(self):
        """空文件应检测为 LF。"""
        data = b''
        self.assertEqual(detect_line_ending(data), '\n')

    def test_single_line_no_ending(self):
        """单行无换行应检测为 LF。"""
        data = b'hello world'
        self.assertEqual(detect_line_ending(data), '\n')

    def test_mixed_crlf_and_cr(self):
        """混合 CRLF 和 CR 应优先检测为 CRLF。"""
        data = b'line1\r\nline2\rline3\r\n'
        self.assertEqual(detect_line_ending(data), '\r\n')

    def test_only_cr_without_lf(self):
        """只有 CR（没有 LF）应检测为 CR。"""
        data = b'line1\rline2\rline3'
        self.assertEqual(detect_line_ending(data), '\r')


class TestConvertLineEndings(unittest.TestCase):
    """测试换行符转换逻辑。"""

    def test_convert_to_lf_noop(self):
        """目标为 LF 时不应修改内容。"""
        text = 'line1\nline2\nline3\n'
        result = convert_line_endings(text, '\n')
        self.assertEqual(result, text)

    def test_convert_lf_to_crlf(self):
        """LF 转 CRLF。"""
        text = 'line1\nline2\nline3\n'
        result = convert_line_endings(text, '\r\n')
        self.assertEqual(result, 'line1\r\nline2\r\nline3\r\n')

    def test_convert_lf_to_cr(self):
        """LF 转 CR。"""
        text = 'line1\nline2\nline3\n'
        result = convert_line_endings(text, '\r')
        self.assertEqual(result, 'line1\rline2\rline3\r')

    def test_no_double_crlf(self):
        """原始 CRLF 文件被 Python 转为 LF 后，再转回 CRLF 不会出现双重转换。"""
        # 模拟 CRLF 文件被 Python 默认打开方式转为 LF 后的内容
        text = 'line1\nline2\nline3\n'
        result = convert_line_endings(text, '\r\n')
        self.assertNotIn('\r\r', result)  # 不应出现双重 CR
        self.assertEqual(result.count('\r\n'), 3)


class TestRoundTripPersistence(unittest.TestCase):
    """测试完整的读取-修改-保存流程中换行符的保留。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_test_file(self, name, content_bytes):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as f:
            f.write(content_bytes)
        return path

    def _read_test_file_raw(self, path):
        with open(path, 'rb') as f:
            return f.read()

    def test_crlf_preserved_after_read_modify_save(self):
        """CRLF 文件读取后修改保存，应仍为 CRLF。"""
        path = self._create_test_file('test.tex', b'line1\r\nline2\r\nline3\r\n')

        # 模拟 Document._load_file_content 的流程
        with open(path, 'rb') as f:
            raw_bytes = f.read()
        line_ending = detect_line_ending(raw_bytes)
        text = raw_bytes.decode('utf-8')

        # 用户修改
        text = text.replace('line2', 'line2_modified')

        # 模拟 Document.save_to_disk 的流程
        # 先统一所有换行符为 LF（防御性处理），再转换为目标格式
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)

        with open(path, 'wb') as f:
            f.write(text.encode('utf-8'))

        # 验证换行符未改变
        raw_after = self._read_test_file_raw(path)
        self.assertEqual(raw_after, b'line1\r\nline2_modified\r\nline3\r\n')
        self.assertIn(b'\r\n', raw_after)
        self.assertNotIn(b'\n', raw_after.replace(b'\r\n', b''))  # 不应有孤立 LF

    def test_cr_preserved_after_read_modify_save(self):
        """CR 文件读取后修改保存，应仍为 CR。"""
        path = self._create_test_file('test.tex', b'line1\rline2\rline3\r')

        with open(path, 'rb') as f:
            raw_bytes = f.read()
        line_ending = detect_line_ending(raw_bytes)
        text = raw_bytes.decode('utf-8')

        text = text.replace('line2', 'line2_modified')

        # 模拟 Document.save_to_disk 的流程
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)

        with open(path, 'wb') as f:
            f.write(text.encode('utf-8'))

        raw_after = self._read_test_file_raw(path)
        self.assertEqual(raw_after, b'line1\rline2_modified\rline3\r')
        self.assertNotIn(b'\n', raw_after)  # 不应有 LF

    def test_lf_preserved_after_read_modify_save(self):
        """LF 文件读取后修改保存，应仍为 LF。"""
        path = self._create_test_file('test.tex', b'line1\nline2\nline3\n')

        with open(path, 'rb') as f:
            raw_bytes = f.read()
        line_ending = detect_line_ending(raw_bytes)
        text = raw_bytes.decode('utf-8')

        text = text.replace('line2', 'line2_modified')

        # 模拟 Document.save_to_disk 的流程
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)

        with open(path, 'wb') as f:
            f.write(text.encode('utf-8'))

        raw_after = self._read_test_file_raw(path)
        self.assertEqual(raw_after, b'line1\nline2_modified\nline3\n')

    def test_newline_ending_preserved(self):
        """文件末尾无换行符的，应保持无末尾换行符。"""
        path = self._create_test_file('test.tex', b'line1\r\nline2')

        with open(path, 'rb') as f:
            raw_bytes = f.read()
        line_ending = detect_line_ending(raw_bytes)
        text = raw_bytes.decode('utf-8')

        text = text + '\nline3'

        # 模拟 Document.save_to_disk 的流程
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if line_ending != '\n':
            text = text.replace('\n', line_ending)

        with open(path, 'wb') as f:
            f.write(text.encode('utf-8'))

        raw_after = self._read_test_file_raw(path)
        self.assertEqual(raw_after, b'line1\r\nline2\r\nline3')


if __name__ == '__main__':
    unittest.main()
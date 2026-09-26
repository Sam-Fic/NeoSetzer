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

'''chktex 输出解析与问题定位（纯逻辑，不依赖 GTK，方便离线测试）。

chktex 以机器可读格式运行（见 build_chktex_args）：

    chktex -q -wall -f '%l:%c:%k:%n:%m\t%d\n'

每条问题占一行：

    行号:列号:种类:编号:消息文本<TAB>违规文本

- 消息文本可能含冒号，左侧只取前 4 个字段，其余整体归为消息；
- 违规文本（data）放在行尾 TAB 之后，其中的冒号/空格不受干扰；
- data 用于把问题定位回编辑器中的具体区间，比列号更可靠——chktex
  的列号在多字节字符（UTF-8）下的计数单位与 GtkSourceView 的字符
  偏移可能不一致，定位时优先「从列号附近起查找 data 在行内的首次
  出现」，找不到再全文找，仍找不到退化为整行。

行号为 0 的问题是文件级提示（对应不到具体行），只进问题列表、
不打下划线。chktex 的 kind 取值为 Warning / Error / Message，
为工具自身的英文名词，照原样展示、不做翻译。
'''

from dataclasses import dataclass

# chktex 输出格式：行:列:种类:编号:消息，TAB 之后是违规文本。
# 把变长文本字段全部放行尾并用 TAB 分隔，保证含冒号的消息/违规文本
# 都能无歧义切分。
FORMAT_STRING = '%l:%c:%k:%n:%m\t%d\n'


@dataclass(frozen=True)
class ChktexIssue:
    '''一条 chktex 报告。line 为 1-based 行号（0 = 文件级提示），
    column 为 chktex 给出的 1-based 列号（仅作定位参考）。'''

    line: int
    column: int | None
    kind: str
    number: int
    message: str
    data: str


def build_chktex_args():
    '''构造 chktex 命令行。-wall 打开全部警告，-q 关掉横幅噪声。'''
    return ['chktex', '-q', '-wall', '-f', FORMAT_STRING]


def parse_chktex_output(text):
    '''解析 chktex 输出为 ChktexIssue 元组。

    容错策略：横幅/空行/缺 TAB/字段不齐/行号列号非数字的行一律跳过
    （chktex 的输出按行自包含，坏行只损失该行，不影响其余）。
    '''
    issues = []
    for raw in text.splitlines():
        line = raw.rstrip('\r')
        if not line or '\t' not in line:
            continue
        head, _, data = line.partition('\t')
        parts = head.split(':', 4)
        if len(parts) != 5:
            continue
        line_str, column_str, kind, number_str, message = parts
        try:
            line_number = int(line_str)
            column = int(column_str)
            number = int(number_str)
        except ValueError:
            continue
        if line_number < 0:
            continue
        issues.append(ChktexIssue(
            line_number,
            column if column > 0 else None,
            kind.strip() or 'Message',
            number,
            message.strip(),
            data))
    return tuple(issues)


def locate_span(line_text, column, data):
    '''在行内定位违规文本区间，返回 (start, end) 字符偏移（0-based，
    end 不含）。找不到 data 或 data 为空时退化为整行。

    先从 column - 1（chktex 列号 1-based）起找 data 的首次出现；因
    列号计数单位可能与字符偏移不一致（多字节字符），找不到则退回
    从行首全文找——宁可选到同行的另一处相同文本，也不标整行。
    '''
    if data:
        start = column - 1 if column is not None and column > 0 else 0
        index = line_text.find(data, start)
        if index == -1:
            index = line_text.find(data)
        if index != -1:
            return (index, index + len(data))
    return (0, len(line_text))


def build_line_messages(issues):
    '''按行聚合问题消息，供 gutter 悬停提示使用。

    返回 {行号(1-based): (消息, ...)}；行号 < 1 的文件级提示无法悬停，
    不包含在内。消息前缀 chktex 的英文 kind 与编号，与编译诊断在
    gutter 中的「Error:/Warning:」前缀风格一致。
    '''
    messages = {}
    for issue in issues:
        if issue.line < 1:
            continue
        messages.setdefault(issue.line, []).append(
            '{} {}: {}'.format(issue.kind, issue.number, issue.message))
    return {line: tuple(items) for line, items in messages.items()}

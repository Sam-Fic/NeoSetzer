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

'''未使用 / 重复 \\label 的纯逻辑检查（不依赖 GTK，方便离线测试）。

分析只依赖两个序列：

- ``definitions`` —— 每个 ``\\label`` 出现一次记一项（同名定义两次即两项）；
- ``references`` —— 每次 ``\\ref`` 族命令的使用记一项（``\\ref{a,b}`` 拆成
  两项，由 parser 负责）。

「引用了但未定义」（undefined reference）由 LaTeX 编译器与构建日志报告，
这里只做编译器不管的两件事：定义了却从未被引用（unused），以及同名定义
多次（duplicate）。两者的严重度都按 warning 处理：不影响编译产物。
'''

from collections import Counter
from dataclasses import dataclass, field

from setzer.project.problem_center import ProjectProblem


@dataclass(frozen=True)
class LabelCheckReport:
    '''按名字聚合的检查结果。徽章等 UI 只需要名字级状态；带位置的
    问题明细走 iter_label_problems。'''

    unused_names: frozenset
    duplicate_names: frozenset
    duplicate_counts: dict = field(default_factory=dict)

    @property
    def is_empty(self):
        return not self.unused_names and not self.duplicate_names


def analyze_labels(definitions, references):
    '''返回 LabelCheckReport。空名字（如 ``\\label{}``）两侧都忽略：
    解析器允许空定义，但对其报「未使用」没有意义。'''
    referenced = set(references)
    counts = Counter(name for name in definitions if name)
    unused = frozenset(name for name in counts if name not in referenced)
    duplicates = frozenset(name for name, count in counts.items() if count > 1)
    return LabelCheckReport(
        unused_names=unused,
        duplicate_names=duplicates,
        duplicate_counts={name: counts[name] for name in duplicates},
    )


def iter_label_problems(definitions, references, describe):
    '''把检查结果展开为 ProjectProblem 序列（source='labels'）。

    definitions 为 ``(name, occurrence)`` 对，occurrence 不透明（通常为
    ``(document, offset)``），由 describe 映射为 ``(filename, line)``；
    describe 返回 None 或缺失时 filename/line 记 None，问题仍保留。
    同名重复时只报重复问题、不再叠加未使用（与徽章的优先级一致），
    问题按名字排序保证输出确定。
    '''
    report = analyze_labels([name for name, _ in definitions], references)
    if report.is_empty:
        return
    occurrences = dict()
    for name, occurrence in definitions:
        if name:
            occurrences.setdefault(name, []).append(occurrence)

    for name in sorted(report.duplicate_names):
        for occurrence in occurrences[name]:
            yield _label_problem(
                'Label "{}" is defined {} times'.format(name, report.duplicate_counts[name]),
                describe(occurrence))
    for name in sorted(report.unused_names - report.duplicate_names):
        for occurrence in occurrences[name]:
            yield _label_problem(
                'Label "{}" is never referenced'.format(name),
                describe(occurrence))


def _label_problem(description, location):
    filename, line = location if location else (None, None)
    actions = []
    if filename:
        actions.append('open-file')
    if isinstance(line, int) and line > 0:
        actions.append('jump-to-source')
    return ProjectProblem(
        'warning', 'labels', 'Labels', filename, line, description, tuple(actions))

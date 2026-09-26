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

'''自动重试的冷却判定（纯逻辑，无 gi 依赖，headless 可测）。

片段预览是「停止输入后自动编译」，若图本身编不过就会随每次打字反复
编译。轻 preamble 无所谓（150 ms 硬失败），但学位论文级 preamble 单次
可达 10-20 s——没有护栏就是一台 CPU 火炉。

策略：同一身份下连续失败 max_consecutive 次后停止自动重试，直到身份
变化（用户换了图，或改了 root preamble）或显式 reset()（面板上的
Retry 按钮）。**不**把「图内容变了」算作身份变化：多数失败根因在
preamble（缺宏包/缺库），改图通常改不好；而「改好图后还要手动点一下
Retry」的代价远小于「每敲一个字重编 20 秒」。
'''

DEFAULT_MAX_CONSECUTIVE_FAILURES = 2


class RetryPolicy(object):

    def __init__(self, max_consecutive=DEFAULT_MAX_CONSECUTIVE_FAILURES):
        self.max_consecutive = max_consecutive
        self._identity = None
        self._consecutive = 0
        self._last_reason = ''

    def should_attempt(self, identity):
        '''是否允许为 identity 发起自动尝试（同时登记身份变更）。

        identity 变化即开启新一轮计数——这是「换张图立刻有机会」和
        「改好 preamble 立刻有机会」的实现方式。调用方须在每次真正
        发起请求前调用一次；被拒绝时不要再请求。
        '''
        if identity != self._identity:
            self._identity = identity
            self._consecutive = 0
            self._last_reason = ''
        return self._consecutive < self.max_consecutive

    def record_success(self):
        self._consecutive = 0
        self._last_reason = ''

    def record_failure(self, reason=''):
        '''reason 为空时保留上一次的非空原因：失败原因往往首轮就有，
        后续轮次（如被取消）没带原因，面板上不该突然查无原因。'''
        self._consecutive += 1
        if reason:
            self._last_reason = reason

    def reset(self):
        '''用户显式重试：保留身份，只把计数清零。'''
        self._consecutive = 0

    @property
    def consecutive_failures(self):
        return self._consecutive

    @property
    def last_reason(self):
        return self._last_reason

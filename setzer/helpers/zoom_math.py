#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

'''应用级 UI 缩放的纯数值逻辑（gi-free，可在无 GTK 环境下直接单测）。

被 setzer/app/ui_zoom.py 使用：那里负责 GtkSettings:gtk-xft-dpi 的读写与
设置持久化，这里只做计算，遵守 tests/python 的 gi-free 测试原则。

设计：离散阶梯（ladder）而非连乘/连除。zoom level 只取 ZOOM_FACTOR^k
（k ∈ [K_MIN, K_MAX]，约 51%–236%）：
- in / out 是索引 ±1 的精确互逆，往返必然回到原值；连续 ×÷ 除浮点累积
  误差外，还有更隐蔽的不可逆缺陷：在上限 clamp 截断后再缩回会偏离 100%
  （如 ×1.1 十次到顶被夹到 2.5，再 ÷1.1 十次只得 0.964），阶梯化后不
  存在该问题（边界内往返严格还原）；
- 所有对外值都经 clamp_level 规范化，恒为阶梯值，显示与判定不漂移。

约定：
- zoom level 是相对 1.0 的倍率（k=0 恰为 1.0）；
- gtk-xft-dpi 的单位是 1024 × dpi；-1 表示「未设置，用后端默认」，等效
  96 dpi。基准为非正值时按 96 dpi 参与换算。
'''

import math

# 缩放步长：与编辑器字号缩放（FontManager.FONT_ZOOM_FACTOR）一致，保持手感统一。
ZOOM_FACTOR = 1.1
# 阶梯索引范围：K_MAX = floor(log(2.5)/log(1.1)) = 9 → 235.8%；
# K_MIN = ceil(log(0.5)/log(1.1)) = -7 → 51.3%。2.5 / 0.5 同时是持久化
# 垃圾值的硬 clamp 边界（见 clamp_level 的规范化）。
K_MAX = math.floor(math.log(2.5) / math.log(ZOOM_FACTOR))
K_MIN = math.ceil(math.log(0.5) / math.log(ZOOM_FACTOR))
# gtk-xft-dpi 未设置（-1 等）时的等效基准：96 dpi。
DEFAULT_DPI = 96 * 1024
# 浮点容差（阶梯值间的间距远大于此）。
_EPSILON = 1e-9


def level_from_index(k):
    '''阶梯索引 k → 倍率。k=0 精确等于 1.0（math.pow(1.1, 0)）。'''
    return math.pow(ZOOM_FACTOR, k)


def level_to_index(level):
    '''数值 → 最近的阶梯索引，夹到 [K_MIN, K_MAX]。

    对已是阶梯值的输入，log/pow 的往返误差远小于 0.5，round 后精确还原；
    对手改配置文件写进的任意值，取最近档位（吸附）。
    '''
    try:
        k = round(math.log(level) / math.log(ZOOM_FACTOR))
    except (ValueError, TypeError):
        # level <= 0 时 math.log 报错，统一按损坏数据处理。
        return 0
    return max(K_MIN, min(K_MAX, k))


def clamp_level(level):
    '''规范化为合法阶梯倍率。

    NaN / Inf / 非正数 / 非数值视为损坏数据 → 1.0；其余吸附到最近阶梯值
    （这是 in / out 精确互逆的前提：manager 的当前值恒为阶梯值）。
    '''
    try:
        level = float(level)
    except (TypeError, ValueError):
        return 1.0
    if level != level or level in (float('inf'), float('-inf')) or level <= 0:
        return 1.0
    return level_from_index(level_to_index(level))


def next_zoom_in(level):
    '''放大一步（索引 +1）；已在阶梯顶端则原值返回。'''
    k = level_to_index(clamp_level(level))
    return level_from_index(min(k + 1, K_MAX))


def next_zoom_out(level):
    '''缩小一步（索引 -1）；已在阶梯底端则原值返回。'''
    k = level_to_index(clamp_level(level))
    return level_from_index(max(k - 1, K_MIN))


def is_default(level):
    '''是否处于 100%。阶梯值保证 k=0 精确等于 1.0，可直接比较。'''
    return clamp_level(level) == 1.0


def can_zoom_in(level):
    return level_to_index(clamp_level(level)) < K_MAX


def can_zoom_out(level):
    return level_to_index(clamp_level(level)) > K_MIN


def zoomed_dpi(base_dpi, level):
    '''原始 gtk-xft-dpi × 倍率，返回可写入属性的 int。

    base_dpi 为 None 或非正值（-1 等）时按 96 dpi 基准换算——GTK 对 -1 的
    语义即「默认值」，等效 96，故换算结果与系统默认视觉一致。
    '''
    base = base_dpi if (base_dpi is not None and base_dpi > 0) else DEFAULT_DPI
    return int(round(base * clamp_level(level)))


def format_percent(level):
    '''缩放百分比的统一显示格式（'110%'）。'''
    return '{:.0%}'.format(clamp_level(level))

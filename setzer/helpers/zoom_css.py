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

'''应用缩放的「非文字量」样式表生成（纯逻辑，gi-free，可脱离 GTK 直接单测）。

为什么需要它：gtk-xft-dpi 只提高 Pango 的文字渲染分辨率，而 CSS 里的长度量
（-gtk-icon-size、font-size 的显式声明）是设备无关值，**不随 dpi 变化**。实测
（GTK 4.22 + libadwaita 1.9，改 dpi ×1.5）：

- 未声明 font-size 的默认字体标签：122px → 188px（随 dpi 缩放）✓
- `font-size: 11pt` / `15px` / `0.9em` 的标签：宽高基本不变 ✗
- `-gtk-icon-size` 图标：16×16、32×32 恒定不变 ✗

于是只写 dpi 的「应用缩放」会呈现文字变大、图标与显式字号原地不动的错位。
本模块把这些量镜像成一份按倍率放大的样式表，由 setzer/app/ui_zoom.py 以高于
USER 的优先级装载，使图标（以及自研 CSS 里的 em 字号）与文字同步缩放。

编辑器字号是另一条路：它由 FontManager 以显式 pt 写进 CSS（见
FontManager.propagate_font_setting），那里按同一倍率乘进去，不经过本模块。

约定与代价：
- 装载优先级高于主题 ⇒ 主题里未登记的 -gtk-icon-size 规则会被 `*` 兜底值压平，
  所以 THEME_ICON_SIZE_RULES 必须穷举主题的声明（数据取自 libadwaita 1.9 的
  /org/gnome/Adwaita/styles/* 与 GTK 4.22 内置主题的全部 -gtk-icon-size 字面值）。
  选择器逐字照抄：将来 libadwaita 改名只会让对应规则失配（该处图标不缩放），
  属优雅降级而非破坏。
- 自研样式（data/resources/style_gtk.css）**不硬编码**：运行时读原文并用
  parse_scalable_declarations 抽出声明自动镜像，改样式表不需要同步本模块。
- 100% 时 build_css 返回空串：不装 provider，样式表零改动，与未启用缩放完全一致。
- padding / margin / min-height 等几何量仍按系统比例（改这些等于重写主题，
  风险远大于收益）。图标变大后按钮靠内容自然撑开，不会被 min-height 裁掉。
'''

import math
import re

# 主题 -gtk-icon-size 镜像表：(选择器, 基准 px)。`*` 兜底放最前，其余按特异性
# 在同一 provider 内自然覆盖它。基准值必须与主题一致：k=1 时逐字等于主题，
# 保证「装与不装」在 100% 下完全等价。
THEME_ICON_SIZE_RULES = (
    # 默认图标尺寸（headerbar 按钮、菜单、entry、spinbutton 等的绝大多数图标）
    ('*', 16),
    ('.normal-icons', 16),
    # 复选/单选指示器
    ('check, radio', 14),
    ('popover.menu check, popover.menu radio', 14),
    ('treeview.view radio:selected:focus, treeview.view radio:selected, radio', 14),
    # GtkTreeView 展开层级缩进箭头
    ('treeexpander indent', 8),
    # 自绘标题栏的窗口控制按钮
    ('headerbar windowcontrols.start > image.icon', 20),
    ('headerbar windowcontrols.end > image.icon', 20),
    # 通用大图标类
    ('.large-icons', 32),
    # 关于对话框的应用图标
    ('window.aboutdialog image.large-icons', 128),
    ('window.aboutdialog:not(.ssd-frame) image.large-icons', 128),
    # AdwStatusPage 图标（welcome 页 / 空状态）
    ('statuspage > scrolledwindow > viewport > box > clamp > box > .icon', 128),
    ('statuspage.compact > scrolledwindow > viewport > box > clamp > box > .icon', 96),
    ('sidebar:not(.page).empty statuspage > scrolledwindow > viewport > box > clamp > box > .icon', 96),
    ('statuspage.spinner > scrolledwindow > viewport > box > clamp > box > .icon', 64),
)

# 支持镜像的属性 → 允许的取值单位。
SCALABLE_PROPERTIES = {
    '-gtk-icon-size': 'px',
    'font-size': 'em',
}

# build_css 输出用的最小尺寸保护（level 已被 zoom_math 夹在 0.51–2.36，正常不触发）。
_MIN_ICON_PX = 1

# 只含这些字符的选择器才敢照抄进生成的样式表：GTK4 CSS 解析器对非法选择器是
# 整表报错（load_from_string 抛异常 → 整套缩放样式失效），所以宁可跳过可疑声明。
# 特别是 @media 块会让粗粒度正则把 "@media ..." 当选择器的一部分，必须挡掉。
_SELECTOR_ALLOWED = re.compile(r'^[\w\s.:#>*\[\]()"\'=~-]+$')
# 注释块（/* ... */）：先剔除，否则注释里的 '@media' / 花括号会干扰块级扫描
_COMMENT = re.compile(r'/\*.*?\*/', re.S)
# 粗粒度样式块：选择器 { 声明; ... }
_RULE_BLOCK = re.compile(r'([^{}]+)\{([^{}]*)\}')
# 属性声明：'<prop>: <num><unit>'
_DECLARATION = re.compile(
    r'(?:(-gtk-icon-size)|(?<![-\w])font-size)\s*:\s*([0-9]*\.?[0-9]+)(px|em)\b')


def scaled_size(base_px, level):
    '''基准 px × 倍率 → 整数 px（下限 1）。

    取整而非保留浮点：CSS 长度按整数 px 解析最稳，且 k=1 时逐字还原基准。
    '''
    try:
        value = int(round(base_px * float(level)))
    except (TypeError, ValueError):
        return int(base_px)
    return max(_MIN_ICON_PX, value)


def scaled_em(value_em, level):
    '''em 值 × 倍率 → calc() 表达式。

    用 calc(<orig>em * <k>) 而不是先算成 px：em 的基准（父节点字号）由 GTK 在
    运行时解析，我们只补「缩放这一层」，不需要也不该猜系统字体名/字号。
    '''
    return 'calc({:g}em * {:.6g})'.format(value_em, float(level))


def strip_at_rules(css_text):
    '''丢弃 @media / @supports / @keyframes 等 at-rule 的整块内容，只留顶层规则。

    镜像 at-rule 内的声明会丢条件（把「高对比度下才生效」变成无条件生效），语义
    不对等 ⇒ 宁可跳过。@import/@charset 这类语句形式一并丢弃。
    '''
    out = []
    depth = 0
    index = 0
    length = len(css_text)
    while index < length:
        char = css_text[index]
        if depth:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
            index += 1
            continue
        if char == '@':
            brace = css_text.find('{', index)
            semi = css_text.find(';', index)
            if brace != -1 and (semi == -1 or brace < semi):
                depth = 1
                index = brace + 1
                continue
            index = (semi + 1) if semi != -1 else length
            continue
        out.append(char)
        index += 1
    return ''.join(out)


def parse_scalable_declarations(css_text):
    '''从 CSS 文本抽出可镜像声明：((selector, property, value), ...)，value 为数值。

    供自研样式表（style_gtk.css）的运行时镜像使用：先剔注释、再剔 at-rule，
    最后扫扁平块；非法选择器整条丢弃（见 _SELECTOR_ALLOWED 的说明）。
    '''
    stripped = strip_at_rules(_COMMENT.sub(' ', css_text or ''))
    found = []
    for match in _RULE_BLOCK.finditer(stripped):
        selector = re.sub(r'\s+', ' ', match.group(1)).strip()
        if not selector or not _SELECTOR_ALLOWED.match(selector):
            continue
        body = match.group(2)
        for decl in _DECLARATION.finditer(body):
            prop = '-gtk-icon-size' if decl.group(1) else 'font-size'
            value = float(decl.group(2))
            if SCALABLE_PROPERTIES[prop] != decl.group(3):
                continue
            found.append((selector, prop, value))
    return found


def build_css(level, app_declarations=(), theme_rules=THEME_ICON_SIZE_RULES):
    '''生成当前倍率下的镜像样式表；倍率为 1（100%）时返回空串。

    app_declarations：parse_scalable_declarations(自研 CSS) 的结果，追加在主题表
    之后 —— 同一 provider 内特异性起作用，自研规则（如 .sidebar-empty-state image
    48px）会正确压过 `*` 的 16px 兜底。
    '''
    try:
        level = float(level)
    except (TypeError, ValueError):
        return ''
    # NaN / Inf / 非正 / 非法类型一律不生成（Inf 会在取整时 OverflowError）。
    if not math.isfinite(level) or level <= 0 or abs(level - 1.0) < 1e-9:
        return ''

    lines = []
    emitted = set()
    # 自研声明优先（去重后不再要主题表里的同名项），但顺序上仍先输出 `*` 兜底，
    # 保证特异性覆盖关系与源样式表一致。
    app_map = {}
    for selector, prop, value in app_declarations:
        app_map[(selector, prop)] = value
    for selector, base_px in theme_rules:
        if (selector, '-gtk-icon-size') in app_map:
            continue
        lines.append(_rule(selector, '-gtk-icon-size',
                           '{}px'.format(scaled_size(base_px, level))))
        emitted.add((selector, '-gtk-icon-size'))
    for (selector, prop), value in app_map.items():
        if prop == '-gtk-icon-size':
            rendered = '{}px'.format(scaled_size(value, level))
        else:
            rendered = scaled_em(value, level)
        lines.append(_rule(selector, prop, rendered))
    return '\n'.join(lines)


def _rule(selector, prop, value):
    return '{} {{ {}: {}; }}'.format(selector, prop, value)

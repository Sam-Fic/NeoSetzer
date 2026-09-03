#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic

# coding: utf-8

# 单元测试：setzer.helpers.zoom_css（应用缩放的「非文字量」样式表生成）。
#
# 覆盖：
# - 100% 必须返回空串（不装 provider ⇒ 与未启用缩放零差异）；
# - 图标 px 取整规则、下限保护、非法倍率防御；
# - em 字号镜像成 calc(<orig>em * k)（不猜系统字体，交 GTK 运行时解析）；
# - `*` 兜底排最前、自研声明后置且去重（特异性覆盖关系与源样式一致）；
# - 自研样式表解析：注释/at-rule/@import 的干扰与选择器白名单；
# - 守卫：data/resources/style_gtk.css 里每个可镜像声明都必须被解析到
#   （运行时镜像依赖它，漏一条该处图标/字号就不随缩放）。

import os.path
import unittest

from setzer.helpers import zoom_css


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
APP_CSS_PATH = os.path.join(REPO_ROOT, 'data', 'resources', 'style_gtk.css')


class TestScaledSize(unittest.TestCase):

    def test_rounds_to_nearest_int_px(self):
        self.assertEqual(zoom_css.scaled_size(16, 1.1), 18)       # 17.6
        self.assertEqual(zoom_css.scaled_size(32, 1.1), 35)       # 35.2
        self.assertEqual(zoom_css.scaled_size(14, 1.1), 15)       # 15.4
        self.assertEqual(zoom_css.scaled_size(16, 1.21), 19)      # 19.36

    def test_floor_at_one_pixel(self):
        self.assertEqual(zoom_css.scaled_size(8, 0.01), 1)

    def test_non_numeric_level_returns_base(self):
        self.assertEqual(zoom_css.scaled_size(16, 'broken'), 16)
        self.assertEqual(zoom_css.scaled_size(16, None), 16)


class TestBuildCss(unittest.TestCase):

    def test_default_level_returns_empty(self):
        self.assertEqual(zoom_css.build_css(1.0), '')
        self.assertEqual(zoom_css.build_css(1.0, (('.x', 'font-size', 0.9),)), '')

    def test_garbage_levels_return_empty(self):
        for level in (0, -1, None, 'x', float('nan'), float('inf')):
            self.assertEqual(zoom_css.build_css(level), '', msg=repr(level))

    def test_wildcard_fallback_is_first_rule(self):
        # `*` 兜底必须排在所有具体选择器之前：同一 provider 内特异性靠后置覆盖
        lines = zoom_css.build_css(1.5).splitlines()
        self.assertTrue(lines[0].startswith('* {'))

    def test_all_theme_rules_present_and_scaled(self):
        css = zoom_css.build_css(2.0)
        for selector, base_px in zoom_css.THEME_ICON_SIZE_RULES:
            self.assertIn(
                '{} {{ -gtk-icon-size: {}px; }}'.format(selector, base_px * 2), css)

    def test_every_generated_selector_is_plain(self):
        # 生成表里不能混进 at-rule 头/注释/花括号：非法选择器会让整表解析失败
        for line in zoom_css.build_css(1.331).splitlines():
            selector = line.split('{')[0].strip()
            self.assertNotIn('@', selector)
            self.assertNotIn('}', selector)
            self.assertNotIn('/*', selector)

    def test_em_font_sizes_use_calc(self):
        css = zoom_css.build_css(1.1, (('.drop-label', 'font-size', 1.1),))
        self.assertIn('.drop-label { font-size: calc(1.1em * 1.1); }', css)

    def test_app_declaration_replaces_same_selector_theme_rule(self):
        # 同一 (选择器, 属性) 只出一份：自研值优先，且不重复输出主题项
        css = zoom_css.build_css(
            2.0, (('*', '-gtk-icon-size', 24.0),))
        self.assertEqual(css.count('* {'), 1)
        self.assertIn('* { -gtk-icon-size: 48px; }', css)

    def test_app_rules_come_after_theme_fallback(self):
        css = zoom_css.build_css(2.0, (('.big image', '-gtk-icon-size', 48.0),))
        self.assertLess(css.index('* {'), css.index('.big image {'))


class TestParseDeclarations(unittest.TestCase):

    def test_parses_icon_px_and_em_font_size(self):
        found = zoom_css.parse_scalable_declarations(
            '.a image { -gtk-icon-size: 48px; }\n'
            '.b { font-size: 0.9em; color: red; }')
        self.assertEqual(found, [('.a image', '-gtk-icon-size', 48.0),
                                 ('.b', 'font-size', 0.9)])

    def test_ignores_unsupported_units(self):
        # px 字号不能镜像：它的基准不是默认字体，乘算是另一条路（编辑器由
        # FontManager 处理），这里刻意只认 em
        self.assertEqual(zoom_css.parse_scalable_declarations('.a { font-size: 15px; }'), [])
        self.assertEqual(zoom_css.parse_scalable_declarations('.a { font-size: larger; }'), [])
        self.assertEqual(zoom_css.parse_scalable_declarations('.a { -gtk-icon-size: inherit; }'), [])

    def test_comments_with_at_mentions_do_not_break_parsing(self):
        # style_gtk.css 里真有这种注释（「不依赖 @media prefers-contrast」）
        found = zoom_css.parse_scalable_declarations(
            '/* 不依赖 @media prefers-contrast: more 触发 */\n.a { font-size: 0.8em; }')
        self.assertEqual(found, [('.a', 'font-size', 0.8)])

    def test_at_rule_blocks_are_skipped(self):
        # at-rule 内的声明带条件，无条件镜像会改变语义 ⇒ 整块丢弃
        found = zoom_css.parse_scalable_declarations(
            '@media (prefers-contrast: more) { .dark-only { font-size: 0.9em } }\n'
            '.plain { -gtk-icon-size: 16px }')
        self.assertEqual(found, [('.plain', '-gtk-icon-size', 16.0)])

    def test_at_statement_lines_are_skipped(self):
        found = zoom_css.parse_scalable_declarations(
            '@import url("other.css");\n.a { -gtk-icon-size: 32px }')
        self.assertEqual(found, [('.a', '-gtk-icon-size', 32.0)])

    def test_selector_whitespace_is_normalized(self):
        found = zoom_css.parse_scalable_declarations(
            '.a\n    > .b {\n  -gtk-icon-size: 16px;\n}')
        self.assertEqual(found, [('.a > .b', '-gtk-icon-size', 16.0)])

    def test_real_app_css_is_fully_covered(self):
        # 守卫：样式表里每条可镜像声明都必须被解析到（漏一条 = 该处不随缩放）
        if not os.path.exists(APP_CSS_PATH):
            self.skipTest('style_gtk.css 不在仓库预期位置')
        with open(APP_CSS_PATH, encoding='utf-8') as handle:
            text = handle.read()
        expected = [
            (selector.strip(), prop, float(value))
            for selector, body in _flat_blocks(text)
            for prop, value in _declarations(body)
            if selector.strip() and not _inside_at_rule(text, selector)]
        found = zoom_css.parse_scalable_declarations(text)
        self.assertTrue(found, '解析结果为空，说明解析器已失效')
        self.assertEqual(sorted(found), sorted(expected))


def _flat_blocks(css_text):
    """独立实现的参考解析（不复用被测代码）：顶层块 → (选择器, 声明文本)。"""
    import re
    stripped = re.sub(r'/\*.*?\*/', ' ', css_text, flags=re.S)
    blocks = []
    for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', stripped):
        selector = re.sub(r'\s+', ' ', match.group(1)).strip()
        blocks.append((selector, match.group(2)))
    return blocks


def _declarations(body):
    import re
    out = []
    for match in re.finditer(r'(-gtk-icon-size|font-size)\s*:\s*([0-9]*\.?[0-9]+)(px|em)\b', body):
        prop, value, unit = match.group(1), match.group(2), match.group(3)
        wanted = 'px' if prop == '-gtk-icon-size' else 'em'
        if unit == wanted:
            out.append((prop, value))
    return out


def _inside_at_rule(css_text, selector):
    """参考实现的粗判：选择器文本自带 at-rule 头 ⇒ 属于 at-rule 内。"""
    return '@' in selector


if __name__ == '__main__':
    unittest.main()

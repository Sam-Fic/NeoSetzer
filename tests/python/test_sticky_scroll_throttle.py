#!/usr/bin/env python3
# Copyright (C) 2026-present Sam-Fic
# SPDX-License-Identifier: GPL-3.0-or-later

'''sticky scroll 在重排/动画期间的每帧开销节流（无界面 AST 测试）。

侧栏开合动画每帧改编辑器宽度，sticky scroll 每帧重算高度并重绘。两条热路径
必须便宜：高度没变就不能碰 set_size_request（GTK 不做值比较，会无条件
queue_resize 并冒泡到窗口 → 每帧全子树重新测量）；配色与字体描述在不变时
不能每帧重新解析/分配。'''

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
STICKY = 'setzer/document/sticky_scroll/sticky_scroll.py'


class RGBA:
    def __init__(self, red=0.0, green=0.0, blue=0.0, alpha=1.0):
        self.red, self.green, self.blue, self.alpha = red, green, blue, alpha


class Layout:
    '''Pango.Layout 替身：记录 setter 调用次数。'''

    def __init__(self):
        self.calls = Mock()

    def set_font_description(self, desc):
        self.calls.set_font_description(desc)

    def set_width(self, w):
        self.calls.set_width(w)

    def set_ellipsize(self, e):
        self.calls.set_ellipsize(e)

    def set_text(self, t, n):
        self.calls.set_text(t, n)

    def set_alignment(self, a):
        self.calls.set_alignment(a)


def make_sticky(**namespace_extra):
    source = ROOT / STICKY
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls_node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'StickyScroll')
    names = {'_update_height', '_get_colors', '_sync_font_desc',
             '_get_type_label', '_parse_hex', '_update_margin',
             'on_size_changed'}
    methods = [node for node in cls_node.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        'FontManager': SimpleNamespace(get_line_height=lambda view: 20),
        'ColorManager': SimpleNamespace(
            get_ui_color=lambda name: RGBA(0.1, 0.2, 0.3)),
        'Adw': SimpleNamespace(StyleManager=SimpleNamespace(
            get_default=lambda: SimpleNamespace(get_dark=lambda: make_sticky.dark))),
        'Gdk': SimpleNamespace(RGBA=RGBA),
        'Pango': SimpleNamespace(SCALE=1024,
                                 EllipsizeMode=SimpleNamespace(END='end', NONE='none'),
                                 Alignment=SimpleNamespace(LEFT='left')),
    }
    namespace.update(namespace_extra)
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
    attrs = {node.name: namespace[node.name] for node in methods}
    attrs['_parse_hex'] = staticmethod(attrs['_parse_hex'])
    cls = type('StickyScroll', (), attrs)

    sticky = cls()
    sticky.source_view = SimpleNamespace()
    sticky.source_buffer = SimpleNamespace(get_style_scheme=lambda: None)
    sticky.current_sections = []
    sticky._next_section = None
    sticky._offset = 0
    sticky._section_height = 28
    sticky._requested_height = None
    sticky._colors_cache = None
    sticky._font_desc_str = None
    sticky.layout = Layout()
    sticky.type_layout = Layout()
    sticky._margin_width = 0
    sticky.margin_width = 40
    sticky.drawing_area = SimpleNamespace(set_size_request=Mock(),
                                         set_margin_start=Mock())
    sticky.view = SimpleNamespace(
        margin=SimpleNamespace(get_width=lambda: sticky.margin_width))
    sticky._refresh_scheduled = 0
    sticky._schedule_refresh = lambda: setattr(
        sticky, '_refresh_scheduled', sticky._refresh_scheduled + 1)
    return sticky


make_sticky.dark = False


class TestUpdateHeightThrottle(unittest.TestCase):
    def test_first_call_sets_size_request(self):
        sticky = make_sticky()
        sticky.current_sections = [('section', 'Intro', 1, 2, 2)]
        sticky._update_height()
        sticky.drawing_area.set_size_request.assert_called_once_with(-1, 20)
        self.assertEqual(sticky._requested_height, 20)

    def test_repeated_same_height_does_not_resize(self):
        sticky = make_sticky()
        sticky.current_sections = [('section', 'Intro', 1, 2, 2)]
        for _ in range(30):
            sticky._update_height()
        # 侧栏动画每帧重算高度，但高度不变时必须一次都不碰 set_size_request。
        sticky.drawing_area.set_size_request.assert_called_once_with(-1, 20)

    def test_height_change_resizes_again(self):
        sticky = make_sticky()
        sticky.current_sections = [('section', 'Intro', 1, 2, 2)]
        sticky._update_height()
        sticky.current_sections.append(('subsection', 'Details', 5, 6, 3))
        sticky._update_height()
        self.assertEqual(sticky.drawing_area.set_size_request.call_count, 2)
        sticky.drawing_area.set_size_request.assert_called_with(-1, 40)

    def test_next_section_offset_counts_one_more_row(self):
        sticky = make_sticky()
        sticky.current_sections = [('section', 'Intro', 1, 2, 2)]
        sticky._next_section = ('section', 'Next', 9, 10, 2)
        sticky._offset = 8
        sticky._update_height()
        sticky.drawing_area.set_size_request.assert_called_once_with(-1, 40)

    def test_no_sections_requests_zero(self):
        sticky = make_sticky()
        sticky._update_height()
        sticky.drawing_area.set_size_request.assert_called_once_with(-1, 0)


class TestResizeRefresh(unittest.TestCase):
    def test_size_change_syncs_margin_and_schedules_refresh(self):
        sticky = make_sticky()
        sticky.on_size_changed(780, 600)
        sticky.drawing_area.set_margin_start.assert_called_once_with(40)
        self.assertEqual(sticky._refresh_scheduled, 1)

    def test_unchanged_margin_is_not_rewritten(self):
        sticky = make_sticky()
        sticky.on_size_changed(780, 600)
        sticky.drawing_area.set_margin_start.reset_mock()
        # 侧栏动画每帧都会派发一次几何变化；margin 没变就不能再写
        # set_margin_start（它会 queue_resize，等于每帧重排一次布局）。
        for width in (760, 740, 720):
            sticky.on_size_changed(width, 600)
        sticky.drawing_area.set_margin_start.assert_not_called()
        self.assertEqual(sticky._refresh_scheduled, 4)

    def test_margin_width_change_is_written(self):
        sticky = make_sticky()
        sticky.on_size_changed(780, 600)
        sticky.margin_width = 52
        sticky.on_size_changed(780, 600)
        self.assertEqual(sticky.drawing_area.set_margin_start.call_count, 2)
        sticky.drawing_area.set_margin_start.assert_called_with(52)


class TestColorsCache(unittest.TestCase):
    def make_buffer(self, fg='#101010', bg='#fefefe'):
        style = SimpleNamespace(props=SimpleNamespace(foreground=fg, background=bg))
        scheme = SimpleNamespace(get_style=lambda name: style)
        return SimpleNamespace(get_style_scheme=lambda: scheme)

    def make_counting_sticky(self):
        '''RGBA 分配计数的实例：缓存命中时不应再分配新的颜色对象。'''
        counter = {'rgba': 0}

        def factory(**kwargs):
            counter['rgba'] += 1
            return RGBA(**kwargs)

        source = ROOT / STICKY
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls_node = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef)
                        and node.name == 'StickyScroll')
        names = {'_get_colors', '_parse_hex'}
        methods = [node for node in cls_node.body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {
            'ColorManager': SimpleNamespace(get_ui_color=lambda name: RGBA()),
            'Adw': SimpleNamespace(StyleManager=SimpleNamespace(
                get_default=lambda: SimpleNamespace(
                    get_dark=lambda: make_sticky.dark))),
            'Gdk': SimpleNamespace(RGBA=factory),
        }
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'),
             namespace)
        attrs = {node.name: namespace[node.name] for node in methods}
        attrs['_parse_hex'] = staticmethod(attrs['_parse_hex'])
        sticky = type('StickyScroll', (), attrs)()
        sticky._colors_cache = None
        return sticky, counter

    def test_parses_scheme_colors_once(self):
        sticky, counter = self.make_counting_sticky()
        sticky.source_buffer = self.make_buffer()
        fg, bg = sticky._get_colors()
        self.assertAlmostEqual(fg.red, 0x10 / 255.0)
        self.assertAlmostEqual(bg.blue, 0xfe / 255.0)
        self.assertEqual(counter['rgba'], 2)

        for _ in range(5):
            self.assertIs(sticky._get_colors()[0], fg)
        # 缓存命中：不再解析十六进制字符串、不再分配 RGBA。
        self.assertEqual(counter['rgba'], 2)

    def test_theme_flip_invalidates_cache(self):
        sticky = make_sticky()
        sticky.source_buffer = self.make_buffer()
        light_fg = sticky._get_colors()[0]
        make_sticky.dark = True
        try:
            # 深浅色切换不体现在 style 字符串上（走 ColorManager 回退色的路径
            # 才受影响），但缓存 key 含 dark 标志，必须重算而不是复用旧值。
            dark_fg = sticky._get_colors()[0]
        finally:
            make_sticky.dark = False
        self.assertIsNot(dark_fg, light_fg)
        self.assertAlmostEqual(dark_fg.red, 0x10 / 255.0)

    def test_scheme_change_invalidates_cache(self):
        sticky = make_sticky()
        sticky.source_buffer = self.make_buffer()
        first = sticky._get_colors()[0]
        sticky.source_buffer = self.make_buffer(fg='#202020')
        second = sticky._get_colors()[0]
        self.assertIsNot(first, second)
        self.assertAlmostEqual(second.red, 0x20 / 255.0)


class TestFontDescSync(unittest.TestCase):
    def make_view(self, desc):
        context = SimpleNamespace(get_font_description=lambda: desc)
        return SimpleNamespace(get_pango_context=lambda: context)

    def test_sets_font_description_on_first_draw(self):
        sticky = make_sticky()
        desc = SimpleNamespace(to_string=lambda: 'Monospace 11')
        sticky.source_view = self.make_view(desc)
        sticky._sync_font_desc()
        sticky.layout.calls.set_font_description.assert_called_once_with(desc)
        sticky.type_layout.calls.set_font_description.assert_called_once_with(desc)

    def test_unchanged_font_description_is_not_reapplied(self):
        sticky = make_sticky()
        desc = SimpleNamespace(to_string=lambda: 'Monospace 11')
        sticky.source_view = self.make_view(desc)
        for _ in range(20):
            sticky._sync_font_desc()
        sticky.layout.calls.set_font_description.assert_called_once_with(desc)

    def test_zoom_change_reapplies_font_description(self):
        sticky = make_sticky()
        descs = [SimpleNamespace(to_string=lambda: 'Monospace 11')]
        sticky.source_view = SimpleNamespace(
            get_pango_context=lambda: SimpleNamespace(
                get_font_description=lambda: descs[0]))
        sticky._sync_font_desc()
        descs[0] = SimpleNamespace(to_string=lambda: 'Monospace 12')
        sticky._sync_font_desc()
        self.assertEqual(sticky.layout.calls.set_font_description.call_count, 2)


if __name__ == '__main__':
    unittest.main()

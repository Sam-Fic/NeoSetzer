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

'''单文件并排 diff（VSCode 式），非 LaTeX 文件与 PDF diff 兜底路径共用。

左右两栏分别是 HEAD 版与工作区版全文，行按 difflib 对齐（同一行在两栏里
指同一行，空出来的一侧留空），改动行整行着色：左栏红（旧）、右栏绿（新）。

外观与主编辑器一致：
- 两栏都是 GtkSource.View + 用户配色方案（等宽、行距、颜色与源码区同源）；
- 行号由自绘 gutter 列绘制（与 document/gutter 同一套坐标换算），
  右侧对齐、本侧不存在的行留空；
- 两栏包在一张 8px 圆角的卡片里（css .diff-card），与 .editor-card 同规范；
- 自动换行跟随 preferences/enable_line_wrapping，且实时响应设置变更。

窗口是标准的 Adw.Window（右上角窗口关闭按钮、Esc 关闭），而非模态
Adw.Dialog：正文可能很长，用户往往要一边看 diff 一边操作主窗口。
'''

import builtins
import difflib


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Adw, Gdk, Pango, PangoCairo, GtkSource

from setzer.app.color_manager import ColorManager
from setzer.app.font_manager import FontManager
from setzer.app.service_locator import ServiceLocator


# 超过这个行数就不做并排渲染（两侧各建一个上万行的 Buffer 会卡）。
MAX_DIFF_LINES = 5000

# 改动行底色。用半透明 rgba 让深浅主题都成立（前景色仍由主题决定）。
_COLOR_REMOVED = 'rgba(192, 28, 40, 0.22)'
_COLOR_ADDED = 'rgba(38, 162, 105, 0.22)'

# 行号列左右留白（与正文之间的呼吸空间）。
_GUTTER_PAD_START = 6
_GUTTER_PAD_END = 10
# view realize 前拿不到可靠字体度量时的兜底左内缩；之后由
# update_gutter_width 按行号位数精确设置。
_VIEW_LEFT_MARGIN = 6

# 打开中的窗口：Adw.Window 在 presented 期间虽由 GTK 持有，但 Python 侧
# 没有任何引用时析构时机不确定（信号回调里的 self 会成悬空引用），显式
# 留一份引用，close-request 时移除。
_open_windows = set()


def align_lines(old_text, new_text):
    '''把两侧全文对齐成 [(old_line 或 None, new_line 或 None, kind), ...]。

    kind ∈ equal / change / delete / insert：change 两侧都有（对位改写），
    delete 只有旧侧，insert 只有新侧。返回行序即渲染行序，两栏行数恒等。'''
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    rows = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            for offset in range(i2 - i1):
                rows.append((old_lines[i1 + offset], new_lines[j1 + offset], 'equal'))
        elif tag == 'replace':
            for offset in range(max(i2 - i1, j2 - j1)):
                old_line = old_lines[i1 + offset] if i1 + offset < i2 else None
                new_line = new_lines[j1 + offset] if j1 + offset < j2 else None
                rows.append((old_line, new_line, 'change'))
        elif tag == 'delete':
            for index in range(i1, i2):
                rows.append((old_lines[index], None, 'delete'))
        elif tag == 'insert':
            for index in range(j1, j2):
                rows.append((None, new_lines[index], 'insert'))
    return rows


def line_numbers(rows, side):
    '''给对齐后的每一行分配原始文件行号（本侧缺失的行给 None）。

    rows 保持各自文件的原始行序（equal/change/delete/insert 顺序拼接），
    所以「逐行累加本侧非空行」得到的就是真实行号，而不是显示行下标。
    对齐行的两栏行数恒等，因此两侧列表长度一致，滚动映射可以直接按下标。'''
    numbers = []
    number = 0
    for old_line, new_line, kind in rows:
        line = old_line if side == 'old' else new_line
        if line is None:
            numbers.append(None)
        else:
            number += 1
            numbers.append(number)
    return numbers


def _parse_hex_color(value):
    '''把样式方案里的 '#rrggbb' / '#rrggbbaa' 解析成 Gdk.RGBA。'''
    if not value:
        return None
    value = value.strip().lstrip('#')
    try:
        if len(value) == 6:
            return Gdk.RGBA(red=int(value[0:2], 16) / 255.0,
                            green=int(value[2:4], 16) / 255.0,
                            blue=int(value[4:6], 16) / 255.0, alpha=1.0)
        if len(value) == 8:
            return Gdk.RGBA(red=int(value[0:2], 16) / 255.0,
                            green=int(value[2:4], 16) / 255.0,
                            blue=int(value[4:6], 16) / 255.0,
                            alpha=int(value[6:8], 16) / 255.0)
    except ValueError:
        return None
    return None


class _DiffColumn(object):
    '''并排 diff 的一栏：只读 GtkSourceView + 自绘行号 gutter。'''

    def __init__(self, rows, side, settings):
        self.side = side
        self.numbers = line_numbers(rows, side)
        self._foreground_color = None
        self._gutter_width = 0
        self._char_width = 0
        # 换行符号的 Gsk.RenderNode 缓存，key = (尺寸, 设备缩放)。
        self._newline_icon_nodes = dict()

        self.buffer = GtkSource.Buffer()
        self.buffer.set_style_scheme(ServiceLocator.get_style_scheme())
        self.buffer.connect('notify::style-scheme', self._on_scheme_changed)

        self.view = GtkSource.View.new_with_buffer(self.buffer)
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_monospace(True)
        # 行号由下面的自绘列负责；GtkSourceView 内置 gutter 必须关掉，
        # 否则会出现两套行号（内置的按 buffer 行号、且无法跳过空行）。
        self.view.set_show_line_numbers(False)
        self.view.set_left_margin(_VIEW_LEFT_MARGIN)
        self.view.set_right_margin(12)
        self.view.set_top_margin(6)
        self.view.set_bottom_margin(6)

        # 行距与源码区同一套设置：pixels_above/below 均分，换行续行同间距。
        spacing = settings.get_value('preferences', 'line_spacing')
        self.view.set_pixels_above_lines(spacing // 2)
        self.view.set_pixels_below_lines(spacing - spacing // 2)
        self.view.set_pixels_inside_wrap(spacing)

        self._fill_buffer(rows)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_child(self.view)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_vexpand(True)

        # 行号直接画在 view 的左内缩里（document/gutter 同款做法）：
        # 不另立行号列、不画任何背景，所以行号与正文之间不会出现色差或
        # 视图边界描边形成的竖线。内缩宽度由 update_gutter_width 按字体
        # 与行号位数设置。
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_halign(Gtk.Align.START)
        self.drawing_area.set_valign(Gtk.Align.FILL)
        self.drawing_area.set_size_request(0, -1)
        # 不拦截输入：滚轮/点击穿透到下面的滚动区。
        self.drawing_area.set_can_target(False)
        self.drawing_area.set_draw_func(self._draw_numbers)

        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.scrolled)
        self.overlay.set_vexpand(True)
        self.overlay.add_overlay(self.drawing_area)

        self.scrolled.get_vadjustment().connect(
            'value-changed', lambda adjustment: self.drawing_area.queue_draw())
        # 自动换行开关 / 窗口宽度变化会让整篇文档重排（adjustment changed），
        # 行号位置随之全变，必须同帧重绘。
        self.scrolled.get_vadjustment().connect(
            'changed', lambda adjustment: self.drawing_area.queue_draw())

        self.update_gutter_width()
        self.apply_wrap_mode(settings.get_value('preferences', 'enable_line_wrapping'))

    def _fill_buffer(self, rows):
        text_lines = []
        for old_line, new_line, kind in rows:
            line = old_line if self.side == 'old' else new_line
            text_lines.append('' if line is None else line)
        self.buffer.set_text('\n'.join(text_lines))

        tag_removed = self.buffer.create_tag('diff-removed', background=_COLOR_REMOVED)
        tag_added = self.buffer.create_tag('diff-added', background=_COLOR_ADDED)
        for offset, (old_line, new_line, kind) in enumerate(rows):
            if self.side == 'old' and kind in ('change', 'delete') and old_line is not None:
                tag = tag_removed
            elif self.side == 'new' and kind in ('change', 'insert') and new_line is not None:
                tag = tag_added
            else:
                continue
            # get_iter_at_line 返回 (found, iter) 元组，不是迭代器本身。
            _, line_start = self.buffer.get_iter_at_line(offset)
            line_end = line_start.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            self.buffer.apply_tag(tag, line_start, line_end)

    def apply_wrap_mode(self, wrap):
        '''跟随偏好设置里的自动换行：开启时按词换行并关掉横向滚动。'''
        if wrap:
            self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        else:
            self.view.set_wrap_mode(Gtk.WrapMode.NONE)
            self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    def update_gutter_width(self):
        '''按最长行号位数与当前字体宽度重算行号列宽。

        字体度量在 view realize 前可能还是系统默认值（见 document/gutter
        的同款注释），因此绘制时还会再调一次，宽度有变化才重排。'''
        char_width = FontManager.get_char_width(self.view)
        if char_width <= 0:
            return
        # 换行符号的水平定位与主编辑器 gutter 一样需要字符宽（右边缘让出
        # 一个字符），所以即使列宽没变也要记住最新度量。
        self._char_width = char_width
        digits = max((len(str(n)) for n in self.numbers if n is not None), default=1)
        width = digits * char_width + _GUTTER_PAD_START + _GUTTER_PAD_END
        if width == self._gutter_width:
            return
        self._gutter_width = width
        # 行号占用的就是 view 的左内缩：正文自动从行号右侧开始，
        # 两者之间不存在另一个控件的边界。
        self.view.set_left_margin(width)
        self.drawing_area.set_size_request(width, -1)

    def _on_scheme_changed(self, buffer, pspec):
        self._foreground_color = None
        self.drawing_area.queue_draw()

    def _foreground(self):
        '''行号颜色：用正文前景色，与主编辑器 gutter 一致。'''
        if self._foreground_color is not None:
            return self._foreground_color
        scheme = self.buffer.get_style_scheme()
        style = scheme.get_style('text') if scheme is not None else None
        fg = _parse_hex_color(style.props.foreground) if style is not None else None
        if fg is None:
            fg = ColorManager.get_ui_color('view_fg_color')
        self._foreground_color = fg
        return self._foreground_color

    def _draw_numbers(self, area, ctx, width, height):
        self.update_gutter_width()
        # 只画数字，不铺底色：背景就是 textview 自己的背景，任何色差都
        # 无处产生。
        Gdk.cairo_set_source_rgba(ctx, self._foreground())

        adjustment = self.scrolled.get_vadjustment()
        scroll_top = adjustment.get_value()
        # get_iter_location 给的是缓冲坐标（不含 view 的 top-margin），而
        # 正文实际绘制在 top-margin 之下，因此换算到控件坐标时要补上它，
        # 否则行号会比正文高出一截。
        top_margin = self.view.get_top_margin()
        total_lines = self.buffer.get_line_count()
        layout = Pango.Layout(self.view.get_pango_context())
        # 行号右对齐到「内缩宽 - 右侧留白」处，即紧贴正文左缘。
        layout.set_width(max(self._gutter_width - _GUTTER_PAD_END, 1) * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.RIGHT)

        # 视口顶所在行；行号 y 用该行首个视觉行的位置（自动换行的续行不重复
        # 编号，与主编辑器一致：续行位置画换行符号，见 draw_newline_symbol）。
        start = self.view.get_line_at_y(scroll_top).target_iter
        line = start.get_line()
        while line < total_lines:
            number = self.numbers[line]
            _, line_iter = self.buffer.get_iter_at_line(line)
            current = line_iter.copy()
            first_display_line = True
            past_bottom = False
            while True:
                location = self.view.get_iter_location(current)
                y = location.y - scroll_top + top_margin
                if y > height:
                    past_bottom = True
                    break
                if y + location.height >= 0:
                    if first_display_line:
                        if number is not None:
                            layout.set_text(str(number), -1)
                            _, text_height = layout.get_pixel_size()
                            ctx.move_to(0, y + (location.height - text_height) / 2)
                            PangoCairo.show_layout(ctx, layout)
                    else:
                        self._draw_newline_symbol(ctx, y, location.height)
                first_display_line = False
                # 同一逻辑行的下一个视觉行；跨行（换到下一逻辑行）即停。
                if not self.view.forward_display_line(current):
                    break
                if current.get_line() != line:
                    break
            if past_bottom:
                break
            line += 1

    def _draw_newline_symbol(self, ctx, y, line_height):
        '''自动换行产生的续行：在行号位置画 newline-symbolic（同主编辑器）。'''
        size = max(6, round(line_height * 0.3))
        node = self._get_newline_icon_node(size)
        if node is None:
            return
        # 与 document/gutter.draw_newline_symbol 完全同位：行号右边缘再
        # 让出一个字符宽，符号紧贴数字左侧，不越过数字右缘。
        char_width = self._char_width or size
        x = round(self._gutter_width - _GUTTER_PAD_END - char_width - size)
        ctx.save()
        ctx.translate(x, round(y + (line_height - size) / 3))
        node.draw(ctx)
        ctx.restore()

    def _get_newline_icon_node(self, size):
        '''把 symbolic 图标渲染成 Gsk.RenderNode 并按 (尺寸, 缩放) 缓存。

        与 document/gutter 的同名方法一致：按设备缩放倍数渲染纹理，
        HiDPI/分数缩放下不发虚；图标未安装时返回 None（不画符号）。'''
        scale_factor = self.view.get_scale_factor() or 1
        key = (size, scale_factor)
        node = self._newline_icon_nodes.get(key)
        if node is not None:
            return node
        display = self.view.get_display()
        theme = Gtk.IconTheme.get_for_display(display)
        paintable = theme.lookup_icon('newline-symbolic', None, size, scale_factor,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
        if paintable is None:
            return None
        pixel_size = size * scale_factor
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, pixel_size, pixel_size)
        node = snapshot.to_node()
        self._newline_icon_nodes[key] = node
        return node


class DiffWindow(Adw.Window):
    '''单文件并排 diff 窗口（标准窗口，右上角关闭按钮）。'''

    def __init__(self, path, old_text, new_text, parent=None):
        super().__init__()
        self.set_title(os.path.basename(path))
        # 查看器窗口豁免 §6.5 档位：代码审阅横向空间优先（980×620）。
        self.set_default_size(980, 620)
        if parent is not None:
            self.set_transient_for(parent)
            # 主窗口关闭时一并关掉，避免留下孤儿窗口。
            self.set_destroy_with_parent(True)

        self._settings = ServiceLocator.get_settings()
        self._settings_callback = None
        self._syncing = False

        self.toolbar_view = Adw.ToolbarView()
        self.headerbar = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(
            title=os.path.basename(path), subtitle=path)
        self.headerbar.set_title_widget(self.window_title)
        self.toolbar_view.add_top_bar(self.headerbar)

        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        # 顶部不留白：正文卡片直接顶到标题栏下沿。
        self.content.set_margin_top(0)
        self.content.set_margin_bottom(12)
        self.content.set_margin_start(12)
        self.content.set_margin_end(12)
        self.toolbar_view.set_content(self.content)
        self.set_content(self.toolbar_view)

        rows = align_lines(old_text, new_text)
        truncated = len(rows) > MAX_DIFF_LINES
        if truncated:
            rows = rows[:MAX_DIFF_LINES]

        if not rows:
            empty = Gtk.Label(label=_('No changes in this file.'))
            empty.add_css_class('dimmed')
            empty.set_vexpand(True)
            empty.set_valign(Gtk.Align.CENTER)
            self.content.append(empty)
        else:
            self._build_columns(rows)
            if truncated:
                note = Gtk.Label(label=_('Diff truncated: too many lines to display.'))
                note.add_css_class('dimmed')
                note.add_css_class('caption')
                note.set_halign(Gtk.Align.START)
                self.content.append(note)

        # Esc 关闭：标准窗口没有 Adw.Dialog 的默认 Esc 处理。
        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key_controller)

        self._settings_callback = self.on_settings_changed
        self._settings.connect('settings_changed', self._settings_callback)
        self.connect('close-request', self._on_close_request)

        _open_windows.add(self)

    def _build_columns(self, rows):
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class('diff-card')
        # GTK4 没有 overflow 属性，圆角裁剪靠 set_overflow(HIDDEN)
        # （与 .editor-card / .preview-card 同一做法）。
        self.card.set_overflow(Gtk.Overflow.HIDDEN)
        self.card.set_vexpand(True)

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_vexpand(True)
        self.old_column = _DiffColumn(rows, 'old', self._settings)
        self.new_column = _DiffColumn(rows, 'new', self._settings)
        self.paned.set_start_child(self.old_column.overlay)
        self.paned.set_end_child(self.new_column.overlay)
        self.paned.set_position(480)
        self.card.append(self.paned)
        self.content.append(self.card)

        self._sync_scrolling(self.old_column.scrolled, self.new_column.scrolled)

    def _sync_scrolling(self, first, second):
        '''两栏纵向滚动互跟随。

        以「视口顶所在的对齐行」为锚点映射：两侧 buffer 行一一对应，
        即使开启自动换行、两侧换行数量不同，也尽量让同一对齐行停在
        视口顶部（纯 set_value 同步在这种情况下会逐行漂移）。
        重入保护：set_value 会再发 value-changed，不拦就会两栏互相推。'''

        def follow(source, target):
            if self._syncing:
                return
            self._syncing = True
            try:
                source_adjustment = source.get_vadjustment()
                target_adjustment = target.get_vadjustment()
                y = source_adjustment.get_value()
                source_line = source.get_child().get_line_at_y(y)
                line = source_line.target_iter.get_line()
                # 保留行内偏移：锚点行在两侧的首视觉行高度可能不同（换行），
                # 但行内滚动量依然直觉一致。
                offset_in_line = y - source_line.line_top
                target_view = target.get_child()
                found, target_iter = target_view.get_buffer().get_iter_at_line(line)
                if not found:
                    return
                target_y = target_view.get_line_yrange(target_iter)[0]
                lower = target_adjustment.get_lower()
                upper = max(lower, target_adjustment.get_upper()
                            - target_adjustment.get_page_size())
                target_adjustment.set_value(
                    max(lower, min(target_y + offset_in_line, upper)))
            finally:
                self._syncing = False

        first.get_vadjustment().connect(
            'value-changed', lambda adjustment: follow(first, second))
        second.get_vadjustment().connect(
            'value-changed', lambda adjustment: follow(second, first))

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section != 'preferences':
            return
        if item == 'enable_line_wrapping':
            for column in self._columns():
                column.apply_wrap_mode(value)
        elif item == 'editor_style_scheme':
            scheme = ServiceLocator.get_style_scheme()
            for column in self._columns():
                column.buffer.set_style_scheme(scheme)

    def _columns(self):
        return [column for column in
                (getattr(self, 'old_column', None), getattr(self, 'new_column', None))
                if column is not None]

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_close_request(self, window):
        self._teardown()
        return False

    def _teardown(self):
        if self._settings_callback is not None:
            try:
                self._settings.disconnect('settings_changed', self._settings_callback)
            except (TypeError, KeyError, AttributeError):
                pass
            self._settings_callback = None
        _open_windows.discard(self)

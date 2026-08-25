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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib, Pango, PangoCairo

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager
from setzer.app.font_manager import FontManager


_SECTION_LEVELS = {
    'part': 0,
    'chapter': 1,
    'section': 2,
    'subsection': 3,
    'subsubsection': 4,
    'paragraph': 5,
    'subparagraph': 6,
}


class StickyScroll(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.source_view = document.source_view
        self.source_buffer = document.source_buffer
        self.view = document.view
        self.settings = ServiceLocator.get_settings()

        self.visible = self.settings.get_value('preferences', 'enable_sticky_scroll')
        self.current_sections = list()
        self._last_first_line = None
        self._next_section = None
        self._section_height = 28
        self._offset = 0
        self._margin_width = 0

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_draw_func(self.draw)
        self.drawing_area.set_valign(Gtk.Align.START)
        self.drawing_area.set_halign(Gtk.Align.FILL)
        self.drawing_area.set_vexpand(False)
        self.drawing_area.add_css_class('sticky-scroll')
        self.drawing_area.set_visible(self.visible)
        self.view.overlay.add_overlay(self.drawing_area)

        self.layout = Pango.Layout(self.source_view.get_pango_context())
        self.layout.set_ellipsize(Pango.EllipsizeMode.END)

        self._refresh_idle_id = None

        self.adjustment = self.view.scrolled_window.get_vadjustment()
        self.adjustment.connect('value-changed', self.on_scroll)
        # GTK 4: replaced 'size-allocate' signal with 'notify::width'/'notify::height'
        # as size-allocate no longer exists in GTK 4
        self.source_view.connect('notify::width', self.on_size_changed)
        self.source_view.connect('notify::height', self.on_size_changed)
        self.view.margin.connect('notify::width', self.on_margin_width_changed)

        self.document.parser.connect('finished_parsing', self.on_parser_update)
        self.document.connect('cursor_position_changed', self.on_cursor_change)
        self.document.code_folding.connect('folding_state_changed', self.on_folding_state_changed)
        self.settings.connect('settings_changed', self.on_settings_changed)
        self.source_buffer.connect('notify::style-scheme', self.on_scheme_changed)

        self._update_margin()
        self._update()

    def on_scheme_changed(self, buffer, pspec):
        self.drawing_area.queue_draw()

    def on_margin_width_changed(self, widget, pspec):
        self._update_margin()

    def _update_margin(self):
        self._margin_width = self.view.margin.get_width()
        self.drawing_area.set_margin_start(self._margin_width)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if item == 'enable_sticky_scroll':
            self.visible = value
            self.drawing_area.set_visible(value)
            if value:
                self._update()
            else:
                self.current_sections = list()
                self._next_section = None
                self._offset = 0
                self._update_height()

    def on_parser_update(self, parser):
        # symbols 结构变化：首行虽可能未变，但章节链已变，强制重算。
        self._last_first_line = None
        self._update()

    def on_cursor_change(self, document):
        self._schedule_refresh()

    def on_scroll(self, adjustment):
        self._schedule_refresh()

    def on_size_changed(self, widget, gparam):
        self._schedule_refresh()

    def on_folding_state_changed(self, code_folding):
        # 折叠状态变化：可见章节集合会变，强制重算。
        self._last_first_line = None
        self._schedule_refresh()

    def _schedule_refresh(self):
        if not self.visible:
            return
        if self._refresh_idle_id is None:
            self._refresh_idle_id = GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self):
        self._refresh_idle_id = None
        self._update()
        return False

    def _update(self):
        if not self.visible:
            return

        # 早退：滚动时绝大多数帧的"可见首行"并未改变，而 _find_current_sections
        # 对 symbols.blocks 是 O(n) 遍历、且 _is_section_visible 对每个 block 又是
        # O(n) 向上回溯父节点 → 大文档下每帧 O(n^2)。首行未变意味着当前章节链
        # 与下一章节都不会变，直接复用上次结果，只重算粘性偏移（动画依赖它）
        # 并重绘即可。parser/folding 变化时会把 _last_first_line 置 None 强制重算。
        first_line = self._get_first_visible_line()
        if (first_line is not None and first_line == self._last_first_line
                and self.current_sections is not None):
            self._compute_offset(self._next_section)
            self._update_height()
            self.drawing_area.queue_draw()
            return

        sections, next_section = self._find_current_sections()
        self._last_first_line = first_line
        self._compute_offset(next_section)
        self.current_sections = sections
        self._next_section = next_section
        self._update_height()
        self.drawing_area.queue_draw()

    def _find_current_sections(self):
        return self._find_sections_for_line(self._get_first_visible_line())

    def _find_sections_for_line(self, first_visible_line):
        '''Return sticky parent sections and the heading at ``first_visible_line``.

        Navigation needs the same answer before it changes the adjustment, so
        this calculation is deliberately independent of the current viewport.
        '''

        blocks = self.document.parser.symbols.get('blocks', list())
        if not blocks or first_visible_line is None:
            return list(), None

        active_sections = dict()
        next_section = None

        # 可见性一次预计算：原实现对每个章节调 _is_section_visible，其父链
        # 回溯每一步都全量扫描 blocks（O(S×B×d)），大文档打开/每次解析后
        # 重算可达秒级。_compute_section_visibility 用排序+栈扫描把全部
        # 章节的可见性一次算完，此处退化为 O(1) 查表。
        section_visibility = self._compute_section_visibility(blocks)

        for block in blocks:
            block_type = block[4]
            if block_type not in _SECTION_LEVELS:
                continue
            block_start_line = block[2]
            block_end_line = block[3]
            if block[1] is None:
                continue

            if not section_visibility.get(block[0], True):
                continue

            if block_start_line < first_visible_line <= block_end_line:
                level = _SECTION_LEVELS[block_type]
                if level not in active_sections or block_start_line > active_sections[level][2]:
                    title = block[5] if len(block) > 5 else ''
                    active_sections[level] = (block_type, title, block_start_line, block_end_line, level)

            elif block_start_line == first_visible_line:
                if block_end_line >= first_visible_line:
                    level = _SECTION_LEVELS[block_type]
                    title = block[5] if len(block) > 5 else ''
                    next_section = (block_type, title, block_start_line, block_end_line, level)

        result = list()
        for level in sorted(active_sections.keys()):
            result.append(active_sections[level])

        max_levels = 4
        if len(result) > max_levels:
            result = result[-max_levels:]
        elif len(result) == max_levels and next_section is not None:
            next_section_level = next_section[4]
            if next_section_level <= result[0][4]:
                next_section = None

        return result, next_section

    def get_navigation_reserved_height(self, line_number, extra_margin_lines=1):
        '''Return the navigation space reserved for sticky headers at a line.

        A target heading itself is not counted: when it becomes the first
        visible line, Sticky Scroll shows only its already-active parents.
        One additional text line is reserved by default below those headers,
        so reading-oriented navigation does not place content flush against
        the sticky area.  Returning zero while disabled, or when no headers
        are active, keeps ordinary top alignment unchanged.
        '''

        if not self.visible:
            return 0
        current_sections, _ = self._find_sections_for_line(line_number)
        if not current_sections:
            return 0
        line_height = FontManager.get_line_height(self.source_view)
        return (len(current_sections) + max(0, extra_margin_lines)) * line_height

    def _compute_section_visibility(self, blocks):
        '''一次性预计算所有章节块的可见性，返回 {offset_start: bool}。

        原 _is_section_visible 对每个章节沿父链回溯、每步全量扫描 blocks，
        整体 O(S×B×d)——6000 次调用实测 1.7s。这里按起始行排序后用栈式
        扫描构建父链映射（O(S log S)），再沿父链记忆化判定折叠可见性。

        父节点语义与原实现逐条一致：
          - 层级严格更低（candidate_level < level）
          - 起始行严格更小、结束行严格更大（真包含）
          - 在所有满足条件的块中取起始行最大者；起始行并列时取 blocks 列表
            中先出现者（原实现严格大于比较保留首个命中）。排序键
            (start_line, -index) 使同起始行块按列表索引降序入栈，栈顶向下
            扫描即先遇到索引小者，与原实现的平局规则一致。真实解析输出中
            同起始行意味着同行嵌套命令（blocks_list 以文档逆序构建），该
            规则同时保证更深的命令优先成为父节点。

        栈弹出条件 candidate[3] <= block[2] 安全：一旦某块的结束行不大于
        当前行起点，它也不可能包含任何后续块（后续块起始行只会更大）。
        同起始行的嵌套命令因「严格小于」条件互不为父，与原实现一致。
        '''
        sections = [b for b in blocks
                    if b[4] in _SECTION_LEVELS and b[1] is not None]
        # 稳定排序：(start_line, -原始索引)。见上方平局规则说明。
        order = {id(b): i for i, b in enumerate(sections)}
        sections.sort(key=lambda b: (b[2], -order[id(b)]))

        code_folding = getattr(self.document, 'code_folding', None)
        folded_regions = code_folding.folding_regions if code_folding is not None else dict()

        parent_of = dict()
        stack = list()
        for block in sections:
            while stack and stack[-1][3] <= block[2]:
                stack.pop()
            level = _SECTION_LEVELS[block[4]]
            for candidate in reversed(stack):
                if (_SECTION_LEVELS[candidate[4]] < level
                        and candidate[2] < block[2]
                        and candidate[3] > block[3]):
                    parent_of[block[0]] = candidate
                    break
            stack.append(block)

        def is_folded(block):
            region = folded_regions.get(block[0])
            return region['is_folded'] if region is not None else False

        visibility = dict()

        def compute(block):
            # 迭代爬父链：命中已算结果或遇到折叠块即停，整条链共享结论。
            chain = list()
            current = block
            while True:
                cached = visibility.get(current[0])
                if cached is not None:
                    result = cached
                    break
                chain.append(current)
                if is_folded(current):
                    result = False
                    break
                current = parent_of.get(current[0])
                if current is None:
                    result = True
                    break
            for seen in chain:
                visibility[seen[0]] = result

        for block in sections:
            if block[0] not in visibility:
                compute(block)
        return visibility

    def _is_section_visible(self, block):
        '''单块便捷查询：走与热路径相同的预计算逻辑。'''
        blocks = self.document.parser.symbols.get('blocks', list())
        if not blocks:
            return True
        return self._compute_section_visibility(blocks).get(block[0], True)

    def _get_first_visible_line(self):
        adjustment = self.adjustment
        scroll_top = adjustment.get_value()
        line_iter, _ = self.source_view.get_line_at_y(scroll_top)
        return line_iter.get_line()

    def _compute_offset(self, next_section):
        self._offset = 0
        if next_section is None:
            return

        try:
            next_start_iter = self.source_buffer.get_iter_at_line(next_section[2])[1]
            next_start_loc = self.source_view.get_iter_location(next_start_iter)
            scroll_top = self.adjustment.get_value()
            next_start_y = next_start_loc.y - scroll_top
            if -self._section_height < next_start_y < 0:
                self._offset = -next_start_y
            elif next_start_y >= 0:
                self._offset = 0
            else:
                self._offset = self._section_height
        except Exception:
            self._offset = 0

    def _update_height(self):
        # 行高与编辑区保持一致：直接用编辑器单行实际高度，不再额外放大
        # （原实现用 1.4 倍，导致 sticky 每行比编辑器高、错位）。
        char_height = FontManager.get_line_height(self.source_view)
        self._section_height = char_height
        count = len(self.current_sections)
        if self._next_section is not None and self._offset > 0:
            count += 1
        self.drawing_area.set_size_request(-1, count * self._section_height if count > 0 else 0)

    def draw(self, drawing_area, ctx, width, height):
        if not self.visible:
            return

        has_next = self._next_section is not None and self._offset > 0
        if not self.current_sections and not has_next:
            return

        ctx.save()
        ctx.rectangle(0, 0, width, height)
        ctx.clip()

        fg, bg = self._get_colors()

        y_cursor = 0.0
        for i, section in enumerate(self.current_sections):
            if y_cursor >= height:
                break

            entry_height = self._section_height

            alpha = 1.0
            if y_cursor + entry_height > height:
                alpha = max(0.0, (height - y_cursor) / entry_height)
                entry_height = height - y_cursor

            if entry_height > 0:
                self._draw_section(ctx, section, 0, y_cursor, width, entry_height, fg, bg, alpha, i > 0)

            y_cursor += self._section_height

        if has_next and self._offset > 0 and y_cursor < height:
            entry_height = self._offset
            alpha = self._offset / self._section_height if self._section_height > 0 else 1.0

            if y_cursor + entry_height > height:
                alpha = max(0.0, (height - y_cursor) / self._section_height)
                entry_height = height - y_cursor

            if entry_height > 0:
                self._draw_section(ctx, self._next_section, 0, y_cursor, width, entry_height, fg, bg, alpha, len(self.current_sections) > 0)

        # 最底部一条分割线：仅整体底边绘制，用于与下方编辑区内容分隔，
        # 行间不画（已在 _draw_section 中移除）。用前景色做柔和细分隔。
        if height > 0:
            border_color = Gdk.RGBA()
            if fg is not None:
                border_color = Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=fg.alpha)
            border_color.alpha = 0.3
            Gdk.cairo_set_source_rgba(ctx, border_color)
            ctx.rectangle(0, height - 1, width, 1)
            ctx.fill()

        ctx.restore()

    def _draw_section(self, ctx, section, x, y, width, height, fg, bg, alpha, is_parent):
        section_type, title, _, _, level = section

        # 不透明背景：直接用编辑区背景色填满，不透明度恒为 1.0，
        # 不再区分父/子级做 0.85 半透明，也不再随滚动淡出而透明。
        bg_color = Gdk.RGBA()
        if bg is not None:
            bg_color = Gdk.RGBA(red=bg.red, green=bg.green, blue=bg.blue, alpha=1.0)
        Gdk.cairo_set_source_rgba(ctx, bg_color)
        ctx.rectangle(x, y, width, height)
        ctx.fill()

        font_desc = self.source_view.get_pango_context().get_font_description()

        type_label = self._get_type_label(section_type)
        ctx_type = Pango.Layout(self.source_view.get_pango_context())
        if font_desc is not None:
            ctx_type.set_font_description(font_desc)
        ctx_type.set_text(type_label, -1)
        ctx_type.set_alignment(Pango.Alignment.LEFT)
        type_rect = ctx_type.get_extents().logical_rect
        type_height = type_rect.height / Pango.SCALE
        type_width = type_rect.width / Pango.SCALE

        type_color = Gdk.RGBA()
        if fg is not None:
            type_color = Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=fg.alpha)
        type_color.alpha = 0.5 * alpha
        Gdk.cairo_set_source_rgba(ctx, type_color)

        text_y = y + (height - type_height) / 2
        ctx.move_to(x + 12, text_y)
        PangoCairo.show_layout(ctx, ctx_type)

        text_color = Gdk.RGBA()
        if fg is not None:
            text_color = Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=fg.alpha)
        text_color.alpha = alpha
        Gdk.cairo_set_source_rgba(ctx, text_color)

        ctx_text = Pango.Layout(self.source_view.get_pango_context())
        if font_desc is not None:
            ctx_text.set_font_description(font_desc)
        display_title = title.strip() if title else '(unnamed)'
        ctx_text.set_text(display_title, -1)
        ctx_text.set_alignment(Pango.Alignment.LEFT)
        max_text_width = width - type_width - 28
        if max_text_width > 0:
            ctx_text.set_width(max_text_width * Pango.SCALE)
            ctx_text.set_ellipsize(Pango.EllipsizeMode.END)
        ctx.move_to(x + 12 + type_width + 4, text_y)
        PangoCairo.show_layout(ctx, ctx_text)

    def _get_type_label(self, section_type):
        labels = {
            'part': 'Part',
            'chapter': 'Chapter',
            'section': 'Section',
            'subsection': 'Subsection',
            'subsubsection': 'Subsubsection',
            'paragraph': 'Paragraph',
            'subparagraph': 'Subparagraph',
        }
        return labels.get(section_type, section_type)

    def _get_colors(self):
        scheme = self.source_buffer.get_style_scheme()
        style = scheme.get_style('text') if scheme else None

        def _parse_hex(s):
            if not s:
                return None
            s = s.strip().lstrip('#')
            if len(s) == 6:
                return Gdk.RGBA(red=int(s[0:2], 16) / 255.0,
                               green=int(s[2:4], 16) / 255.0,
                               blue=int(s[4:6], 16) / 255.0, alpha=1.0)
            elif len(s) == 8:
                return Gdk.RGBA(red=int(s[0:2], 16) / 255.0,
                               green=int(s[2:4], 16) / 255.0,
                               blue=int(s[4:6], 16) / 255.0,
                               alpha=int(s[6:8], 16) / 255.0)
            return None

        fg = _parse_hex(style.props.foreground) if style else None
        bg = _parse_hex(style.props.background) if style else None
        if fg is None:
            fg = ColorManager.get_ui_color('view_fg_color')
        if bg is None:
            bg = ColorManager.get_ui_color('view_bg_color')
        return fg, bg

    def shutdown(self):
        if self._refresh_idle_id is not None:
            GLib.source_remove(self._refresh_idle_id)
            self._refresh_idle_id = None

        try:
            self.settings.disconnect('settings_changed', self.on_settings_changed)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.document.parser.disconnect('finished_parsing', self.on_parser_update)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.document.disconnect('cursor_position_changed', self.on_cursor_change)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.document.code_folding.disconnect('folding_state_changed', self.on_folding_state_changed)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.view.margin.disconnect_by_func(self.on_margin_width_changed)
        except (TypeError, KeyError, AttributeError):
            pass

        try:
            self.view.overlay.remove_overlay(self.drawing_area)
        except Exception:
            pass

        self.drawing_area = None

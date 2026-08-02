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
        self._update()

    def on_cursor_change(self, document):
        self._schedule_refresh()

    def on_scroll(self, adjustment):
        self._schedule_refresh()

    def on_size_changed(self, widget, gparam):
        self._schedule_refresh()

    def on_folding_state_changed(self, code_folding):
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
        sections, next_section = self._find_current_sections()
        self._compute_offset(next_section)
        self.current_sections = sections
        self._next_section = next_section
        self._update_height()
        self.drawing_area.queue_draw()

    def _find_current_sections(self):
        blocks = self.document.parser.symbols.get('blocks', list())
        if not blocks:
            return list(), None

        first_visible_line = self._get_first_visible_line()
        if first_visible_line is None:
            return list(), None

        active_sections = dict()
        next_section = None

        for block in blocks:
            block_type = block[4]
            if block_type not in _SECTION_LEVELS:
                continue
            block_start_line = block[2]
            block_end_line = block[3]
            if block[1] is None:
                continue

            if not self._is_section_visible(block):
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

    def _is_section_visible(self, block):
        level = _SECTION_LEVELS.get(block[4])
        if level is None:
            return False

        if self._is_block_folded(block):
            return False

        current_block = block
        current_level = level
        blocks = self.document.parser.symbols.get('blocks', list())
        while True:
            best_parent = None
            for candidate in blocks:
                candidate_type = candidate[4]
                if candidate_type not in _SECTION_LEVELS:
                    continue
                candidate_level = _SECTION_LEVELS[candidate_type]
                if candidate_level >= current_level:
                    continue
                if candidate[2] < current_block[2] and candidate[3] > current_block[3]:
                    if best_parent is None or candidate[2] > best_parent[2]:
                        best_parent = candidate
            if best_parent is None:
                break
            if self._is_block_folded(best_parent):
                return False
            current_block = best_parent
            current_level = _SECTION_LEVELS[best_parent[4]]

        return True

    def _is_block_folded(self, block):
        if not hasattr(self.document, 'code_folding'):
            return False
        if block[0] in self.document.code_folding.folding_regions:
            return self.document.code_folding.folding_regions[block[0]]['is_folded']
        return False

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
        char_height = FontManager.get_line_height(self.source_view)
        self._section_height = max(char_height * 1.4, 24)
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

        ctx.restore()

    def _draw_section(self, ctx, section, x, y, width, height, fg, bg, alpha, is_parent):
        section_type, title, _, _, level = section

        bg_color = Gdk.RGBA()
        if bg is not None:
            bg_color = Gdk.RGBA(red=bg.red, green=bg.green, blue=bg.blue, alpha=bg.alpha)
        bg_color.alpha = (0.85 if is_parent else 1.0) * alpha
        Gdk.cairo_set_source_rgba(ctx, bg_color)
        ctx.rectangle(x, y, width, height)
        ctx.fill()

        border_color = Gdk.RGBA()
        if fg is not None:
            border_color = Gdk.RGBA(red=fg.red, green=fg.green, blue=fg.blue, alpha=fg.alpha)
        border_color.alpha = 0.15 * alpha
        Gdk.cairo_set_source_rgba(ctx, border_color)
        ctx.rectangle(x, y + height - 1, width, 1)
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

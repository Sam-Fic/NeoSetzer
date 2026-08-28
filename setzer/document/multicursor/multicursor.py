#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, GtkSource, Gdk, GLib, GObject, Pango

from setzer.app.color_manager import ColorManager


class MultiCursor(object):
    """Manages multiple cursors and selections in a GtkSourceView.

    The primary cursor is the one native to GtkTextBuffer (insert mark).
    Additional cursors are tracked via Gtk.TextMarks stored in
    self.cursors as (cursor_mark, anchor_mark or None, selection tag or
    None). A non-None anchor marks a selection ranging from anchor to
    cursor; the tag highlights that range in the view.

    Cursor marks use right gravity so that text inserted exactly at the
    cursor position pushes the mark behind the new text (the cursor
    follows typing). Anchor marks use left gravity so a selection start
    stays put when its replacement text is inserted.

    Supports:
    - Alt+Click to add/remove a cursor (document controller claims the
      click so the native caret does not move)
    - Alt+Drag column selection
    - Ctrl+D / Ctrl+Shift+L select next/all occurrences
    - Ctrl+Alt+Up/Down add cursor on line above/below
    - Multi-cursor insert (incl. newline), backspace/delete and indent
    """

    def __init__(self, document):
        self.document = document
        self.buffer = document.source_buffer
        self.view = document.source_view

        # Additional cursors: (cursor_mark, anchor_mark or None, tag or None)
        # The primary cursor is NOT in this list.
        self.cursors = []
        self._tag_counter = 0

        # Whether column selection mode is active. In column mode edits
        # apply to the additional cursors only — the primary caret sits
        # at a corner of the rectangle and must not be edited twice.
        self._column_mode = False

        # Create a drawing area for rendering additional cursors
        self._draw_area = Gtk.DrawingArea()
        self._draw_area.set_css_classes(['multicursor-overlay'])
        self._draw_area.set_hexpand(True)
        self._draw_area.set_vexpand(True)
        self._draw_area.set_can_focus(False)
        self._draw_area.set_can_target(False)  # Don't receive events
        self._draw_area.set_draw_func(self._on_draw, None)

        # Try to add the drawing area to the overlay
        self._overlay = None
        parent = self.view.get_parent()
        while parent is not None:
            if isinstance(parent, Gtk.Overlay):
                parent.add_overlay(self._draw_area)
                self._overlay = parent
                break
            parent = parent.get_parent()

        # Redraw triggers: additional cursor screen positions change on
        # scroll, resize and any buffer modification (marks shift with
        # text). Without these the overlay keeps stale cursor paintings.
        self._scroll_handlers = []
        scrolled = self.view.get_parent()
        if isinstance(scrolled, Gtk.ScrolledWindow):
            for adjustment in (scrolled.get_hadjustment(), scrolled.get_vadjustment()):
                handler = adjustment.connect('value-changed', self._queue_draw)
                self._scroll_handlers.append((adjustment, handler))
        self._size_handlers = []
        for prop in ('notify::width', 'notify::height'):
            handler = self.view.connect(prop, self._queue_draw)
            self._size_handlers.append(handler)

        # Track buffer modifications to refresh the overlay (marks are
        # shifted automatically by GTK; only the painting needs refresh).
        self._changed_handler = self.buffer.connect('changed', self._queue_draw)

        # Suppress handler during multi-cursor edits
        self._suppress_handlers = False

        # Track search context for select-next-occurrence
        self._search_context = None

    def shutdown(self):
        """Clean up signals and marks."""
        self._clear_all_cursors()

        # Remove drawing area from parent
        if self._draw_area is not None:
            if self._overlay is not None:
                try:
                    self._overlay.remove_overlay(self._draw_area)
                except Exception:
                    pass
            self._draw_area = None
            self._overlay = None

        for adjustment, handler in self._scroll_handlers:
            try:
                adjustment.disconnect(handler)
            except Exception:
                pass
        self._scroll_handlers = []
        for handler in self._size_handlers:
            try:
                self.view.disconnect(handler)
            except Exception:
                pass
        self._size_handlers = []
        if self._changed_handler:
            try:
                self.buffer.disconnect(self._changed_handler)
            except Exception:
                pass
            self._changed_handler = None

    def _queue_draw(self, *args):
        if self._draw_area is not None:
            self._draw_area.queue_draw()

    # --- Cursor management ---

    def get_cursor_count(self):
        """Total cursor count including primary."""
        return 1 + len(self.cursors)

    def has_multiple_cursors(self):
        return len(self.cursors) > 0

    def is_column_mode(self):
        return self._column_mode

    def clear_all(self):
        """Remove all additional cursors and selections."""
        self._clear_all_cursors()

    def _clear_all_cursors(self):
        """Internal: remove all additional cursor marks and tags."""
        for cursor_mark, anchor_mark, tag in self.cursors:
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark is not None:
                self.buffer.delete_mark(anchor_mark)
            if tag is not None:
                self._remove_tag(tag)
        self.cursors.clear()

        self._column_mode = False
        self._queue_draw()

    def _cursor_offsets(self):
        """Offsets of all additional cursors (selection ends included)."""
        offsets = set()
        for cursor_mark, anchor_mark, _tag in self.cursors:
            offsets.add(self.buffer.get_iter_at_mark(cursor_mark).get_offset())
            if anchor_mark is not None:
                offsets.add(self.buffer.get_iter_at_mark(anchor_mark).get_offset())
        return offsets

    def add_cursor_at_iter(self, iter_pos, _allow_primary_overlap=False):
        """Add an additional cursor at the given position.

        Skips positions already occupied by another cursor (including the
        primary caret — an overlapping cursor would double-insert text),
        unless _allow_primary_overlap is set (column selection edits skip
        the primary, so the rectangle corner must still get its cursor).
        """
        offset = iter_pos.get_offset()
        if offset in self._cursor_offsets():
            return
        if not _allow_primary_overlap:
            primary_offset = self.buffer.get_iter_at_mark(
                self.buffer.get_insert()).get_offset()
            if offset == primary_offset:
                return
        # Right gravity: typing at the cursor pushes the mark behind the
        # inserted text.
        mark = self.buffer.create_mark(None, iter_pos, False)
        self.cursors.append((mark, None, None))
        self._queue_draw()

    def add_cursor_with_selection(self, start_iter, end_iter):
        """Add an additional cursor with a selection range."""
        # Ensure start is before end
        if start_iter.compare(end_iter) > 0:
            start_iter, end_iter = end_iter, start_iter
        if start_iter.get_offset() == end_iter.get_offset():
            self.add_cursor_at_iter(start_iter)
            return

        cursor_mark = self.buffer.create_mark(None, end_iter, False)
        anchor_mark = self.buffer.create_mark(None, start_iter, True)
        tag = self._add_selection_tag(start_iter, end_iter)
        self.cursors.append((cursor_mark, anchor_mark, tag))
        self._queue_draw()

    def remove_last_cursor(self):
        """Remove the most recently added cursor."""
        if self.cursors:
            cursor_mark, anchor_mark, tag = self.cursors.pop()
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark is not None:
                self.buffer.delete_mark(anchor_mark)
            if tag is not None:
                self._remove_tag(tag)
            self._queue_draw()

    def remove_cursor_at_offset(self, offset, tolerance=2):
        """Remove cursor nearest to the given offset (within tolerance chars)."""
        best_idx = -1
        best_dist = tolerance + 1
        for i, (cursor_mark, _anchor, _tag) in enumerate(self.cursors):
            mark_offset = self.buffer.get_iter_at_mark(cursor_mark).get_offset()
            dist = abs(mark_offset - offset)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_idx >= 0:
            cursor_mark, anchor_mark, tag = self.cursors.pop(best_idx)
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark is not None:
                self.buffer.delete_mark(anchor_mark)
            if tag is not None:
                self._remove_tag(tag)
            self._queue_draw()

    def add_cursors_column(self, anchor_iter, active_iter, additive=False):
        """Create column selection (one cursor per line in range)."""
        if not additive:
            self._clear_all_cursors()
        self._column_mode = True

        start_line = min(anchor_iter.get_line(), active_iter.get_line())
        end_line = max(anchor_iter.get_line(), active_iter.get_line())
        anchor_col = anchor_iter.get_line_offset()
        active_col = active_iter.get_line_offset()
        col_start = min(anchor_col, active_col)
        col_end = max(anchor_col, active_col)

        existing = self._cursor_offsets()
        for line_num in range(start_line, end_line + 1):
            found, line_iter = self.buffer.get_iter_at_line(line_num)
            if not found:
                continue

            # Calculate column positions on this line
            line_end = line_iter.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            line_length = line_end.get_offset() - line_iter.get_offset()

            sel_start_col = min(col_start, line_length)
            sel_end_col = min(col_end, line_length)

            if sel_start_col >= sel_end_col:
                # Empty selection: just a cursor at col_start
                cursor_iter = line_iter.copy()
                cursor_iter.forward_chars(sel_start_col)
                if cursor_iter.get_offset() not in existing:
                    self.add_cursor_at_iter(cursor_iter, _allow_primary_overlap=True)
                    existing.add(cursor_iter.get_offset())
            else:
                start_iter = line_iter.copy()
                start_iter.forward_chars(sel_start_col)
                end_iter = line_iter.copy()
                end_iter.forward_chars(sel_end_col)
                self.add_cursor_with_selection(start_iter, end_iter)

        self._queue_draw()

    def add_cursor_above(self):
        """Add cursor on the line above each existing cursor."""
        self._add_cursor_vertical(-1)

    def add_cursor_below(self):
        """Add cursor on the line below each existing cursor."""
        self._add_cursor_vertical(1)

    def _add_cursor_vertical(self, direction):
        """Add a cursor one line up (-1) or down (+1) for every cursor."""
        line_count = self.buffer.get_line_count()
        new_iters = []

        # Primary cursor first
        positions = [self.buffer.get_iter_at_mark(self.buffer.get_insert())]
        positions += [self.buffer.get_iter_at_mark(cursor_mark)
                      for cursor_mark, _anchor, _tag in self.cursors]

        existing = self._cursor_offsets()
        for iter_pos in positions:
            target_line = iter_pos.get_line() + direction
            if target_line < 0 or target_line >= line_count:
                continue
            found, new_iter = self.buffer.get_iter_at_line(target_line)
            if not found:
                continue
            new_iter.set_line_offset(iter_pos.get_line_offset())
            # Clamp to line length
            line_end = new_iter.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            if new_iter.get_offset() > line_end.get_offset():
                new_iter = line_end
            if new_iter.get_offset() not in existing:
                new_iters.append(new_iter)
                existing.add(new_iter.get_offset())

        for iter_pos in new_iters:
            self.add_cursor_at_iter(iter_pos)

    # --- Select next/all occurrence ---

    def _ensure_search_context(self):
        if self._search_context is None:
            self._search_context = GtkSource.SearchContext.new(
                self.buffer, GtkSource.SearchSettings())
            self._search_context.set_highlight(False)
        settings = self._search_context.get_settings()
        settings.set_case_sensitive(False)
        settings.set_at_word_boundaries(False)
        settings.set_regex_enabled(False)
        return self._search_context

    def select_next_occurrence(self):
        """Select the next occurrence of the selected text or word under cursor.

        The first invocation (no selection, no additional cursors) selects
        the word under the caret, VS Code style. Subsequent invocations
        add the next match after the rightmost selection, wrapping around
        at the end of the buffer. Returns True if a selection was made.
        """
        if not self.buffer.get_has_selection():
            if self.has_multiple_cursors():
                # Additional cursors exist but primary selection is gone —
                # nothing meaningful to extend.
                return False
            caret = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            bounds = self._get_word_bounds_at_iter(caret)
            if bounds is None:
                return False
            start_iter, end_iter = bounds
            self.buffer.select_range(end_iter, start_iter)
            return True

        bounds = self.buffer.get_selection_bounds()
        text = self.buffer.get_text(bounds[0], bounds[1], True)
        if not text or not text.strip():
            return False

        context = self._ensure_search_context()
        context.get_settings().set_search_text(text)

        # Start the search after the rightmost selected range.
        search_offset = max(bounds[0].get_offset(), bounds[1].get_offset())
        for cursor_mark, anchor_mark, _tag in self.cursors:
            search_offset = max(
                search_offset,
                self.buffer.get_iter_at_mark(cursor_mark).get_offset())
            if anchor_mark is not None:
                search_offset = max(
                    search_offset,
                    self.buffer.get_iter_at_mark(anchor_mark).get_offset())

        # Skip matches already covered by an existing selection; wrap
        # around once at the buffer end.
        attempts = len(self.cursors) + 2
        search_iter = self.buffer.get_iter_at_offset(search_offset)
        wrapped = False
        while attempts > 0:
            attempts -= 1
            found, match_start, match_end, did_wrap = context.forward(search_iter)
            if not found and not wrapped:
                wrapped = True
                search_iter = self.buffer.get_start_iter()
                continue
            if not found:
                return False
            if not self._is_range_selected(match_start.get_offset(),
                                           match_end.get_offset()):
                self.add_cursor_with_selection(match_start, match_end)
                return True
            search_iter = match_end

        return False

    def select_all_occurrences(self):
        """Select all occurrences of the selected text or word under cursor."""
        if self.buffer.get_has_selection():
            bounds = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(bounds[0], bounds[1], True)
        else:
            caret = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            text = self._get_word_at_iter(caret)
        if not text or not text.strip():
            return False

        context = self._ensure_search_context()
        context.get_settings().set_search_text(text)

        # Clear existing additional cursors
        self._clear_all_cursors()

        # First, set primary to the first occurrence
        search_iter = self.buffer.get_start_iter()
        primary_set = False

        while True:
            found, match_start, match_end, _wrapped = context.forward(search_iter)
            if not found:
                break

            if not primary_set:
                # Set primary to first match
                self.buffer.select_range(match_end, match_start)
                primary_set = True
            else:
                # Add as additional selection
                self.add_cursor_with_selection(match_start, match_end)

            # Move past this match
            search_iter = match_end.copy()
            if not search_iter.forward_char():
                break

        return primary_set

    def _is_range_selected(self, start_offset, end_offset):
        """True if the range overlaps the primary or any additional selection."""
        if self.buffer.get_has_selection():
            sel_start, sel_end = self.buffer.get_selection_bounds()
            if start_offset < sel_end.get_offset() and end_offset > sel_start.get_offset():
                return True
        for cursor_mark, anchor_mark, _tag in self.cursors:
            if anchor_mark is None:
                continue
            a = self.buffer.get_iter_at_mark(anchor_mark).get_offset()
            b = self.buffer.get_iter_at_mark(cursor_mark).get_offset()
            sel_start, sel_end = min(a, b), max(a, b)
            if start_offset < sel_end and end_offset > sel_start:
                return True
        return False

    def _get_search_text(self):
        """Get the text to search: selection or word under cursor."""
        if self.buffer.get_has_selection():
            bounds = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(bounds[0], bounds[1], True)
            if text and text.strip():
                return text
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return self._get_word_at_iter(cursor)

    def _get_word_bounds_at_iter(self, iter_pos):
        """Return (start_iter, end_iter) of the word at iter_pos, or None."""
        start = iter_pos.copy()
        end = iter_pos.copy()

        # Move backward to find word start
        while start.get_offset() > 0:
            prev = start.copy()
            prev.backward_char()
            ch = prev.get_char()
            if ch.isalnum() or ch == '_':
                start = prev
            else:
                break

        # Move forward to find word end
        while True:
            ch = end.get_char()
            if ch.isalnum() or ch == '_':
                if not end.forward_char():
                    break
            else:
                break

        if start.get_offset() < end.get_offset():
            return start, end
        return None

    def _get_word_at_iter(self, iter_pos):
        """Extract the word at the given iterator position."""
        bounds = self._get_word_bounds_at_iter(iter_pos)
        if bounds is None:
            return None
        return self.buffer.get_text(bounds[0], bounds[1], True)

    # --- Edit operations (called from controller) ---

    def _collect_edit_ranges(self, include_primary):
        """Collect (start_offset, end_offset) edit ranges for all cursors.

        Overlapping/identical ranges are merged so duplicate cursors do
        not double-edit the same span.
        """
        ranges = []
        if include_primary:
            if self.buffer.get_has_selection():
                sel_bounds = self.buffer.get_selection_bounds()
                ranges.append((sel_bounds[0].get_offset(),
                               sel_bounds[1].get_offset()))
            else:
                offset = self.buffer.get_iter_at_mark(
                    self.buffer.get_insert()).get_offset()
                ranges.append((offset, offset))

        for cursor_mark, anchor_mark, _tag in self.cursors:
            cursor_offset = self.buffer.get_iter_at_mark(cursor_mark).get_offset()
            if anchor_mark is not None:
                anchor_offset = self.buffer.get_iter_at_mark(anchor_mark).get_offset()
                ranges.append((min(cursor_offset, anchor_offset),
                               max(cursor_offset, anchor_offset)))
            else:
                ranges.append((cursor_offset, cursor_offset))

        # Merge overlapping ranges (sort ascending, fold neighbours)
        ranges.sort()
        merged = []
        for start, end in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    def _consume_selections(self):
        """Drop anchors and tags of selections that were just replaced.

        After typing over a selection the selection is gone (VS Code
        behaviour): the cursor stays behind the inserted text, ready for
        further appending.
        """
        new_cursors = []
        for cursor_mark, anchor_mark, tag in self.cursors:
            if anchor_mark is not None:
                self.buffer.delete_mark(anchor_mark)
                if tag is not None:
                    self._remove_tag(tag)
                new_cursors.append((cursor_mark, None, None))
            else:
                new_cursors.append((cursor_mark, anchor_mark, tag))
        self.cursors = new_cursors

    def handle_insert(self, text):
        """Insert text at all cursor positions. Returns True if handled."""
        if not self.has_multiple_cursors() and not self._column_mode:
            return False

        self._suppress_handlers = True
        try:
            self.buffer.begin_user_action()

            # In column mode the primary caret sits at a rectangle corner
            # already covered by the column cursors — editing it too would
            # duplicate input at that corner.
            ranges = self._collect_edit_ranges(include_primary=not self._column_mode)

            # Apply from the end so earlier offsets stay valid.
            for start_offset, end_offset in reversed(ranges):
                if start_offset != end_offset:
                    start_iter = self.buffer.get_iter_at_offset(start_offset)
                    end_iter = self.buffer.get_iter_at_offset(end_offset)
                    self.buffer.delete(start_iter, end_iter)
                insert_iter = self.buffer.get_iter_at_offset(start_offset)
                self.buffer.insert(insert_iter, text)

            self.buffer.end_user_action()
        finally:
            self._suppress_handlers = False

        self._consume_selections()
        self._queue_draw()
        return True

    def handle_delete(self, delete_type):
        """Handle delete/backspace at all cursor positions.

        delete_type: 'backspace' or 'delete'
        Returns True if handled.
        """
        if not self.has_multiple_cursors() and not self._column_mode:
            return False

        self._suppress_handlers = True
        try:
            self.buffer.begin_user_action()

            ranges = []
            include_primary = not self._column_mode

            if include_primary:
                if self.buffer.get_has_selection():
                    sel_bounds = self.buffer.get_selection_bounds()
                    ranges.append((min(sel_bounds[0].get_offset(),
                                       sel_bounds[1].get_offset()),
                                   max(sel_bounds[0].get_offset(),
                                       sel_bounds[1].get_offset())))
                else:
                    primary_iter = self.buffer.get_iter_at_mark(
                        self.buffer.get_insert())
                    ranges.append(self._adjacent_char_range(
                        primary_iter, delete_type))

            for cursor_mark, anchor_mark, _tag in self.cursors:
                cursor_iter = self.buffer.get_iter_at_mark(cursor_mark)
                if anchor_mark is not None:
                    anchor_offset = self.buffer.get_iter_at_mark(anchor_mark).get_offset()
                    cursor_offset = cursor_iter.get_offset()
                    ranges.append((min(cursor_offset, anchor_offset),
                                   max(cursor_offset, anchor_offset)))
                else:
                    ranges.append(self._adjacent_char_range(
                        cursor_iter, delete_type))

            # Drop empty (boundary) ranges, merge overlaps
            ranges = [(s, e) for s, e in ranges if s < e]
            ranges.sort()
            merged = []
            for start, end in ranges:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            for start, end in reversed(merged):
                start_iter = self.buffer.get_iter_at_offset(start)
                end_iter = self.buffer.get_iter_at_offset(end)
                self.buffer.delete(start_iter, end_iter)

            self.buffer.end_user_action()
        finally:
            self._suppress_handlers = False

        self._consume_selections()
        self._queue_draw()
        return True

    def _adjacent_char_range(self, iter_pos, delete_type):
        """Range of the char before (backspace) or after (delete) iter_pos.

        Returns an empty range at buffer boundaries.
        """
        offset = iter_pos.get_offset()
        if delete_type == 'backspace':
            if offset > 0:
                return (offset - 1, offset)
            return (offset, offset)
        next_iter = iter_pos.copy()
        if next_iter.forward_char():
            return (offset, offset + 1)
        return (offset, offset)

    # --- Signal handlers ---

    # --- Drawing ---

    def _get_cursor_color(self):
        """Cursor colour: GtkSourceView cursor colour with fallbacks."""
        try:
            color = self.view.get_cursor_color()
            if color is not None and color.alpha > 0.01:
                return color
        except Exception:
            pass
        try:
            return ColorManager.get_ui_color('view_fg_color')
        except Exception:
            fallback = Gdk.RGBA()
            fallback.red = fallback.green = fallback.blue = 0.0
            fallback.alpha = 1.0
            return fallback

    def _on_draw(self, widget, cr, width, height, user_data):
        """Draw additional cursors on the overlay drawing area.

        get_iter_location returns buffer coordinates; convert them to
        view widget coordinates with buffer_to_window_coords, then
        translate into the drawing area's coordinate space.
        """
        if not self.has_multiple_cursors():
            return

        color = self._get_cursor_color()

        for cursor_mark, _anchor_mark, _tag in self.cursors:
            cursor_iter = self.buffer.get_iter_at_mark(cursor_mark)
            cursor_rect = self.view.get_iter_location(cursor_iter)

            try:
                view_x, view_y = self.view.buffer_to_window_coords(
                    Gtk.TextWindowType.WIDGET, cursor_rect.x, cursor_rect.y)
                translated = self.view.translate_coordinates(
                    widget, view_x, view_y)
                if translated is None:
                    continue
                draw_x, draw_y = translated
            except Exception:
                continue

            # Clamp to drawing area bounds
            if draw_x < -20 or draw_x > width + 20 or draw_y < -20 or draw_y > height + 20:
                continue

            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            cr.set_line_width(2.0)
            cr.move_to(draw_x + 0.5, draw_y)
            cr.line_to(draw_x + 0.5, draw_y + cursor_rect.height)
            cr.stroke()

    # --- Selection tags ---

    def _add_selection_tag(self, start_iter, end_iter):
        """Apply a TextTag highlighting an additional selection."""
        if start_iter.get_offset() >= end_iter.get_offset():
            return None

        self._tag_counter += 1
        tag_name = 'mc-selection-{}'.format(self._tag_counter)
        # 读主题 accent 色（明暗/高对比主题下自动跟随），不写死 Adwaita 蓝。
        tag = self.buffer.create_tag(
            tag_name,
            background=ColorManager.get_ui_color_string('accent_bg_color'),
            background_set=True)
        self.buffer.apply_tag(tag, start_iter, end_iter)
        return tag

    def _remove_tag(self, tag):
        try:
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            self.buffer.remove_tag(tag, start, end)
            self.buffer.get_tag_table().remove(tag)
        except Exception:
            pass

    # --- Column selection helpers ---

    def get_column_selections(self):
        """Return list of (line_num, start_col, end_col) for column selections."""
        if not self._column_mode:
            return []

        result = []
        for cursor_mark, anchor_mark, _tag in self.cursors:
            cursor_iter = self.buffer.get_iter_at_mark(cursor_mark) if cursor_mark else None
            anchor_iter = self.buffer.get_iter_at_mark(anchor_mark) if anchor_mark else None

            if cursor_iter and anchor_iter:
                line_num = cursor_iter.get_line()
                start_col = min(cursor_iter.get_line_offset(), anchor_iter.get_line_offset())
                end_col = max(cursor_iter.get_line_offset(), anchor_iter.get_line_offset())
                result.append((line_num, start_col, end_col))
            elif cursor_iter:
                line_num = cursor_iter.get_line()
                col = cursor_iter.get_line_offset()
                result.append((line_num, col, col))

        return result

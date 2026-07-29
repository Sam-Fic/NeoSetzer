#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
    self.cursors. Each cursor may have an associated selection (a range
    from cursor_mark to a corresponding anchor mark).

    Supports:
    - Ctrl+Click to add cursor at position
    - Ctrl+D / Ctrl+Shift+L select next/all occurrences
    - Alt+Drag column selection
    - Ctrl+Alt+Up/Down add cursor on line above/below
    - Multi-cursor text insertion (typing at all cursors)
    - Multi-cursor deletion (backspace/delete at all cursors)
    """

    def __init__(self, document):
        self.document = document
        self.buffer = document.source_buffer
        self.view = document.source_view

        # Additional cursors: list of (cursor_mark, selection_anchor_mark or None)
        # The primary cursor is NOT in this list.
        self.cursors = []

        # Whether column selection mode is active
        self._column_mode = False

        # Selection tags for highlighting additional selections
        self._selection_tags = []

        # Create a drawing area for rendering additional cursors
        self._draw_area = Gtk.DrawingArea()
        self._draw_area.set_css_classes(['multicursor-overlay'])
        self._draw_area.set_hexpand(True)
        self._draw_area.set_vexpand(True)
        self._draw_area.set_margin_top(0)
        self._draw_area.set_margin_bottom(0)
        self._draw_area.set_margin_start(0)
        self._draw_area.set_margin_end(0)
        self._draw_area.set_can_focus(False)
        self._draw_area.set_can_target(False)  # Don't receive events
        self._draw_area.set_draw_func(self._on_draw, None)

        # Try to add the drawing area to the overlay
        parent = self.view.get_parent()
        while parent is not None:
            if isinstance(parent, Gtk.Overlay):
                parent.add_overlay(self._draw_area)
                break
            parent = parent.get_parent()

        # Cursor blink handling
        self._cursor_blink_id = None
        self._cursor_visible = True

        # Track primary cursor changes to update selection handling
        self._cursor_handler = self.buffer.connect(
            'notify::cursor-position', self._on_primary_cursor_changed)

        # Track buffer modifications to adjust cursor positions
        self._insert_handler = self.buffer.connect('insert-text', self._on_insert_text_before)
        self._delete_handler = self.buffer.connect('delete-range', self._on_delete_range_before)

        # Suppress handler during multi-cursor edits
        self._suppress_handlers = False

        # Track search context for select-next-occurrence
        self._search_context = None

    def shutdown(self):
        """Clean up signals and marks."""
        self._clear_all_cursors()

        # Remove drawing area from parent
        if self._draw_area is not None:
            parent = self._draw_area.get_parent()
            if parent is not None and isinstance(parent, Gtk.Overlay):
                parent.remove_overlay(self._draw_area)
            self._draw_area = None

        if self._cursor_handler:
            self.buffer.disconnect(self._cursor_handler)
            self._cursor_handler = None
        if self._insert_handler:
            self.buffer.disconnect(self._insert_handler)
            self._insert_handler = None
        if self._delete_handler:
            self.buffer.disconnect(self._delete_handler)
            self._delete_handler = None
        if self._cursor_blink_id:
            GLib.Source.remove(self._cursor_blink_id)
            self._cursor_blink_id = None

    # --- Cursor management ---

    def get_cursor_count(self):
        """Total cursor count including primary."""
        return 1 + len(self.cursors)

    def has_multiple_cursors(self):
        return len(self.cursors) > 0

    def clear_all(self):
        """Remove all additional cursors and selections."""
        self._clear_all_cursors()

    def _clear_all_cursors(self):
        """Internal: remove all additional cursor marks and tags."""
        for cursor_mark, anchor_mark in self.cursors:
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark:
                self.buffer.delete_mark(anchor_mark)
        self.cursors.clear()

        for tag in self._selection_tags:
            # Remove tag from buffer ranges
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            self.buffer.remove_tag(tag, start, end)
            self.buffer.get_tag_table().remove(tag)
        self._selection_tags.clear()

        self._column_mode = False
        self._draw_area.queue_draw()

    def add_cursor_at_iter(self, iter_pos):
        """Add an additional cursor at the given position."""
        mark = self.buffer.create_mark(None, iter_pos, True)
        self.cursors.append((mark, None))
        self._draw_area.queue_draw()

    def add_cursor_with_selection(self, start_iter, end_iter):
        """Add an additional cursor with a selection range."""
        # Ensure start is before end
        if start_iter.compare(end_iter) > 0:
            start_iter, end_iter = end_iter, start_iter

        cursor_mark = self.buffer.create_mark(None, end_iter, True)
        anchor_mark = self.buffer.create_mark(None, start_iter, True)
        self.cursors.append((cursor_mark, anchor_mark))

        # Add visual selection highlight
        self._add_selection_tag(start_iter, end_iter)
        self._draw_area.queue_draw()

    def remove_last_cursor(self):
        """Remove the most recently added cursor."""
        if self.cursors:
            cursor_mark, anchor_mark = self.cursors.pop()
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark:
                self.buffer.delete_mark(anchor_mark)
            self._draw_area.queue_draw()

    def remove_cursor_at_offset(self, offset, tolerance=2):
        """Remove cursor nearest to the given offset (within tolerance chars)."""
        best_idx = -1
        best_dist = tolerance + 1
        for i, (cursor_mark, _) in enumerate(self.cursors):
            mark_offset = cursor_mark.get_iter().get_offset()
            dist = abs(mark_offset - offset)
            if dist < best_dist:
                best_dist = dist
                best_idx = i

        if best_idx >= 0:
            cursor_mark, anchor_mark = self.cursors.pop(best_idx)
            self.buffer.delete_mark(cursor_mark)
            if anchor_mark:
                self.buffer.delete_mark(anchor_mark)
            self._draw_area.queue_draw()

    def add_cursors_column(self, anchor_iter, active_iter):
        """Create column selection (one cursor per line in range)."""
        self._clear_all_cursors()
        self._column_mode = True

        start_line = min(anchor_iter.get_line(), active_iter.get_line())
        end_line = max(anchor_iter.get_line(), active_iter.get_line())
        anchor_col = anchor_iter.get_line_offset()
        active_col = active_iter.get_line_offset()
        col_start = min(anchor_col, active_col)
        col_end = max(anchor_col, active_col)

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
                self.add_cursor_at_iter(cursor_iter)
            else:
                start_iter = line_iter.copy()
                start_iter.forward_chars(sel_start_col)
                end_iter = line_iter.copy()
                end_iter.forward_chars(sel_end_col)
                self.add_cursor_with_selection(start_iter, end_iter)

        self._draw_area.queue_draw()

    def add_cursor_above(self):
        """Add cursor on the line above each existing cursor."""
        new_cursors = []
        # Process primary cursor
        primary = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if primary.get_line() > 0:
            new_iter = self.buffer.get_iter_at_line(primary.get_line() - 1)[1]
            new_iter.set_line_offset(primary.get_line_offset())
            # Clamp to line length
            line_end = new_iter.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            if new_iter.get_offset() > line_end.get_offset():
                new_iter = line_end
            new_cursors.append(new_iter)

        # Process additional cursors
        for cursor_mark, _ in self.cursors:
            iter_pos = cursor_mark.get_iter()
            if iter_pos.get_line() > 0:
                new_iter = self.buffer.get_iter_at_line(iter_pos.get_line() - 1)[1]
                new_iter.set_line_offset(iter_pos.get_line_offset())
                line_end = new_iter.copy()
                if not line_end.ends_line():
                    line_end.forward_to_line_end()
                if new_iter.get_offset() > line_end.get_offset():
                    new_iter = line_end
                new_cursors.append(new_iter)

        for iter_pos in new_cursors:
            self.add_cursor_at_iter(iter_pos)

    def add_cursor_below(self):
        """Add cursor on the line below each existing cursor."""
        new_cursors = []
        line_count = self.buffer.get_line_count()
        primary = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if primary.get_line() < line_count - 1:
            new_iter = self.buffer.get_iter_at_line(primary.get_line() + 1)[1]
            new_iter.set_line_offset(primary.get_line_offset())
            line_end = new_iter.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            if new_iter.get_offset() > line_end.get_offset():
                new_iter = line_end
            new_cursors.append(new_iter)

        for cursor_mark, _ in self.cursors:
            iter_pos = cursor_mark.get_iter()
            if iter_pos.get_line() < line_count - 1:
                new_iter = self.buffer.get_iter_at_line(iter_pos.get_line() + 1)[1]
                new_iter.set_line_offset(iter_pos.get_line_offset())
                line_end = new_iter.copy()
                if not line_end.ends_line():
                    line_end.forward_to_line_end()
                if new_iter.get_offset() > line_end.get_offset():
                    new_iter = line_end
                new_cursors.append(new_iter)

        for iter_pos in new_cursors:
            self.add_cursor_at_iter(iter_pos)

    # --- Select next/all occurrence ---

    def select_next_occurrence(self):
        """Select the next occurrence of the selected text or word under cursor."""
        search_text = self._get_search_text()
        if not search_text:
            return False

        # If we have an active search, use it; otherwise create one
        if self._search_context is None:
            self._search_context = GtkSource.SearchContext.new(
                self.buffer, GtkSource.SearchSettings())
            self._search_context.set_highlight(False)

        settings = self._search_context.get_settings()
        settings.set_search_text(search_text)
        settings.set_case_sensitive(False)
        settings.set_at_word_boundaries(False)
        settings.set_regex_enabled(False)

        # Find the position after the last cursor (primary or additional)
        search_start = self._get_last_cursor_iter()
        search_start.forward_char()  # Start after last cursor

        # Search forward
        result = self._search_context.forward(search_start)
        if result[0]:
            # Add as a new selection
            self.add_cursor_with_selection(result[1], result[2])
            return True
        return False

    def select_all_occurrences(self):
        """Select all occurrences of the selected text or word under cursor."""
        search_text = self._get_search_text()
        if not search_text:
            return False

        if self._search_context is None:
            self._search_context = GtkSource.SearchContext.new(
                self.buffer, GtkSource.SearchSettings())
            self._search_context.set_highlight(False)

        settings = self._search_context.get_settings()
        settings.set_search_text(search_text)
        settings.set_case_sensitive(False)
        settings.set_at_word_boundaries(False)
        settings.set_regex_enabled(False)

        # Clear existing additional cursors
        self._clear_all_cursors()

        # First, set primary to the first occurrence
        search_iter = self.buffer.get_start_iter()
        primary_set = False

        while True:
            result = self._search_context.forward(search_iter)
            if not result[0]:
                break

            if not primary_set:
                # Set primary to first match
                self.buffer.select_range(result[2], result[1])
                primary_set = True
            else:
                # Add as additional selection
                self.add_cursor_with_selection(result[1], result[2])

            # Move past this match
            search_iter = result[2].copy()
            if not search_iter.forward_char():
                break

        return primary_set

    def _get_search_text(self):
        """Get the text to search: selection or word under cursor."""
        if self.buffer.get_has_selection():
            bounds = self.buffer.get_selection_bounds()
            if bounds and len(bounds) == 2:
                text = self.buffer.get_text(bounds[0], bounds[1], True)
                if text and text.strip():
                    return text
        # Get word under cursor
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return self._get_word_at_iter(cursor)

    def _get_word_at_iter(self, iter_pos):
        """Extract the word at the given iterator position."""
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
            return self.buffer.get_text(start, end, True)
        return None

    def _get_last_cursor_iter(self):
        """Get the iterator for the last (rightmost) cursor."""
        last_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        for cursor_mark, _ in self.cursors:
            iter_pos = cursor_mark.get_iter()
            if iter_pos.get_offset() > last_iter.get_offset():
                last_iter = iter_pos
        return last_iter.copy()

    # --- Edit operations (called from controller) ---

    def handle_insert(self, text):
        """Insert text at all cursor positions. Returns True if handled."""
        if not self.has_multiple_cursors() and not self._column_mode:
            return False

        self._suppress_handlers = True
        try:
            self.buffer.begin_user_action()

            # Collect all edit positions: (offset, cursor_or_selection)
            edits = []

            # Primary cursor
            primary_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
            if self.buffer.get_has_selection():
                sel_bounds = self.buffer.get_selection_bounds()
                edits.append((primary_iter.get_offset(), 'primary_selection',
                              sel_bounds[0].get_offset(), sel_bounds[1].get_offset()))
            else:
                edits.append((primary_iter.get_offset(), 'primary',
                              primary_iter.get_offset(), primary_iter.get_offset()))

            # Additional cursors
            for cursor_mark, anchor_mark in self.cursors:
                cursor_iter = cursor_mark.get_iter()
                cursor_offset = cursor_iter.get_offset()
                if anchor_mark:
                    anchor_iter = anchor_mark.get_iter()
                    anchor_offset = anchor_iter.get_offset()
                    start = min(cursor_offset, anchor_offset)
                    end = max(cursor_offset, anchor_offset)
                    edits.append((start, 'secondary_selection', start, end))
                else:
                    edits.append((cursor_offset, 'secondary', cursor_offset, cursor_offset))

            # Sort by start offset descending so edits don't affect earlier positions
            edits.sort(key=lambda e: e[2], reverse=True)

            for edit in edits:
                edit_type = edit[1]
                start_offset = edit[2]
                end_offset = edit[3]

                start_iter = self.buffer.get_iter_at_offset(start_offset)
                end_iter = self.buffer.get_iter_at_offset(end_offset)

                # Delete existing selection at this position
                if start_offset != end_offset:
                    self.buffer.delete(start_iter, end_iter)

                # Insert text
                insert_iter = self.buffer.get_iter_at_offset(start_offset)
                self.buffer.insert(insert_iter, text)

            self.buffer.end_user_action()
        finally:
            self._suppress_handlers = False

        self._draw_area.queue_draw()
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

            edits = []

            # Primary cursor
            if self.buffer.get_has_selection():
                sel_bounds = self.buffer.get_selection_bounds()
                start = min(sel_bounds[0].get_offset(), sel_bounds[1].get_offset())
                end = max(sel_bounds[0].get_offset(), sel_bounds[1].get_offset())
                edits.append((start, end))
            else:
                primary_iter = self.buffer.get_iter_at_mark(self.buffer.get_insert())
                if delete_type == 'backspace':
                    if primary_iter.get_offset() > 0:
                        primary_iter.backward_char()
                end = primary_iter.get_offset()
                edits.append((end, end + 1))

            # Additional cursors
            for cursor_mark, anchor_mark in self.cursors:
                cursor_iter = cursor_mark.get_iter()
                cursor_offset = cursor_iter.get_offset()
                if anchor_mark:
                    anchor_iter = anchor_mark.get_iter()
                    anchor_offset = anchor_iter.get_offset()
                    start = min(cursor_offset, anchor_offset)
                    end = max(cursor_offset, anchor_offset)
                    edits.append((start, end))
                else:
                    if delete_type == 'backspace':
                        if cursor_offset > 0:
                            edits.append((cursor_offset - 1, cursor_offset))
                    else:
                        line_end = cursor_iter.copy()
                        if not line_end.ends_line():
                            line_end.forward_char()
                        if cursor_offset != line_end.get_offset():
                            edits.append((cursor_offset, cursor_offset + 1))

            # Sort descending by start offset
            edits.sort(key=lambda e: e[0], reverse=True)

            # Merge overlapping ranges
            merged = []
            for start, end in edits:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (min(merged[-1][0], start), max(merged[-1][1], end))
                else:
                    merged.append((start, end))

            for start, end in merged:
                start_iter = self.buffer.get_iter_at_offset(start)
                end_iter = self.buffer.get_iter_at_offset(end)
                self.buffer.delete(start_iter, end_iter)

            self.buffer.end_user_action()
        finally:
            self._suppress_handlers = False

        self._draw_area.queue_draw()
        return True

    # --- Signal handlers ---

    def _on_primary_cursor_changed(self, buffer, location):
        """When primary cursor moves, check if we should clear multi-cursor."""
        if not self.has_multiple_cursors():
            return
        # Don't clear during our own edit operations
        if self._suppress_handlers:
            return
        # We don't clear on primary cursor change - user may want to move primary
        # while keeping additional cursors. Clearing happens on explicit actions.

    def _on_insert_text_before(self, buffer, location, text, length):
        """Track insertions to adjust cursor marks (Gtk handles this automatically)."""
        if self._suppress_handlers:
            return

    def _on_delete_range_before(self, buffer, start, end):
        """Track deletions to adjust cursor marks (Gtk handles this automatically)."""
        if self._suppress_handlers:
            return

    # --- Drawing ---

    def _on_draw(self, widget, cr, width, height, user_data):
        """Draw additional cursors and selection highlights.

        This is the draw_func callback for the overlay Gtk.DrawingArea.
        It draws on top of the source view, so coordinates need to be
        translated from source view space to drawing area space.
        """
        if not self.has_multiple_cursors():
            return

        source_view = self.view
        scale = widget.get_scale_factor()

        # Get the translation from source view to drawing area coordinates
        # The source view is inside a scrolled window, so we need to account
        # for the scroll offset
        scrolled = source_view.get_parent()
        hadj = scrolled.get_hadjustment()
        vadj = scrolled.get_vadjustment()
        scroll_x = hadj.get_value() if hadj else 0
        scroll_y = vadj.get_value() if vadj else 0

        # Get cursor color from the source view theme
        style_context = source_view.get_style_context()
        color = style_context.get_color(Gtk.StateFlags.NORMAL)

        try:
            cursor_color = source_view.get_cursor_color()
            if cursor_color:
                color = cursor_color
        except:
            pass

        # Draw additional cursors
        for cursor_mark, anchor_mark in self.cursors:
            cursor_iter = cursor_mark.get_iter()
            cursor_rect = source_view.get_iter_location(cursor_iter)

            # Translate from source view coordinates to drawing area coordinates
            # The drawing area is aligned with the overlay, so we need to find
            # the position of the source view relative to the drawing area
            source_alloc = source_view.get_allocation()
            draw_alloc = widget.get_allocation()

            # Get source view position relative to its scrolled window
            # and then to the overlay/drawing area
            # Since source view is the main child of the scrolled window,
            # and the scrolled window is inside a container that's inside the
            # overlay, we need to compute the offset

            # First, let's find the offset from the drawing area to the source view
            # by walking up the widget hierarchy
            offset_x, offset_y = self._get_source_view_offset(widget, source_view)

            # Now translate the cursor position
            x = cursor_rect.x - scroll_x + offset_x
            y = cursor_rect.y - scroll_y + offset_y

            # Clamp to drawing area bounds
            if x < -20 or x > width + 20 or y < -20 or y > height + 20:
                continue

            # Draw cursor line
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            cr.set_line_width(2.0 * scale)

            if self._cursor_visible:
                cr.move_to(x, y)
                cr.line_to(x, y + cursor_rect.height)
                cr.stroke()

            # Draw selection if any
            if anchor_mark:
                anchor_iter = anchor_mark.get_iter()
                self._draw_selection(source_view, cr, anchor_iter, cursor_iter,
                                    scale, offset_x, offset_y, scroll_x, scroll_y)

    def _get_source_view_offset(self, draw_widget, source_view):
        """Calculate the offset from the drawing area to the source view.

        Returns (offset_x, offset_y) in the same coordinate space as
        the drawing area.
        """
        # Walk up from source_view to find the overlay's child
        # We need to account for margins and padding
        offset_x = 0
        offset_y = 0

        # Get source view's position relative to its parent (scrolled window)
        # Actually, let's use the source view's allocation and the draw widget's
        # allocation to compute the offset

        # Alternative: use translate_coordinates
        try:
            # Translate source view's origin to the drawing area
            rect = Gdk.Rectangle()
            rect.x = 0
            rect.y = 0
            rect.width = 1
            rect.height = 1

            # source_view to draw_widget
            translated = source_view.translate_coordinates(draw_widget, 0, 0)
            if translated is not None:
                offset_x = translated[0]
                offset_y = translated[1]
                return offset_x, offset_y
        except:
            pass

        # Fallback: just use 0,0 and hope for the best
        return offset_x, offset_y

    def _draw_selection(self, source_view, cr, start_iter, end_iter, scale,
                        offset_x, offset_y, scroll_x, scroll_y):
        """Draw selection highlight for additional cursors."""
        # Get selection color from source view theme
        try:
            bg_color = source_view.get_style_context().get_background_color(
                Gtk.StateFlags.SELECTED)
        except:
            bg_color = Gdk.RGBA()
            bg_color.parse('#3584e4')

        # Make it semi-transparent for additional selections
        alpha = 0.4

        # Draw selection rectangles (handles multi-line)
        start_line = start_iter.get_line()
        end_line = end_iter.get_line()

        cr.set_source_rgba(bg_color.red, bg_color.green, bg_color.blue, alpha)

        for line_num in range(start_line, end_line + 1):
            found, line_start = self.buffer.get_iter_at_line(line_num)
            if not found:
                continue

            line_end = line_start.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()

            if line_num == start_line:
                sel_start = start_iter
            else:
                sel_start = line_start

            if line_num == end_line:
                sel_end = end_iter
            else:
                sel_end = line_end

            if sel_start.get_offset() >= sel_end.get_offset():
                continue

            # Get rectangles for this line segment
            rects = source_view.get_selection_bounds(sel_start, sel_end)
            if rects:
                for rect in rects:
                    x = rect.x - scroll_x + offset_x
                    y = rect.y - scroll_y + offset_y
                    width = rect.width
                    height = rect.height

                    cr.rectangle(x, y, width, height)
                    cr.fill()

    def _add_selection_tag(self, start_iter, end_iter):
        """Add a TextTag for visual selection highlight."""
        if start_iter.get_offset() >= end_iter.get_offset():
            return

        # Create a unique tag for this selection
        tag_name = f'mc-selection-{len(self._selection_tags)}'
        tag = self.buffer.create_tag(tag_name, background='#3584e4',
                                     background_set=True)
        # Make it semi-transparent - GTK doesn't support alpha in tags directly,
        # so we draw selections manually in _on_draw instead

        self._selection_tags.append(tag)

    # --- Column selection editing helper ---

    def get_column_selections(self):
        """Return list of (line_num, start_col, end_col) for column selections.

        Used for tab-aware editing and display.
        """
        if not self._column_mode:
            return []

        result = []
        for cursor_mark, anchor_mark in self.cursors:
            cursor_iter = cursor_mark.get_iter() if cursor_mark else None
            anchor_iter = anchor_mark.get_iter() if anchor_mark else None

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

    def is_column_mode(self):
        return self._column_mode

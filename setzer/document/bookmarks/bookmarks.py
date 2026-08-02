#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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

from setzer.helpers.observable import Observable


class Bookmarks(Observable):
    """Manages bookmarks for a single document.

    Bookmarks are stored as a sorted list of line numbers. When the document
    content changes, bookmark positions are updated via direct buffer
    signals (insert-text and delete-range) to maintain relative positions.
    """

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.source_buffer = document.source_buffer

        # Sorted list of line numbers that have bookmarks (0-based).
        self._bookmarks = list()

        self.source_buffer.connect('insert-text', self.on_text_insert)
        self.source_buffer.connect('delete-range', self.on_text_delete)

    def shutdown(self):
        """Disconnect signals to prevent memory leaks."""
        try:
            self.source_buffer.disconnect_by_func(self.on_text_insert)
        except (TypeError, AttributeError):
            pass
        try:
            self.source_buffer.disconnect_by_func(self.on_text_delete)
        except (TypeError, AttributeError):
            pass

    def on_text_insert(self, buffer, iter, text, length):
        """Track text insertion to update bookmark line numbers."""
        if not self._bookmarks:
            return
        insert_line = iter.get_line()
        new_lines = text.count('\n')
        if new_lines > 0:
            self._bookmarks = sorted(
                b + new_lines if b >= insert_line else b
                for b in self._bookmarks
            )
            self.add_change_code('bookmarks_changed')

    def on_text_delete(self, buffer, start, end):
        """Track text deletion to update bookmark line numbers."""
        if not self._bookmarks:
            return
        delete_start_line = start.get_line()
        delete_end_line = end.get_line()
        lines_deleted = delete_end_line - delete_start_line

        new_bookmarks = []
        for bookmark_line in self._bookmarks:
            if bookmark_line < delete_start_line:
                new_bookmarks.append(bookmark_line)
            elif bookmark_line > delete_end_line:
                new_bookmarks.append(bookmark_line - lines_deleted)
            else:
                new_bookmarks.append(delete_start_line)

        self._bookmarks = sorted(set(
            b for b in new_bookmarks
            if 0 <= b < self.source_buffer.get_line_count()
        ))
        self.add_change_code('bookmarks_changed')

    # -- Public API --

    def has_bookmark(self, line):
        """Check if a bookmark exists on the given line (0-based)."""
        return line in self._bookmarks

    def toggle_bookmark(self, line):
        """Toggle a bookmark on the given line (0-based)."""
        if self.has_bookmark(line):
            self._bookmarks.remove(line)
        else:
            self._bookmarks.append(line)
            self._bookmarks = sorted(self._bookmarks)
        self.add_change_code('bookmarks_changed')

    def add_bookmark(self, line):
        """Add a bookmark to the given line (0-based)."""
        if not self.has_bookmark(line):
            self._bookmarks.append(line)
            self._bookmarks = sorted(self._bookmarks)
            self.add_change_code('bookmarks_changed')

    def remove_bookmark(self, line):
        """Remove a bookmark from the given line (0-based)."""
        if self.has_bookmark(line):
            self._bookmarks.remove(line)
            self.add_change_code('bookmarks_changed')

    def clear_bookmarks(self):
        """Remove all bookmarks."""
        self._bookmarks.clear()
        self.add_change_code('bookmarks_changed')

    def get_bookmarks(self):
        """Return the sorted list of bookmarked line numbers."""
        return list(self._bookmarks)

    def get_bookmark_count(self):
        """Return the number of bookmarks."""
        return len(self._bookmarks)

    def get_next_bookmark_line(self, current_line):
        """Return the line number of the next bookmark after current_line.

        Wraps around to the first bookmark if none exists after current_line.
        Returns None if there are no bookmarks.
        """
        if not self._bookmarks:
            return None
        for line in self._bookmarks:
            if line > current_line:
                return line
        return self._bookmarks[0]

    def get_previous_bookmark_line(self, current_line):
        """Return the line number of the previous bookmark before current_line.

        Wraps around to the last bookmark if none exists before current_line.
        Returns None if there are no bookmarks.
        """
        if not self._bookmarks:
            return None
        for line in reversed(self._bookmarks):
            if line < current_line:
                return line
        return self._bookmarks[-1]

    # -- Persistence --

    def load_bookmarks_from_data(self, bookmark_lines):
        """Load bookmarks from a list of line numbers (from persisted state)."""
        line_count = self.source_buffer.get_line_count()
        self._bookmarks = sorted(set(
            b for b in bookmark_lines if 0 <= b < line_count
        ))
        self.add_change_code('bookmarks_changed')

    def get_data_for_persistence(self):
        """Return bookmark data for persistence (list of line numbers)."""
        return list(self._bookmarks)

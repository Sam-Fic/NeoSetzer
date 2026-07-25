#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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

'''源码打印：通过 Gtk.PrintOperation 调用系统打印对话框，
用户可选择打印机、份数、纸张等，无需自行实现打印 UI。'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo, GLib

from setzer.app.service_locator import ServiceLocator


class PrintDialog(object):

    def __init__(self):
        self.document = None
        self.lines = []
        self.lines_per_page = 0
        self.n_pages = 0
        self.font_desc = Pango.FontDescription.from_string('monospace 9')
        self.margin = 36  # points (~0.5 inch)

    def run(self, document):
        self.document = document
        op = Gtk.PrintOperation()
        op.set_job_name(document.get_displayname())
        op.set_show_progress(True)
        op.connect('begin-print', self.on_begin_print)
        op.connect('draw-page', self.on_draw_page)
        try:
            op.run(Gtk.PrintOperationAction.PRINT_DIALOG,
                   ServiceLocator.get_main_window())
        except Exception as e:
            print(f'Print failed: {e}')

    def on_begin_print(self, op, context):
        start, end = self.document.source_buffer.get_bounds()
        text = self.document.source_buffer.get_text(start, end, True)
        self.lines = text.split('\n')

        page_width = context.get_width()
        page_height = context.get_height()

        # 用 PrintContext 创建 PangoLayout，确保与打印 DPI 同步。
        layout = context.create_pango_layout()
        layout.set_font_description(self.font_desc)
        layout.set_wrap(Pango.WrapMode.CHAR)
        layout.set_width(int((page_width - 2 * self.margin) * Pango.SCALE))

        # 计算行高：用一条样例行量取 pixel 高度。
        layout.set_text('Ag')
        ink, logical = layout.get_pixel_extents()
        line_height = logical.height + 2  # 2px 行间距
        if line_height <= 0:
            line_height = 12

        self.lines_per_page = max(1, int((page_height - 2 * self.margin) / line_height))
        self.n_pages = max(1, (len(self.lines) + self.lines_per_page - 1) // self.lines_per_page)
        op.set_n_pages(self.n_pages)
        op._line_height = line_height  # 缓存供 draw-page 使用

    def on_draw_page(self, op, context, page_nr):
        cr = context.get_cairo_context()
        page_width = context.get_width()

        layout = context.create_pango_layout()
        layout.set_font_description(self.font_desc)
        layout.set_wrap(Pango.WrapMode.CHAR)
        layout.set_width(int((page_width - 2 * self.margin) * Pango.SCALE))

        line_height = op._line_height
        start_line = page_nr * self.lines_per_page
        end_line = min(start_line + self.lines_per_page, len(self.lines))

        cr.set_source_rgba(0, 0, 0, 1)
        y = self.margin

        for i in range(start_line, end_line):
            line_text = self.lines[i]
            # 带行号前缀（右对齐 5 位），便于对照源码。
            display_text = '{:>5}  {}'.format(i + 1, line_text)
            layout.set_text(display_text)
            cr.move_to(self.margin, y)
            PangoCairo.show_layout(cr, layout)
            y += line_height

        # 页脚：页码
        layout.set_text(_('Page {page} of {total}').format(page=page_nr + 1, total=self.n_pages))
        ink, logical = layout.get_pixel_extents()
        cr.move_to(page_width - self.margin - logical.width,
                   context.get_height() - self.margin - logical.height)
        PangoCairo.show_layout(cr, layout)

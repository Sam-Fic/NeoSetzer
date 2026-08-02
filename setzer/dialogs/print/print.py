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
# along with this program. If not, see <http://www.gnu.org/licenses/>

'''源码打印：通过 Gtk.PrintOperation 调用系统打印对话框，
用户可选择打印机、份数、纸张等，无需自行实现打印 UI。'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Pango, PangoCairo, GLib, Adw

import os

from setzer.app.service_locator import ServiceLocator


class PrintDialog(object):

    def __init__(self):
        self.document = None
        self.lines = []
        self.lines_per_page = 0
        self.n_pages = 0
        self.font_desc = Pango.FontDescription.from_string('monospace 9')
        self.margin = 36  # points (~0.5 inch)
        # 文本布局边距（点），运行时由 PageSetup 的打印边距覆盖；
        # 持久化后下次打开打印对话框沿用上次用户设定的边距。
        self.text_margin_x = self.margin
        self.text_margin_y = self.margin

        # 打印设置（打印机/份数/纸张/方向）与页面设置（纸张尺寸/边距）持久化到
        # 配置目录下的两个 ini 文件。Gtk 原生序列化，跨重启保留用户偏好。
        config_folder = ServiceLocator.get_config_folder()
        self.print_settings_path = os.path.join(config_folder, 'print-settings.ini')
        self.page_setup_path = os.path.join(config_folder, 'page-setup.ini')
        self.print_settings = self._load_print_settings()
        self.page_setup = self._load_page_setup()

    def _load_print_settings(self):
        try:
            if os.path.exists(self.print_settings_path):
                return Gtk.PrintSettings.new_from_file(self.print_settings_path)
        except Exception:
            pass
        return Gtk.PrintSettings()

    def _load_page_setup(self):
        try:
            if os.path.exists(self.page_setup_path):
                return Gtk.PageSetup.new_from_file(self.page_setup_path)
        except Exception:
            pass
        return Gtk.PageSetup()

    def _save_settings(self):
        '''把当前打印设置与页面设置落盘，供下次打开打印对话框恢复。'''
        try:
            os.makedirs(ServiceLocator.get_config_folder(), exist_ok=True)
            self.print_settings.to_file(self.print_settings_path)
            self.page_setup.to_file(self.page_setup_path)
        except Exception as e:
            print(f'Failed to save print settings: {e}')

    def _show_print_error_toast(self, error_msg):
        '''打印失败时向用户展示带错误详情的 toast。'''
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Printing failed: {error}').format(error=error_msg))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)

    def _sync_margins_from_page_setup(self):
        '''用持久化/用户选择的 PageSetup 边距驱动文本布局边距。

        PageSetup 缺省时上下左右边距可能为 0，此时回退到 self.margin 固定值。
        '''
        if self.page_setup is None:
            self.text_margin_x = self.text_margin_y = self.margin
            return
        left = self.page_setup.get_left_margin(Gtk.Unit.POINTS)
        top = self.page_setup.get_top_margin(Gtk.Unit.POINTS)
        if left > 0 and top > 0:
            self.text_margin_x = left
            self.text_margin_y = top
        else:
            self.text_margin_x = self.text_margin_y = self.margin

    def run(self, document):
        self.document = document
        op = Gtk.PrintOperation()
        op.set_job_name(document.get_displayname())
        op.set_show_progress(True)
        # 注入上次保存的设置，使对话框打开即呈现用户偏好（纸张/边距等）。
        op.set_print_settings(self.print_settings)
        op.set_default_page_setup(self.page_setup)
        op.connect('begin-print', self.on_begin_print)
        op.connect('draw-page', self.on_draw_page)
        op.connect('done', self.on_done)
        try:
            op.run(Gtk.PrintOperationAction.PRINT_DIALOG,
                   ServiceLocator.get_main_window())
        except Exception as e:
            self._show_print_error_toast(str(e))

    def on_done(self, op, result):
        '''打印对话框结束后把用户改动后的设置持久化（取消/报错时丢弃）。'''
        if result == Gtk.PrintOperationResult.APPLY:
            self.print_settings = op.get_print_settings()
            self.page_setup = op.get_default_page_setup()
            self._save_settings()

    def on_begin_print(self, op, context):
        start, end = self.document.source_buffer.get_bounds()
        text = self.document.source_buffer.get_text(start, end, True)
        self.lines = text.split('\n')

        # 用持久化/用户选择的 PageSetup 边距驱动文本布局（见 _sync_margins_from_page_setup）。
        self._sync_margins_from_page_setup()

        page_width = context.get_width()
        page_height = context.get_height()

        # 用 PrintContext 创建 PangoLayout，确保与打印 DPI 同步。
        layout = context.create_pango_layout()
        layout.set_font_description(self.font_desc)
        layout.set_wrap(Pango.WrapMode.CHAR)
        layout.set_width(int((page_width - 2 * self.text_margin_x) * Pango.SCALE))

        # 计算行高：用一条样例行量取 pixel 高度。
        layout.set_text('Ag')
        ink, logical = layout.get_pixel_extents()
        line_height = logical.height + 2  # 2px 行间距
        if line_height <= 0:
            line_height = 12

        self.lines_per_page = max(1, int((page_height - 2 * self.text_margin_y) / line_height))
        self.n_pages = max(1, (len(self.lines) + self.lines_per_page - 1) // self.lines_per_page)
        op.set_n_pages(self.n_pages)
        op._line_height = line_height  # 缓存供 draw-page 使用

    def on_draw_page(self, op, context, page_nr):
        cr = context.get_cairo_context()
        page_width = context.get_width()

        layout = context.create_pango_layout()
        layout.set_font_description(self.font_desc)
        layout.set_wrap(Pango.WrapMode.CHAR)
        layout.set_width(int((page_width - 2 * self.text_margin_x) * Pango.SCALE))

        line_height = op._line_height
        start_line = page_nr * self.lines_per_page
        end_line = min(start_line + self.lines_per_page, len(self.lines))

        cr.set_source_rgba(0, 0, 0, 1)
        y = self.text_margin_y

        for i in range(start_line, end_line):
            line_text = self.lines[i]
            # 带行号前缀（右对齐 5 位），便于对照源码。
            display_text = '{:>5}  {}'.format(i + 1, line_text)
            layout.set_text(display_text)
            cr.move_to(self.text_margin_x, y)
            PangoCairo.show_layout(cr, layout)
            y += line_height

        # 页脚：页码
        layout.set_text(_('Page {page} of {total}').format(page=page_nr + 1, total=self.n_pages))
        ink, logical = layout.get_pixel_extents()
        cr.move_to(page_width - self.text_margin_x - logical.width,
                   context.get_height() - self.text_margin_y - logical.height)
        PangoCairo.show_layout(cr, layout)

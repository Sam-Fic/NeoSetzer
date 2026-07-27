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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, Gtk, Adw, GLib

import webbrowser
import threading

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.go_to_page.go_to_page import GoToPageDialog


class PreviewController(object):

    def __init__(self, preview, view):
        self.preview = preview
        self.view = view

        self.zoom_buffer = 1
        self.cursor_default = Gdk.Cursor.new_from_name('default')
        self.cursor_pointer = Gdk.Cursor.new_from_name('pointer')
        # 缓存上次的 cursor / link_target：update_cursor 由
        # 滚动 + 鼠标移动每帧触发，原每次都无条件 set_cursor / set_link_target_string。
        # 鼠标在无链接区域移动时三者恒定，却每帧触发 GtkWidget cursor 属性设置 +
        # Gtk.Label set_text（Pango 重排）+ valign 变化（Overlay 重排）。仅在值
        # 变化时设置，将 60 次/秒降为实际跨越链接边界时（典型 0-2 次/秒）。
        self._current_cursor = None
        self._current_link_target = None

        self.view.content.connect('size_changed', self.on_size_change)
        self.view.content.connect('scrolling_offset_changed', self.on_scrolling_offset_change)
        self.view.content.connect('hover_state_changed', self.on_hover_state_change)
        self.view.content.connect('primary_button_press', self.on_primary_button_press)
        self.view.content.connect('zoom_request', self.on_zoom_request)

        # 键盘导航：PgUp/PgDn 翻页、Home/End 首末页、方向键微调、Ctrl+G 跳页。
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.view.add_controller(key_controller)

    def on_size_change(self, *arguments):
        self.preview.zoom_manager.update_dynamic_zoom_levels()
        self.update_cursor()

    def on_scrolling_offset_change(self, *arguments):
        self.preview.update_position()
        self.update_cursor()

    def on_zoom_request(self, content, amount):
        self.preview.update_position()

        layout = self.preview.layout
        manager = self.preview.zoom_manager

        prev_zoom_level = manager.get_zoom_level()
        zoom_level = self._compute_zoom_level(prev_zoom_level, amount)

        factor = zoom_level / manager.zoom_level
        x = factor * self.view.content.scrolling_offset_x + (factor - 1) * self.view.content.cursor_x
        prev_pages = self.view.content.scrolling_offset_y // (layout.page_height + layout.page_gap)
        y = (1 - factor) * prev_pages * layout.page_gap + factor * self.view.content.scrolling_offset_y + (factor - 1) * self.view.content.cursor_y
        # Ctrl+滚轮是手动缩放：脱离任何 fit 模式，保留用户设定的绝对级别。
        manager.zoom_mode = 'manual'
        manager.set_zoom_level(zoom_level)
        self.preview.scroll_to_position(x, y)

    def _compute_zoom_level(self, prev_zoom_level, amount):
        '''根据缩放量计算目标缩放级别。

        在停靠点（fit-to-width / fit-to-text-width / fit-to-height）附近使用
        zoom_buffer 平滑过渡，避免在停靠点处抖动；跨越停靠点时吸附到停靠点。
        zoom_buffer 作为实例状态在调用间保持，实现「累积缩放」手感。

        参数:
            prev_zoom_level: 当前缩放级别
            amount: 缩放量（正值放大，负值缩小）
        返回:
            目标缩放级别（夹在 [0.25, 4]）
        '''
        manager = self.preview.zoom_manager
        gap = 1.25
        # 停靠点由 update_dynamic_zoom_levels 缓存为 tuple，避免每次缩放都
        # 重建 3 元素列表。tuple 的 `in` 也略快于 list。
        stopping_points = manager._stopping_points

        if prev_zoom_level in stopping_points:
            if amount <= 0:
                self.zoom_buffer *= (1 - amount)
                adj_amount = max(1, self.zoom_buffer / gap)
                zoom_level = min(max(prev_zoom_level * adj_amount, 0.25), 4)
            else:
                self.zoom_buffer *= (1 - amount)
                adj_amount = min(1, self.zoom_buffer * gap)
                zoom_level = min(max(prev_zoom_level * adj_amount, 0.25), 4)
        else:
            zoom_level = min(max(prev_zoom_level * (1 - amount), 0.25), 4)
            if amount <= 0:
                for level in stopping_points:
                    if prev_zoom_level < level and zoom_level >= level:
                        zoom_level = level
                        self.zoom_buffer = 1 / gap
            if amount > 0:
                for level in stopping_points:
                    if prev_zoom_level > level and zoom_level <= level:
                        zoom_level = level
                        self.zoom_buffer = 1 * gap
        return zoom_level

    def on_hover_state_change(self, *arguments):
        self.update_cursor()

    def update_cursor(self):
        if self.preview.layout == None: return True

        content = self.view.content
        x_offset = content.scrolling_offset_x + (content.cursor_x if content.cursor_x != None else 0)
        y_offset = content.scrolling_offset_y + (content.cursor_y if content.cursor_y != None else 0)

        window_width = content.width
        data = self.preview.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
        if data == None: return True

        page_number, x_offset, y_offset = data
        cursor = self.cursor_default
        link_target = ''
        links = self.preview.links_parser.get_links_for_page(page_number)
        y_offset = (self.preview.page_height - y_offset)
        for link in links:
            if x_offset > link[0].x1 and x_offset < link[0].x2 and y_offset > link[0].y1 and y_offset < link[0].y2:
                cursor = self.cursor_pointer
                if link[2] == 'uri':
                    link_target = link[1]
                elif link[2] == 'goto':
                    link_target = _('Go to page ') + str(link[1].page_num)
                break

        # 仅在变化时设置：set_cursor 触发 GtkWidget cursor 属性流程，
        # set_link_target_string 触发 Gtk.Revealer 动画。
        # 鼠标在无链接区移动时两者恒定，避免每帧重复设置。
        if cursor is not self._current_cursor:
            self._current_cursor = cursor
            self.view.set_cursor(cursor)
        if link_target != self._current_link_target:
            self._current_link_target = link_target
            self.view.set_link_target_string(link_target)

    def on_primary_button_press(self, content, data):
        if self.preview.layout == None: return True

        x_offset, y_offset, state = data

        if state == Gdk.ModifierType.CONTROL_MASK:
            self.preview.init_backward_sync(x_offset, y_offset)
            return True

        if state == 0:
            window_width = content.width
            data = self.preview.layout.get_page_number_and_offsets_by_document_offsets(x_offset, y_offset, window_width)
            if data == None: return True

            page_number, x_offset, y_offset = data
            links = self.preview.links_parser.get_links_for_page(page_number)
            y_offset = self.preview.page_height - y_offset
            for link in links:
                if x_offset > link[0].x1 and x_offset < link[0].x2 and y_offset > link[0].y1 and y_offset < link[0].y2:
                    if link[2] == 'goto':
                        self.preview.scroll_dest_on_screen(link[1])
                        return True
                    elif link[2] == 'uri':
                        url = link[1]
                        # 安全：拦截可能执行脚本的 scheme（javascript: / vbscript:
                        # / data:），并要求用户确认后再打开本地文件（file://），
                        # 防止恶意 PDF 在用户不知情下运行脚本或访问本机文件。
                        scheme = ''
                        try:
                            scheme = (GLib.uri_parse_scheme(url) or '').lower()
                        except Exception:
                            scheme = ''
                        if scheme in ('javascript', 'vbscript', 'data'):
                            self._show_blocked_link_toast()
                            return True
                        if scheme == 'file':
                            self._confirm_open_file(url)
                            return True
                        # 其它（http(s) / mailto / ftp 等）在后台线程打开，避免
                        # 阻塞 GTK 主线程；失败时在主线程弹 toast。
                        def _open_url():
                            try:
                                webbrowser.open_new_tab(url)
                            except Exception:
                                GLib.idle_add(self._show_url_error_toast)
                        threading.Thread(target=_open_url, daemon=True).start()
                        return True
            return True

    def _show_url_error_toast(self):
        '''在主线程显示「无法打开链接」toast。

        webbrowser.open_new_tab 在后台线程中调用；失败时通过 GLib.idle_add
        切回主线程显示 toast（GTK 控件非线程安全，不能从后台线程操作）。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Could not open link'))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)
        return False

    def _show_blocked_link_toast(self):
        '''在主线程显示「链接被拦截」toast（危险 scheme）。'''
        main_window = ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('Link blocked: this link type is unsafe'))
            toast.set_timeout(5)
            main_window.toast_overlay.add_toast(toast)
        return False

    def _confirm_open_file(self, uri):
        '''打开本地文件（file://）前先征得用户同意。'''
        dialog = Adw.AlertDialog(
            heading=_('Open local file from this PDF?'),
            body=_('This link points to a file on your computer:\n\n{uri}').format(uri=uri))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('open', _('Open'))
        dialog.set_response_appearance('open', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        # 把 uri 作为 user_data 传给回调。
        dialog.choose(main_window, None, self._on_open_file_response, uri)

    def _on_open_file_response(self, dialog, result, uri):
        try:
            dialog.choose_finish(result)
        except Exception:
            return
        if dialog.get_response() == 'open':
            def _open_url():
                try:
                    webbrowser.open_new_tab(uri)
                except Exception:
                    GLib.idle_add(self._show_url_error_toast)
            threading.Thread(target=_open_url, daemon=True).start()

    # ── 键盘导航 ──────────────────────────────────────────────

    def on_key_pressed(self, controller, keyval, keycode, state):
        '''支持标准 PDF 阅读器键盘快捷键：
        PgDn / Space — 下翻一页视口
        PgUp         — 上翻一页视口
        Home         — 跳到首页
        End          — 跳到末页
        ↑ / ↓        — 微调滚动（50px）
        Ctrl+G       — 跳转到指定页面
        '''
        if self.preview.layout == None or self.preview.poppler_document == None:
            return False

        ctrl = (state & Gdk.ModifierType.CONTROL_MASK) != 0

        # Ctrl+G — 跳转到页面
        if ctrl and keyval in (Gdk.KEY_g, Gdk.KEY_G):
            self._show_goto_page_dialog()
            return True

        # 以下快捷键不含 Ctrl
        if ctrl:
            return False

        content = self.view.content
        cur_y = content.scrolling_offset_y
        viewport_h = content.height
        layout = self.preview.layout
        step = layout.page_height + layout.page_gap
        total_h = layout.canvas_height
        max_y = max(total_h - viewport_h, 0)

        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_space, Gdk.KEY_KP_Space):
            new_y = min(cur_y + viewport_h * 0.9, max_y)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up):
            new_y = max(cur_y - viewport_h * 0.9, 0)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self.preview.scroll_to_position(content.scrolling_offset_x, 0)
            return True

        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self.preview.scroll_to_position(content.scrolling_offset_x, max_y)
            return True

        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            new_y = min(cur_y + 50, max_y)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up):
            new_y = max(cur_y - 50, 0)
            self.preview.scroll_to_position(content.scrolling_offset_x, new_y)
            return True

        return False

    def _show_goto_page_dialog(self):
        '''弹出"跳转到页面"对话框。'''
        n_pages = self.preview.poppler_document.get_n_pages()
        if n_pages < 2:
            return
        main_window = ServiceLocator.get_main_window()
        dialog = GoToPageDialog(main_window)
        dialog.run(n_pages, self._goto_page)

    def _goto_page(self, page_number):
        '''跳转到指定页面（1-based）。'''
        layout = self.preview.layout
        if layout == None:
            return
        content = self.view.content
        step = layout.page_height + layout.page_gap
        # page_number 是 1-based，Y 偏移从第 0 页顶部开始
        y = (page_number - 1) * step
        self.preview.scroll_to_position(content.scrolling_offset_x, y)



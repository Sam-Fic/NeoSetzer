#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY, without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk, Gio, GLib

from setzer.helpers.observable import Observable
from setzer.app.service_locator import ServiceLocator


def _action_item(label, detailed_action, accel=None):
    '''A Gio.MenuItem for an action, optionally with a parseable accel.'''
    item = Gio.MenuItem.new(label, detailed_action)
    if accel is not None:
        item.set_attribute_value('accel', GLib.Variant('s', accel))
    return item


class ContextMenu(Observable):
    '''Preview area right-click context menu.

    Uses native ``Gtk.PopoverMenu`` with a ``Gio.Menu`` model, matching the
    source view context menu's technology and styling.  The model is rebuilt
    on each right-click to adapt to link / page context.  The search-in-PDF
    popover is a separate ``Gtk.Popover`` kept unchanged.
    '''

    def __init__(self, preview, preview_view):
        Observable.__init__(self)

        self.preview = preview
        self.preview_view = preview_view

        self.current_page_number = None
        self.current_link = None
        self.popup_view_x = 0
        self.popup_view_y = 0

        # Persistent Gtk.PopoverMenu — model rebuilt per right-click.
        self.popover_pointer = Gtk.PopoverMenu()
        self.popover_pointer.set_size_request(288, -1)
        # popover 实际宽度由菜单内容决定（实测约 334，set_size_request 的 288
        # 只是最小值）。popup 前 measure() 测不到（内部子 widget 未构建），
        # map 回调里 allocated_width 也还是 0——宽度是 map 之后才分配的。
        # 故缓存宽度：首次用默认值让光标对齐左边缘，size-allocate 校正后续。
        self._popover_width = 334
        if hasattr(self.popover_pointer, 'set_can_shrink'):
            self.popover_pointer.set_can_shrink(False)
        self.popover_pointer.set_has_arrow(False)
        self.popover_pointer.connect('map', self._on_popover_map)
        self.popover_pointer.connect('notify::allocation', self._on_popover_size_allocate)
        # Gtk.PopoverMenu 内部用 GtkScrolledWindow 包裹菜单内容，
        # 其 max-content-height 默认约 400px，超出即滚动。
        # 遍历子树找到该 ScrolledWindow，取消高度限制。
        self._remove_internal_scroll(self.popover_pointer)

        self.search_popover = None
        self.search_entry = None

        self.view = preview_view
        # GTK4 要求 Popover 在 popup() 前必须有 parent，否则
        # gdk_surface_new_popup 因无 parent surface 而段错误。
        self.popover_pointer.set_parent(preview_view.content.view)
        self.view.content.connect('secondary_button_press', self.on_secondary_button_press)

    # --- Internal helpers -----------------------------------------------------

    @staticmethod
    def _remove_internal_scroll(widget):
        '''递归遍历子树，找到 GtkScrolledWindow 并取消其 max-content-height 限制，
        使 Gtk.PopoverMenu 内部菜单不需要滚动即可完整显示。'''
        if isinstance(widget, Gtk.ScrolledWindow):
            widget.set_max_content_height(2147483647)
            widget.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            return
        child = widget.get_first_child() if hasattr(widget, 'get_first_child') else None
        while child is not None:
            ContextMenu._remove_internal_scroll(child)
            child = child.get_next_sibling() if hasattr(child, 'get_next_sibling') else None

    # --- Model construction ---------------------------------------------------

    def _build_model(self, page_number, link):
        '''Build the Gio.Menu model for the current context.'''
        model = Gio.Menu()

        # Link-aware section (conditional).
        if link is not None:
            link_section = Gio.Menu()
            link_section.append_item(_action_item(_('Open Link'), 'win.preview-open-link'))
            link_type = link[2]
            if link_type == 'uri':
                link_section.append_item(_action_item(_('Copy Link Location'), 'win.preview-copy-link'))
            model.append_section(None, link_section)

        # Page content section.
        page_section = Gio.Menu()
        if page_number is not None:
            target = GLib.Variant('i', page_number)
            copy_item = Gio.MenuItem.new(_('Copy'), 'win.preview-copy-text')
            copy_item.set_action_and_target_value('win.preview-copy-text', target)
            page_section.append_item(copy_item)

            copy_img_item = Gio.MenuItem.new(_('Copy Image'), 'win.preview-copy-image')
            copy_img_item.set_action_and_target_value('win.preview-copy-image', target)
            page_section.append_item(copy_img_item)

            save_img_item = Gio.MenuItem.new(_('Save Image As…'), 'win.preview-save-image')
            save_img_item.set_action_and_target_value('win.preview-save-image', target)
            page_section.append_item(save_img_item)
        else:
            # No page context — show items disabled (greyed out via no action match).
            page_section.append_item(_action_item(_('Copy'), 'win.preview-copy-text'))
            page_section.append_item(_action_item(_('Copy Image'), 'win.preview-copy-image'))
            page_section.append_item(_action_item(_('Save Image As…'), 'win.preview-save-image'))
        model.append_section(None, page_section)

        # Find & Print section.
        find_section = Gio.Menu()
        find_section.append_item(_action_item(_('Search in PDF'), 'win.preview-search-pdf'))
        find_section.append_item(_action_item(_('Print…'), 'win.preview-print'))
        model.append_section(None, find_section)

        # View controls section.
        view_section = Gio.Menu()
        view_section.append_item(_action_item(_('Rotate Clockwise'), 'win.preview-rotate-cw'))
        view_section.append_item(_action_item(_('Rotate Counterclockwise'), 'win.preview-rotate-ccw'))
        view_section.append_item(_action_item(_('Invert Colors'), 'win.preview-recolor'))
        model.append_section(None, view_section)

        # Show Source.
        source_section = Gio.Menu()
        source_section.append_item(_action_item(_('Show Source'), 'win.preview-show-source'))
        model.append_section(None, source_section)

        # Zoom controls as custom child row.
        zoom_section = Gio.Menu()
        zoom_item = Gio.MenuItem()
        zoom_item.set_attribute_value('custom', GLib.Variant('s', 'zoom-controls'))
        zoom_section.append_item(zoom_item)
        model.append_section(None, zoom_section)

        return model

    # --- Menu activation ------------------------------------------------------

    def on_secondary_button_press(self, content, data):
        if self.preview.layout is None:
            return True

        x_offset, y_offset, state = data
        self.popup_view_x = x_offset - content.scrolling_offset_x
        self.popup_view_y = y_offset - content.scrolling_offset_y

        page_number, link = self.preview.get_context_at(x_offset, y_offset)
        self.current_page_number = page_number
        self.current_link = link

        # Rebuild model for current context.
        model = self._build_model(page_number, link)
        self.popover_pointer.set_menu_model(model)

        # Ensure zoom custom child is attached (only once).
        if not hasattr(self, '_zoom_widget_added'):
            self.popover_pointer.add_child(self._build_zoom_widget(), 'zoom-controls')
            self._zoom_widget_added = True

        # Sync recolor action state so checkmark is current.
        recolor_action = self.preview.document.settings.get_value('preferences', 'recolor_pdf')
        self._sync_recolor_state(recolor_action)
        # 定位：offset 让光标落在 popover 左上角附近（右移抵消 GTK 水平居中）。
        # 宽度用缓存值——popup 前 measure() 返回 0、map 时 allocated 也为 0
        # （宽度是 map 之后才分配的），首次用实测默认值，notify::allocation 校正后续。
        self.popover_pointer.set_offset(int(self._popover_width / 2.3), 0)
        rect = Gdk.Rectangle()
        rect.x = int(self.popup_view_x)
        rect.y = int(self.popup_view_y)
        rect.width = 1
        rect.height = 1
        self.popover_pointer.set_pointing_to(rect)
        # 必须用 idle 延迟到事件序列结束后再 popup：在 GestureClick 回调内
        # 同步 popup() 会被立刻关闭（MAP→UNMAP→CLOSED）。sidebar 的 popover
        # 每次右键新建、controller 单一，可同步 popup；preview 的 popover 是
        # 持久的，且 DrawingArea 上挂了多个 controller，回调内同步 popup 时
        # grab 冲突导致 popover 弹出后立即被关闭。
        GLib.idle_add(self.popover_pointer.popup)
        return True

    def _on_popover_map(self, popover):
        popover.grab_focus()

    def _on_popover_size_allocate(self, popover, gparam):
        # 校正缓存宽度：菜单内容变化时（如出现 link 区段）宽度可能改变，
        # 下次 popup 用新值定位。GTK4 移除了 size-allocate 信号，用 notify::allocation。
        w = popover.get_allocated_width()
        if w > 0 and w != self._popover_width:
            self._popover_width = w

    def _sync_recolor_state(self, recolor_pdf):
        '''Sync the preview-recolor action state to match current recolor setting.'''
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        action = main_window.lookup_action('preview-recolor')
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(recolor_pdf))

    # --- Zoom widget (custom child) -------------------------------------------

    def _build_zoom_widget(self):
        box = Gtk.CenterBox()
        box.set_orientation(Gtk.Orientation.HORIZONTAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        zoom_label = Gtk.Label(label=_('Zoom'))
        box.set_start_widget(zoom_label)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        button_zoom_out = Gtk.Button()
        button_zoom_out.set_icon_name('value-decrease-symbolic')
        button_zoom_out.add_css_class('flat')
        button_zoom_out.set_action_name('win.preview-zoom-out')
        inner_box.append(button_zoom_out)

        self.reset_zoom_button = Gtk.Button()
        self.reset_zoom_button.add_css_class('flat')
        self.reset_zoom_button.set_action_name('win.preview-fit-mode')
        self.reset_zoom_button.set_action_target_value(GLib.Variant('s', 'width'))
        inner_box.append(self.reset_zoom_button)

        button_zoom_in = Gtk.Button()
        button_zoom_in.set_icon_name('value-increase-symbolic')
        button_zoom_in.add_css_class('flat')
        button_zoom_in.set_action_name('win.preview-zoom-in')
        inner_box.append(button_zoom_in)

        box.set_end_widget(inner_box)
        return box

    # --- Search in PDF --------------------------------------------------------

    def open_search_popover(self, page_number):
        self.current_page_number = page_number
        if self.search_popover is None:
            self.search_popover = self._build_search_popover()
        self.search_popover.set_parent(self.preview_view.content.view)
        self._popup_search_at_cursor()
        self.search_entry.grab_focus()

    def _build_search_popover(self):
        popover = Gtk.Popover()
        popover.set_size_request(340, -1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        entry = Gtk.SearchEntry()
        entry.set_hexpand(True)
        entry.set_placeholder_text(_('Find in PDF…'))
        entry.connect('activate', self._on_search_next)
        entry.connect('search-changed', self._on_search_changed)

        prev = Gtk.Button(icon_name='go-up-symbolic')
        prev.set_tooltip_text(_('Previous Match'))
        prev.connect('clicked', self._on_search_prev)

        nxt = Gtk.Button(icon_name='go-down-symbolic')
        nxt.set_tooltip_text(_('Next Match'))
        nxt.connect('clicked', self._on_search_next)

        box.append(entry)
        box.append(prev)
        box.append(nxt)
        popover.set_child(box)
        self.search_entry = entry
        return popover

    def _popup_search_at_cursor(self):
        rect = Gdk.Rectangle()
        rect.x = int(self.popup_view_x)
        rect.y = int(self.popup_view_y)
        rect.width = 1
        rect.height = 1
        self.search_popover.set_pointing_to(rect)
        self.search_popover.popup()

    def _on_search_changed(self, entry):
        query = entry.get_text()
        if query.strip():
            self.preview.search(query, True, self.current_page_number or 0)

    def _on_search_next(self, *args):
        q = self.search_entry.get_text()
        if not q.strip() or self.preview.poppler_document is None:
            return
        n = self.preview.poppler_document.get_n_pages()
        start = (self.preview._search_page + 1) % n
        self.preview.search(q, True, start)

    def _on_search_prev(self, *args):
        q = self.search_entry.get_text()
        if not q.strip() or self.preview.poppler_document is None:
            return
        n = self.preview.poppler_document.get_n_pages()
        start = (self.preview._search_page - 1) % n
        self.preview.search(q, False, start)

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
# along with this program. If not to, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GObject, Adw, Pango

import time

from setzer.widgets.search_entry.search_entry import SearchEntry


class DocumentStructurePage(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.sections = dict()
        self.section_widgets = dict()
        self.section_titles = list()
        self.scroll_to = None
        self._current_section_title = ''  # 缓存 section title，用于变化检测
        self._groups_cache = None         # page 的 group 列表缓存

        self.add_buttons()

        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        # toolbar 已在 add_buttons() 内部 append 到 self，这里只追加 page
        self.append(self.page)

        # Adw.PreferencesPage 自身不暴露 get_vadjustment()，但其内部第一个
        # 子控件就是 Gtk.ScrolledWindow，从中取得 vadjustment 供滚动导航使用。
        self.scrolled_window = self.page.get_first_child()
        self.scrolled_window.get_vadjustment().connect('changed', self.on_scroll_or_resize)
        self.scrolled_window.get_vadjustment().connect('value-changed', self.on_scroll_or_resize)
        self.next_button.connect('clicked', self.on_next_button_clicked)
        self.prev_button.connect('clicked', self.on_prev_button_clicked)
        self.search_button.connect('toggled', self.on_search_button_toggled)
        self.search_entry.connect('stop-search', self.on_search_stopped)
        self.search_entry.connect('changed', self.on_search_changed)

    def add_section(self, name, title, widget):
        group = Adw.PreferencesGroup()
        group.set_title(title)
        group.add_css_class('sidebar-section-group')
        group.add(widget)
        self.page.add(group)
        self.sections[name] = group
        self.section_widgets[name] = widget
        self.section_titles.append(title)
        self._groups_cache = None
        return group

    def set_section_visible(self, name, visible):
        self.sections[name].set_visible(visible)

    def add_buttons(self):
        # 顶部内嵌工具栏：左侧为随滚动更新的“当前分区”标题，右侧为
        # linked 的上一段 / 下一段导航按钮。两页（Document Structure /
        # Symbols）共享此结构，外观由 .sidebar-toolbar 统一。
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toolbar.add_css_class('sidebar-toolbar')

        self.section_label = Gtk.Label(label='')
        self.section_label.add_css_class('dim-label')
        self.section_label.add_css_class('sidebar-section-title')
        self.section_label.set_halign(Gtk.Align.START)
        self.section_label.set_hexpand(True)
        self.section_label.set_xalign(0.0)
        self.section_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.section_label.set_margin_start(2)
        self.toolbar.append(self.section_label)

        self.nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.prev_button = Gtk.Button()
        self.prev_button.set_icon_name('go-up-symbolic')
        self.prev_button.set_tooltip_text(_('Previous section'))
        self.prev_button.add_css_class('flat')
        self.prev_button.set_can_focus(False)
        self.nav_box.append(self.prev_button)

        self.next_button = Gtk.Button()
        self.next_button.set_icon_name('go-down-symbolic')
        self.next_button.set_tooltip_text(_('Next section'))
        self.next_button.add_css_class('flat')
        self.next_button.set_can_focus(False)
        self.nav_box.append(self.next_button)

        self.toolbar.append(self.nav_box)

        self.search_button = Gtk.ToggleButton()
        self.search_button.set_icon_name('edit-find-symbolic')
        self.search_button.set_can_focus(False)
        self.search_button.add_css_class('flat')
        self.search_button.set_tooltip_text(_('Find'))
        self.search_button.set_margin_start(6)
        self.toolbar.append(self.search_button)

        self.search_revealer = Gtk.Revealer()
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.search_box.add_css_class('sidebar-search-bar')

        self.search_entry = SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_box.append(self.search_entry)

        self.search_revealer.set_child(self.search_box)

        self.append(self.toolbar)
        self.append(self.search_revealer)

    def on_scroll_or_resize(self, *args):
        scrolling_offset = self.scrolled_window.get_vadjustment().get_value()
        self.prev_button.set_sensitive(scrolling_offset != 0)

        # 一次取 visible sections，复用给按钮敏感度 + section label
        visible_sections = self.get_visible_sections()

        vadj = self.scrolled_window.get_vadjustment()
        at_bottom = scrolling_offset + vadj.get_page_size() >= vadj.get_upper() - 1
        has_next = len(visible_sections) > 0 and scrolling_offset < visible_sections[-1][1]
        self.next_button.set_sensitive(not at_bottom and has_next)

        # section title：仅变化时 set_text，避免每帧触发 Gtk.Label 无谓重绘
        current_title = self._compute_current_title(visible_sections)
        if current_title != self._current_section_title:
            self._current_section_title = current_title
            self.section_label.set_text(current_title)

    def get_visible_sections(self):
        """返回 [(title, absolute_y), ...]，含所有 visible group 的内容绝对 Y 坐标。"""
        result = list()
        groups = self.get_page_groups()
        for i, group in enumerate(groups):
            if not group.get_visible():
                continue
            title = self.section_titles[i] if i < len(self.section_titles) else group.get_title()
            y = group.get_allocation().y
            result.append((title, y))
        return result

    def get_section_offsets(self):
        return [y for (title, y) in self.get_visible_sections()]

    def _compute_current_title(self, sections):
        '''返回当前滚动到视口顶部的分区标题；视口顶部位于第一段之前时返回首段标题。

        接收已取的 visible_sections（绝对 Y），避免重复调用 get_visible_sections。
        '''
        if len(sections) == 0:
            return ''
        scrolling_offset = self.scrolled_window.get_vadjustment().get_value()
        current = sections[0][0]
        for title, y in sections:
            if y <= scrolling_offset + 1:
                current = title
            else:
                break
        return current

    def get_page_groups(self):
        # Adw.PreferencesPage 的实际结构：page → ScrolledWindow → Viewport →
        # Clamp → Box → [Label, PreferencesGroup, ...]。groups 不是 page 的直接
        # 子控件，需递归收集。原实现遍历 page 直接子只得到 ScrolledWindow，
        # 导致 on_scroll_or_resize 一直只认到 1 个「section」，section 导航与
        # 「当前分区」label 功能失效——此处一并修复。
        if self._groups_cache is None:
            groups = list()
            self._collect_groups(self.page, groups)
            self._groups_cache = groups
        return self._groups_cache

    def _collect_groups(self, widget, out):
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.PreferencesGroup):
                out.append(child)
            else:
                self._collect_groups(child, out)
            child = child.get_next_sibling()

    def on_next_button_clicked(self, button):
        scrolling_offset = self.scrolled_window.get_vadjustment().get_value()

        for label_offset in self.get_section_offsets():
            if scrolling_offset < label_offset:
                self.scroll_view(label_offset)
                break

    def on_prev_button_clicked(self, button):
        scrolling_offset = self.scrolled_window.get_vadjustment().get_value()

        for label_offset in reversed([0] + self.get_section_offsets()):
            if scrolling_offset > label_offset:
                self.scroll_view(label_offset)
                break

    def scroll_view(self, position, duration=0.2):
        adjustment = self.scrolled_window.get_vadjustment()
        self.scroll_to = {'position_start': adjustment.get_value(), 'position_end': position, 'time_start': time.time(), 'duration': duration}
        self.scrolled_window.set_kinetic_scrolling(False)
        GObject.timeout_add(15, self.do_scroll)

    def do_scroll(self):
        if self.scroll_to != None:
            adjustment = self.scrolled_window.get_vadjustment()
            time_elapsed = time.time() - self.scroll_to['time_start']
            if self.scroll_to['duration'] == 0:
                time_elapsed_percent = 1
            else:
                time_elapsed_percent = time_elapsed / self.scroll_to['duration']
            if time_elapsed_percent >= 1:
                adjustment.set_value(self.scroll_to['position_end'])
                self.scroll_to = None
                self.scrolled_window.set_kinetic_scrolling(True)
                return False
            else:
                adjustment.set_value(self.scroll_to['position_start'] * (1 - self.ease(time_elapsed_percent)) + self.scroll_to['position_end'] * self.ease(time_elapsed_percent))
                return True
        return False

    def ease(self, time): return (time - 1)**3 + 1

    def on_search_button_toggled(self, button):
        if button.get_active():
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(True)
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(False)
            self.filter_sections('')

    def on_search_stopped(self, entry):
        self.search_button.set_active(False)

    def on_search_changed(self, entry):
        self.filter_sections(entry.get_text())

    def filter_sections(self, query):
        for name, group in self.sections.items():
            widget = self.section_widgets.get(name)
            if widget and hasattr(widget, 'filter_rows'):
                any_visible = widget.filter_rows(query)
                group.set_visible(any_visible or not query)
            else:
                group.set_visible(True)
        self._groups_cache = None

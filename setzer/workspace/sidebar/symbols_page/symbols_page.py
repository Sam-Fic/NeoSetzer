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
from gi.repository import Gtk, Gdk, Gio, GLib, GObject

import setzer.workspace.sidebar.symbols_page.symbols_page_viewgtk as symbols_page_view
from setzer.app.service_locator import ServiceLocator
import setzer.helpers.timer as timer

import math
import time
import xml.etree.ElementTree as ET
import os


class SymbolsPage(object):

    def __init__(self, workspace):
        self.view = symbols_page_view.SymbolsPageView()
        self.workspace = workspace

        self.scroll_to = None
        self._current_section_title = ''  # 缓存 section title，用于变化检测
        # 跟踪 section 导航滚动动画的 timeout id。原实现不跟踪，连续点击
        # 下一/上一段时多个 timeout 同时写 adjustment 造成抖动；widget 销毁
        # 时（duration 0.2s 内）timeout 仍访问已释放的 scrolled_window。
        self._scroll_timeout_id = None

        self.recent = ServiceLocator.get_settings().get_value('app_recent_symbols', 'symbols')
        self.recent_details = list()
        self.recent_view_size = None
        self.update_recent_widget()

        for symbols_list_view in self.view.symbols_views:
            symbols_list_view.connect('child-activated', self.on_flowbox_activated, symbols_list_view)
        self.view.symbols_view_recent.connect('child-activated', self.on_recent_activated)

        self.view.scrolled_window.get_hadjustment().connect('changed', self.on_symbols_view_size_allocate)
        self.view.scrolled_window.get_vadjustment().connect('changed', self.on_scroll_or_resize)
        self.view.scrolled_window.get_vadjustment().connect('value-changed', self.on_scroll_or_resize)
        self.view.next_button.connect('clicked', self.on_next_button_clicked)
        self.view.prev_button.connect('clicked', self.on_prev_button_clicked)
        self.view.search_button.connect('toggled', self.on_search_button_toggled)
        self.view.search_entry.connect('stop-search', self.on_search_stopped)
        self.view.search_entry.connect('changed', self.on_search_changed)
        # widget 销毁时取消进行中的滚动动画 timeout，避免回调访问已释放的
        # scrolled_window 并持有引用阻碍 GC。SymbolsPage 非控件，故连接
        # 其持有的 scrolled_window 的 destroy。
        self.view.scrolled_window.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        if self._scroll_timeout_id is not None:
            try:
                GObject.source_remove(self._scroll_timeout_id)
            except (ValueError, RuntimeError):
                pass
            self._scroll_timeout_id = None

    def update_recent_widget(self):
        for item in [item for item in self.recent]:
            self.add_recent_symbol_to_flowbox(item)

    def on_recent_activated(self, flowbox, child):
        if self.workspace.active_document is None:
            return
        category, command = child.symbol_data
        self.workspace.actions.insert_symbol(None, [command])
        self.add_recent_symbol((category, command))
        return True

    def remove_recent_symbol(self, item):
        self.recent.remove(item)
        for symbol in [symbol for symbol in self.recent_details]:
            if item[1] == symbol[1]:
                self.view.symbols_view_recent.remove(symbol[5])
                self.recent_details.remove(symbol)

    def add_recent_symbol(self, new_item):
        for item in [item for item in self.recent]:
            if item[1] == new_item[1]:
                self.remove_recent_symbol(item)
        if len(self.recent) >= 20:
            self.remove_recent_symbol(self.recent[0])

        self.recent.append(new_item)
        self.add_recent_symbol_to_flowbox(new_item)

    def add_recent_symbol_to_flowbox(self, item):
        (category, command) = item
        xml_tree = ET.parse(os.path.join(ServiceLocator.get_resources_path(), 'symbols', category + '.xml'))
        xml_root = xml_tree.getroot()
        elements = xml_root.findall('./symbol[@command=\'' + command + '\']')
        if len(elements) == 0:
            self.remove_recent_symbol(item)
        else:
            attrib = elements[0].attrib
            symbol = [attrib['file'].rsplit('.')[0], attrib['command'], attrib.get('package', None), int(attrib.get('original_width', 10)), int(attrib.get('original_height', 10))]
            size = max(symbol[3], symbol[4])

            image = Gtk.Image(icon_name='sidebar-' + symbol[0] + '-symbolic')
            image.set_pixel_size(int(size * 1.5))
            image.set_size_request(25 + 11, -1)
            tooltip_text = symbol[1]
            if symbol[2] != None: 
                tooltip_text += ' (' + _('Package') + ': ' + symbol[2] + ')'
            image.set_tooltip_text(tooltip_text)

            button = Gtk.Button(child=image)
            button.add_css_class('flat')
            button.set_tooltip_text(tooltip_text)
            child = Gtk.FlowBoxChild()
            child.set_child(button)
            # recent 点击只需 (category_folder, command) 即可重新插入并更新 recency
            child.symbol_data = (category, command)
            symbol.append(child)
            self.recent_details.append(symbol)

            self.view.symbols_view_recent.insert(child, 0)
            self.view.queue_draw()

    def on_flowbox_activated(self, flowbox, child, symbols_view):
        if self.workspace.active_document is None:
            return
        symbol = child.symbol_data
        self.workspace.actions.insert_symbol(None, [symbol[1]])
        self.add_recent_symbol((symbols_view.symbol_folder, symbol[1]))
        return True

    def on_scroll_or_resize(self, *args):
        scrolling_offset = self.view.scrolled_window.get_vadjustment().get_value()
        if scrolling_offset == 0:
            self.view.prev_button.set_sensitive(False)
        else:
            self.view.prev_button.set_sensitive(True)

        # 一次取 visible sections，复用给按钮敏感度 + section label，
        # 避免每帧调用两次 get_visible_sections（各遍历全部 labels）。
        sections = self.get_visible_sections()
        if len(sections) == 0:
            self.view.next_button.set_sensitive(False)
        else:
            final_offset = sections[-1][1]
            self.view.next_button.set_sensitive(scrolling_offset < final_offset)

        self.update_section_label(sections)

    def get_visible_sections(self):
        """返回 [(title, absolute_y), ...]，含所有 visible group 的内容绝对 Y 坐标。"""
        result = list()
        for group in self.view.labels:
            if not group.get_visible():
                continue
            title = group.get_title()
            y = group.get_allocation().y
            result.append((title, y))
        return result

    def get_section_offsets(self):
        return [y for (title, y) in self.get_visible_sections()]

    def get_current_section_title(self, sections=None):
        """返回当前滚动到视口顶部的分区标题；视口顶部位于第一段之前时返回首段标题。

        接收已取的 visible_sections（绝对 Y），避免重复调用 get_visible_sections。
        """
        if sections is None:
            sections = self.get_visible_sections()
        if len(sections) == 0:
            return ''
        scrolling_offset = self.view.scrolled_window.get_vadjustment().get_value()
        current = sections[0][0]
        for title, y in sections:
            if y <= scrolling_offset + 1:
                current = title
            else:
                break
        return current

    def update_section_label(self, sections=None):
        # section title：仅变化时 set_text，避免每帧触发 Gtk.Label 无谓重绘
        current_title = self.get_current_section_title(sections)
        if current_title != self._current_section_title:
            self._current_section_title = current_title
            self.view.section_label.set_text(current_title)

    def on_next_button_clicked(self, button):
        scrolling_offset = self.view.scrolled_window.get_vadjustment().get_value()

        for label_offset in self.get_section_offsets():
            if scrolling_offset < label_offset:
                self.scroll_view(label_offset)
                break

    def on_prev_button_clicked(self, button):
        scrolling_offset = self.view.scrolled_window.get_vadjustment().get_value()

        for label_offset in reversed([0] + self.get_section_offsets()):
            if scrolling_offset > label_offset:
                self.scroll_view(label_offset)
                break

    def on_search_button_toggled(self, button):
        if button.get_active():
            self.view.search_entry.set_text('')
            self.view.search_revealer.set_reveal_child(True)
            self.view.search_entry.grab_focus()
        else:
            self.view.search_entry.set_text('')
            self.view.search_revealer.set_reveal_child(False)
            document = self.workspace.get_active_document()
            if document != None:
                document.source_view.grab_focus()

    def on_search_stopped(self, entry):
        self.view.search_button.set_active(False)

    def on_search_changed(self, entry):
        self.update_symbols()

    def update_symbols(self):
        any_symbols_found = False

        search_words = self.view.search_entry.get_text().split()
        for i, symbols_view in enumerate(self.view.symbols_views):
            for symbol in symbols_view.visible_symbols:
                symbols_view.remove(symbol[5])
            symbols_view.visible_symbols = []

            for symbol in symbols_view.symbols:
                child = symbol[5]
                symbol_found = True
                for word in search_words:
                    if symbol[0].find(word) == -1:
                        symbol_found = False
                if symbol_found:
                    symbols_view.visible_symbols.append(symbol)
                    symbols_view.insert(child, -1)

            symbols_found = (len(symbols_view.visible_symbols) > 0)
            any_symbols_found |= symbols_found
            symbols_view.set_visible(symbols_found)
            self.view.labels[i].set_visible(symbols_found)
            self.view.placeholders[i].set_visible(symbols_found)

        if any_symbols_found:
            self.view.search_entry.remove_css_class('error')
        else:
            self.view.search_entry.add_css_class('error')

    def on_symbols_view_size_allocate(self, *arguments):
        for symbols_view in self.view.symbols_views:
            allocation = symbols_view.get_allocation()
            if symbols_view.size != (allocation.width, allocation.height):
                symbols_view.size = (allocation.width, allocation.height)

        view = self.view.symbols_view_recent
        allocation = view.get_allocation()
        if self.recent_view_size != (allocation.width, allocation.height):
            self.recent_view_size = (allocation.width, allocation.height)

    def scroll_view(self, position, duration=0.2):
        # 取消进行中的动画：连续点击下一/上一段时，旧 timeout 仍在写
        # adjustment，与新动画叠加产生抖动。
        if self._scroll_timeout_id is not None:
            GObject.source_remove(self._scroll_timeout_id)
            self._scroll_timeout_id = None
        adjustment = self.view.scrolled_window.get_vadjustment()
        self.scroll_to = {'position_start': adjustment.get_value(), 'position_end': position, 'time_start': time.time(), 'duration': duration}
        self.view.scrolled_window.set_kinetic_scrolling(False)
        self._scroll_timeout_id = GObject.timeout_add(15, self.do_scroll)

    def do_scroll(self):
        if self.scroll_to != None:
            adjustment = self.view.scrolled_window.get_vadjustment()
            time_elapsed = time.time() - self.scroll_to['time_start']
            if self.scroll_to['duration'] == 0:
                time_elapsed_percent = 1
            else:
                time_elapsed_percent = time_elapsed / self.scroll_to['duration']
            if time_elapsed_percent >= 1:
                adjustment.set_value(self.scroll_to['position_end'])
                self.scroll_to = None
                self.view.scrolled_window.set_kinetic_scrolling(True)
                self._scroll_timeout_id = None
                return False
            else:
                adjustment.set_value(self.scroll_to['position_start'] * (1 - self.ease(time_elapsed_percent)) + self.scroll_to['position_end'] * self.ease(time_elapsed_percent))
                return True
        # scroll_to 已被取消（新动画或销毁），停止本 timeout。
        self._scroll_timeout_id = None
        return False

    def ease(self, time): return (time - 1)**3 + 1

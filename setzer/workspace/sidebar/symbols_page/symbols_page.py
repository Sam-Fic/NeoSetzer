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
from setzer.helpers.symbol_categories import is_valid_category
from setzer.helpers.scroll_animator import ScrollAnimatorMixin
from setzer.workspace.sidebar.symbols_page.symbol_preview import attach_symbol_hover_preview

import math
import xml.etree.ElementTree as ET
import os


class SymbolsPage(ScrollAnimatorMixin):

    def __init__(self, workspace):
        self.view = symbols_page_view.SymbolsPageView()
        self.workspace = workspace

        self.scroll_to = None
        self._current_section_title = ''  # 缓存 section title，用于变化检测
        # 符号属性缓存：category -> {command: attrib_dict}。add_recent/favorite_symbol_to_flowbox
        # 原每次插入符号都 ET.parse 整个分类 XML（数十到上百节点）。运行时不可变，
        # 首次访问某分类时一次性解析建字典，后续 O(1) 查找。启动时 recent 列表
        # 20 项若同属一分类，由 20 次解析降为 1 次。
        self._symbol_attrib_cache = {}
        # 收藏命令集合：is_favorite_symbol / toggle_favorite_symbol 原对 self.favorites
        # 列表做 any() 线性扫描，悬停预览每次 popup 都调一次。维护并行 set 做 O(1) 查重。
        self._favorites_commands = set()
        # 搜索 idle 去抖 id：on_search_changed 原每次按键全量过滤，连续输入一个词
        # 的每个字符都触发 10 个 FlowBox 的可见性切换。150ms 停顿后合并为一次。
        self._search_idle_id = None
        # 跟踪 section 导航滚动动画的 timeout id。原实现不跟踪，连续点击
        # 下一/上一段时多个 timeout 同时写 adjustment 造成抖动；widget 销毁
        # 时（duration 0.2s 内）timeout 仍访问已释放的 scrolled_window。
        self._scroll_timeout_id = None

        self.recent = ServiceLocator.get_settings().get_value('app_recent_symbols', 'symbols')
        self.recent_details = list()
        self.recent_view_size = None
        self.update_recent_widget()

        self.favorites = ServiceLocator.get_settings().get_value('app_favorite_symbols', 'symbols')
        self.favorites_details = list()
        self._favorites_commands = {item[1] for item in self.favorites}
        self.update_favorites_widget()
        # 无收藏时隐藏整个 Favorites 分类。
        self.view.favorites_group.set_visible(len(self.favorites) > 0)

        for symbols_list_view in self.view.symbols_views:
            symbols_list_view.connect('child-activated', self.on_flowbox_activated, symbols_list_view)
        self.view.symbols_view_recent.connect('child-activated', self.on_recent_activated)
        self.view.symbols_view_favorites.connect('child-activated', self.on_recent_activated)

        # 主列表符号的 hover 预览需要在页面初始化完成后挂载（才能拿到
        # favorites 回调），故在此统一补挂。
        self.wire_favorites()

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
        self._cancel_scroll_animation()

    def update_recent_widget(self):
        for item in [item for item in self.recent]:
            self.add_recent_symbol_to_flowbox(item)

    def _get_symbol_attrib(self, category, command):
        '''从分类 XML 取某 command 的属性字典，带缓存。

        原实现每次插入符号都 ET.parse 整个分类 XML 并 findall 遍历。
        分类 XML 运行时不可变，首次访问建 command→attrib 字典，后续 O(1)。

        category 来自用户配置（recent_symbols / favorite_symbols），
        理论可被注入路径遍历（如 ``../etc/passwd``）。先用白名单校验
        拒绝非法值，再做文件 I/O。详见 setzer.helpers.symbol_categories。
        '''
        # 白名单守卫：非法 category 直接返回 None，不进缓存（避免缓存
        # 被污染后所有后续调用都命中空字典）。仍把空字典写入缓存以表示
        # 「此分类已尝试」，但区分 None（未校验通过）与 {}（已查无文件）。
        if not is_valid_category(category):
            return None
        cache = self._symbol_attrib_cache.get(category)
        if cache is None:
            try:
                xml_tree = ET.parse(os.path.join(ServiceLocator.get_resources_path(), 'symbols', category + '.xml'))
            except (FileNotFoundError, ET.ParseError):
                cache = {}
            else:
                # iter('symbol') 替代原 findall('./symbol[@command=...']')
                # 字符串拼接：避免 command 含单引号（如 \\text{'}）破坏
                # XPath 语法或注入任意表达式。建字典后按 key O(1) 取值。
                cache = {sym.attrib['command']: sym.attrib for sym in xml_tree.getroot().findall('./symbol')}
            self._symbol_attrib_cache[category] = cache
        return cache.get(command)

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
        self.save_recent()

    def add_recent_symbol(self, new_item):
        for item in [item for item in self.recent]:
            if item[1] == new_item[1]:
                self.remove_recent_symbol(item)
        if len(self.recent) >= 20:
            self.remove_recent_symbol(self.recent[0])

        self.recent.append(new_item)
        self.add_recent_symbol_to_flowbox(new_item)
        self.save_recent()

    def add_recent_symbol_to_flowbox(self, item):
        # 共用 _append_symbol_to_flowbox 构建/插入逻辑；仅在 attrib 缺失时
        # 走 Recent 专属的清理路径（从 recent 列表移除过期条目）。
        if self._append_symbol_to_flowbox(
                item, self.view.symbols_view_recent, self.recent_details) is None:
            self.remove_recent_symbol(item)

    def _append_symbol_to_flowbox(self, item, target_flowbox, details_list):
        '''构建符号按钮并插入指定 flowbox。Recent 与 Favorites 共用。

        原实现 add_recent_symbol_to_flowbox / add_favorite_symbol_to_flowbox
        有约 50 行完全重复的代码（XML 属性取值、图标创建、tooltip 拼接、
        hover 预览挂载、FlowBoxChild 包装），仅 flowbox 目标与 details
        列表不同。提取为本方法后，两处调用各剩 2-3 行。

        返回插入的 symbol 条目（6 元素 list）以与原 symbol 结构兼容；
        若 attrib 缺失（category/command 在 XML 中找不到），返回 None，
        由调用方决定如何清理过期条目（Recent 调 remove_recent_symbol，
        Favorites 调 remove_favorite_symbol）。
        '''
        (category, command) = item
        attrib = self._get_symbol_attrib(category, command)
        if attrib is None:
            return None
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
        button.set_accessible_name(_('Insert') + ' ' + symbol[1])
        # 悬停时弹出放大预览（放大版符号 + LaTeX 命令 + 收藏切换按钮）。
        # Recent 与 Favorites 共用同一预览组件，仅 folder 与 favorite 回调不同。
        attach_symbol_hover_preview(
            button, symbol, folder=category,
            favorite_state_func=self.is_favorite_symbol,
            favorite_toggle_func=self.toggle_favorite_symbol)
        child = Gtk.FlowBoxChild()
        child.set_child(button)
        # 点击只需 (category_folder, command) 即可重新插入并更新 recency / 收藏。
        child.symbol_data = (category, command)
        symbol.append(child)
        details_list.append(symbol)

        target_flowbox.insert(child, 0)
        self.view.queue_draw()
        return symbol

    # --- Favorites ---
    # 与 Recent 平行：存储 (category_folder, command)，渲染逻辑与 Recent 一致，
    # 但点击/悬停预览都基于 (folder, command)，且 hover 气泡提供收藏切换按钮。

    def update_favorites_widget(self):
        for item in [item for item in self.favorites]:
            self.add_favorite_symbol_to_flowbox(item)

    def add_favorite_symbol(self, new_item):
        # 去重：已收藏则忽略（保持首次收藏顺序）。O(1) set 查重替代线性扫描。
        if new_item[1] in self._favorites_commands:
            return
        self._favorites_commands.add(new_item[1])
        self.favorites.append(new_item)
        self.add_favorite_symbol_to_flowbox(new_item)
        self.view.favorites_group.set_visible(True)
        self.save_favorites()

    def remove_favorite_symbol(self, item):
        if item in self.favorites:
            self.favorites.remove(item)
        self._favorites_commands.discard(item[1])
        for symbol in [symbol for symbol in self.favorites_details]:
            if item[1] == symbol[1]:
                self.view.symbols_view_favorites.remove(symbol[5])
                self.favorites_details.remove(symbol)
        if len(self.favorites) == 0:
            self.view.favorites_group.set_visible(False)
        self.save_favorites()

    def toggle_favorite_symbol(self, folder, command):
        item = (folder, command)
        if command in self._favorites_commands:
            self.remove_favorite_symbol(item)
        else:
            self.add_favorite_symbol(item)

    def is_favorite_symbol(self, folder, command):
        return command in self._favorites_commands

    def save_favorites(self):
        ServiceLocator.get_settings().set_value('app_favorite_symbols', 'symbols', list(self.favorites))

    def save_recent(self):
        ServiceLocator.get_settings().set_value('app_recent_symbols', 'symbols', list(self.recent))

    def add_favorite_symbol_to_flowbox(self, item):
        # 共用 _append_symbol_to_flowbox；attrib 缺失时走 Favorites 专属清理。
        if self._append_symbol_to_flowbox(
                item, self.view.symbols_view_favorites, self.favorites_details) is None:
            self.remove_favorite_symbol(item)

    def on_flowbox_activated(self, flowbox, child, symbols_view):
        if self.workspace.active_document is None:
            return
        symbol = child.symbol_data
        self.workspace.actions.insert_symbol(None, [symbol[1]])
        self.add_recent_symbol((symbols_view.symbol_folder, symbol[1]))
        return True

    def wire_favorites(self):
        '''为主列表（SidebarSymbolsList）的每个符号按钮挂载 hover 预览。

        主列表按钮在 SymbolsPageView 构造时创建，早于本页面实例，故无法在
        构造期拿到 favorites 回调；统一在此（self 已就绪）补挂，使预览气泡
        带收藏切换按钮。Recent / Favorites 列表的按钮在各自 add_* 方法中创建，
        已直接挂载，无需重复。
        '''
        for symbols_view in self.view.symbols_views:
            folder = symbols_view.symbol_folder
            child = symbols_view.get_first_child()
            while child is not None:
                button = child.get_child()
                symbol = child.symbol_data
                if button is not None and symbol is not None:
                    attach_symbol_hover_preview(
                        button, symbol, folder=folder,
                        favorite_state_func=self.is_favorite_symbol,
                        favorite_toggle_func=self.toggle_favorite_symbol)
                child = child.get_next_sibling()

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
        # 去抖：取消上一次待执行的过滤，150ms 停顿后合并为一次 filter_sections。
        if self._search_idle_id is not None:
            GLib.source_remove(self._search_idle_id)
        self._search_idle_id = GLib.timeout_add(150, self._do_filter_sections)
        return True

    def _do_filter_sections(self):
        self._search_idle_id = None
        self.filter_sections()
        return False

    def filter_sections(self):
        any_symbols_found = False
        search_active = self.view.search_entry.get_text().strip() != ''

        search_words = self.view.search_entry.get_text().split()
        # labels / placeholders 除主分类外还含 Favorites、Recent 两个分组，
        # 故主分类 i 对应的 group 索引需跳过它们。原实现直接用 labels[i]，会
        # 错位把 Favorites/Recent 的可见性误判为主分类，且漏掉最后一个主分类。
        offset = len(self.view.labels) - len(self.view.symbols_views)
        for i, symbols_view in enumerate(self.view.symbols_views):
            symbols_view.visible_symbols = []

            for symbol in symbols_view.symbols:
                child = symbol[5]
                symbol_found = True
                for word in search_words:
                    # 同时按图标名（symbol[0]，如 'alpha'）与 LaTeX 命令
                    #（symbol[1]，如 '\alpha'）匹配，输入 'alpha' 或 '\alpha'
                    # 均可命中。任一词不命中即整体不命中，break 短路。
                    if symbol[0].find(word) == -1 and symbol[1].find(word) == -1:
                        symbol_found = False
                        break
                # 用 set_visible 替代原 remove + insert：所有 child 在 init 时
                # 已插入 FlowBox，过滤时仅切换可见性，避免每次搜索数百次
                # remove/insert 触发布局重排。FlowBox 跳过不可见 child。
                child.set_visible(symbol_found)
                if symbol_found:
                    symbols_view.visible_symbols.append(symbol)

            symbols_found = (len(symbols_view.visible_symbols) > 0)
            any_symbols_found |= symbols_found
            symbols_view.set_visible(symbols_found)
            self.view.labels[i + offset].set_visible(symbols_found)
            self.view.placeholders[i + offset].set_visible(symbols_found)

        if search_active and not any_symbols_found:
            self.view.search_entry.add_css_class('error')
            self.view.content_stack.set_visible_child_name('no-results')
        else:
            self.view.search_entry.remove_css_class('error')
            self.view.content_stack.set_visible_child_name('content')

    def on_symbols_view_size_allocate(self, *arguments):
        for symbols_view in self.view.symbols_views:
            allocation = symbols_view.get_allocation()
            if symbols_view.size != (allocation.width, allocation.height):
                symbols_view.size = (allocation.width, allocation.height)

        view = self.view.symbols_view_recent
        allocation = view.get_allocation()
        if self.recent_view_size != (allocation.width, allocation.height):
            self.recent_view_size = (allocation.width, allocation.height)

    def _get_scrolled_window(self):
        return self.view.scrolled_window

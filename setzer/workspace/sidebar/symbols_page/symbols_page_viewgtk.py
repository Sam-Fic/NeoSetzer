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
from gi.repository import Gdk, Gtk, Adw, Pango

import xml.etree.ElementTree as ET
import os

from setzer.widgets.search_entry.search_entry import SearchEntry
from setzer.app.service_locator import ServiceLocator


class SymbolsPageView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        # 顶部内嵌工具栏：左侧随滚动更新的“当前分区”标题，右侧 linked 的
        # 上一段 / 下一段导航按钮，再接一个独立的查找切换按钮。外观由
        # .sidebar-toolbar 与 Document Structure 页统一。
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        self.section_label = Gtk.Label(label='')
        self.section_label.add_css_class('dim-label')
        self.section_label.add_css_class('sidebar-section-title')
        self.section_label.set_halign(Gtk.Align.START)
        self.section_label.set_hexpand(True)
        self.section_label.set_xalign(0.0)
        self.section_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.toolbar.append(self.section_label)

        self.nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        self.prev_button = Gtk.Button(icon_name='go-up-symbolic')
        self.prev_button.set_can_focus(False)
        self.prev_button.add_css_class('flat')
        self.prev_button.set_tooltip_text(_('Previous section'))
        self.nav_box.append(self.prev_button)

        self.next_button = Gtk.Button(icon_name='go-down-symbolic')
        self.next_button.set_can_focus(False)
        self.next_button.add_css_class('flat')
        self.next_button.set_tooltip_text(_('Next section'))
        self.nav_box.append(self.next_button)

        self.toolbar.append(self.nav_box)

        self.search_button = Gtk.ToggleButton()
        self.search_button.set_icon_name('edit-find-symbolic')
        self.search_button.set_can_focus(False)
        self.search_button.add_css_class('flat')
        self.search_button.set_tooltip_text(_('Find'))
        # 与左侧 nav_box 的间距由 .toolbar-button-spaced CSS class 提供
        # （引用 --setzer-spacing-sm 变量），替代原 set_margin_start(6) 硬编码。
        self.search_button.add_css_class('toolbar-button-spaced')
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

        # Adw.PreferencesPage 提供原生分组标题与滚动；其内部第一个子控件是
        # Gtk.ScrolledWindow，逻辑层通过 self.scrolled_window 访问其 vadjustment。
        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)

        self.no_results_status = Adw.StatusPage()
        self.no_results_status.set_icon_name('edit-find-symbolic')
        self.no_results_status.set_title(_('No Results'))
        self.no_results_status.set_description(_('No symbols match your search'))
        self.no_results_status.set_vexpand(True)
        self.no_results_status.set_valign(Gtk.Align.CENTER)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)
        self.content_stack.add_named(self.page, 'content')
        self.content_stack.add_named(self.no_results_status, 'no-results')
        self.append(self.content_stack)

        self.scrolled_window = self.page.get_first_child()

        self.symbols_views = list()
        self.labels = list()          # Adw.PreferencesGroup 引用，供滚动导航取 y 偏移
        self.placeholders = list()    # 与 labels 对应的 group（搜索过滤时统一显隐）

        self.symbols_view_recent = Gtk.FlowBox()
        self.symbols_view_recent.add_css_class('symbols-flowbox')
        self.symbols_view_recent.set_homogeneous(False)
        self.symbols_view_recent.set_valign(Gtk.Align.START)
        # 关闭选中态：插入符号后不残留高亮（child-activated 仍会正常触发）。
        self.symbols_view_recent.set_selection_mode(Gtk.SelectionMode.NONE)
        self.add_category(_('Recent'), self.symbols_view_recent)

        self.symbols_view_favorites = Gtk.FlowBox()
        self.symbols_view_favorites.add_css_class('symbols-flowbox')
        self.symbols_view_favorites.set_homogeneous(False)
        self.symbols_view_favorites.set_valign(Gtk.Align.START)
        self.symbols_view_favorites.set_selection_mode(Gtk.SelectionMode.NONE)
        # Favorites 放在 Recent 之前：用户最常点，减少滚动。空时由
        # SymbolsPage 控制可见性（无收藏则隐藏整个分类）。
        self.favorites_group = self.add_category(_('Favorites'), self.symbols_view_favorites)

        # 每项: [folder, icon_name(预留备用), label, symbol_width]。
        # 原实现第 4 项是 'SidebarSymbolsList("folder", width)' 字符串，再 eval()
        # 执行——eval 是反模式（无法静态分析、linter 无法追踪调用关系），且若
        # 将来改为从外部数据加载即成 RCE 入口。改为直接存 width 整数，在
        # init_symbols_lists 中直接调用构造函数。
        self.symbols_lists = list()
        self.symbols_lists.append(['greek_letters', 'own-symbols-greek-letters-symbolic', _('Greek Letters'), 25])
        self.symbols_lists.append(['arrows', 'own-symbols-arrows-symbolic', _('Arrows'), 48])
        self.symbols_lists.append(['relations', 'own-symbols-relations-symbolic', _('Relations'), 39])
        self.symbols_lists.append(['operators', 'own-symbols-operators-symbolic', _('Operators'), 47])
        self.symbols_lists.append(['misc_math', 'own-symbols-misc-math-symbolic', _('Misc. Math'), 42])
        self.symbols_lists.append(['misc_text', 'insert-text-symbolic', _('Misc. Symbols'), 38])

        self.init_symbols_lists()

    def add_category(self, title, flowbox):
        group = Adw.PreferencesGroup()
        group.set_title(title)
        group.add(flowbox)
        self.page.add(group)
        self.labels.append(group)
        self.placeholders.append(group)
        return group

    def init_symbols_lists(self):
        for folder, icon, label, width in self.symbols_lists:
            symbols_list_view = SidebarSymbolsList(folder, width)
            self.add_category(label, symbols_list_view)
            self.symbols_views.append(symbols_list_view)


class SidebarSymbolsList(Gtk.FlowBox):

    def __init__(self, symbol_folder, symbol_width):
        Gtk.FlowBox.__init__(self)
        self.add_css_class('symbols-flowbox')

        self.symbol_folder = symbol_folder
        self.symbol_width = symbol_width

        self.size = None

        # symbols: icon name, latex code
        self.symbols = list()
        self.visible_symbols = list()

        self.set_homogeneous(False)
        self.set_valign(Gtk.Align.START)
        # 关闭选中态：插入符号后不残留高亮（child-activated 仍会正常触发）。
        self.set_selection_mode(Gtk.SelectionMode.NONE)

        xml_tree = ET.parse(os.path.join(ServiceLocator.get_resources_path(), 'symbols', symbol_folder + '.xml'))
        xml_root = xml_tree.getroot()
        for symbol_tag in xml_root:
            self.symbols.append([symbol_tag.attrib['file'].rsplit('.')[0], symbol_tag.attrib['command'], symbol_tag.attrib.get('package', None), int(symbol_tag.attrib.get('original_width', 10)), int(symbol_tag.attrib.get('original_height', 10))])

        self.init_symbols_list()

    def init_symbols_list(self):
        for symbol in self.symbols:
            size = max(symbol[3], symbol[4])

            image = Gtk.Image(icon_name='sidebar-' + symbol[0] + '-symbolic')
            image.set_pixel_size(int(size * 1.5))
            tooltip_text = symbol[1]
            if symbol[2] != None:
                tooltip_text += ' (' + _('Package') + ': ' + symbol[2] + ')'
            image.set_tooltip_text(tooltip_text)

            # 用 Gtk.Button 包裹 Image 以获得原生按钮语义与键盘可达性，
            # 再放进 Gtk.FlowBoxChild；点击经 FlowBox 的 child-activated 信号处理。
            button = Gtk.Button(child=image)
            button.add_css_class('flat')
            button.set_tooltip_text(tooltip_text)
            label_prop = getattr(Gtk.AccessibleProperty, 'LABEL', None)
            if label_prop is not None:
                try:
                    button.update_property(label_prop, _('Insert') + ' ' + symbol[1])
                except TypeError:
                    # 某些 PyGObject 版本的 update_property 绑定与 GTK 头不匹配，
                    # 调用即抛 TypeError。a11y 标签为增强项，失败则跳过。
                    pass
            # 主列表符号的 hover 预览（含收藏按钮）由 SymbolsPage.wire_favorites
            # 在页面初始化完成后统一挂载：此时才能拿到 favorites 回调。
            child = Gtk.FlowBoxChild()
            child.set_child(button)
            child.symbol_data = symbol
            symbol.append(child)
            self.visible_symbols.append(symbol)
            self.insert(child, -1)

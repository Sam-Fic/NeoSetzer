#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2026-present Sam-Fic
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
from gi.repository import Adw, Gtk


class SidebarListView(Gtk.Box):
    '''Adw.Sidebar 宿主：files/labels/todos 三个平铺列表子页的公共基类。

    迁移自 StructureWidget 的 Gtk.ListBox + Adw.ActionRow 方案，改用
    libadwaita 1.9 的 Adw.Sidebar 吃官方现成能力：

    - 分组：Adw.SidebarSection 承担产品级分组（files 按主文档/包含/文献、
      labels/todos 按来源文档）；空分组在重建时不添加，自然隐藏。
    - 过滤：常驻 GtkAnyFilter（title OR subtitle 的忽略大小写子串匹配，
      GtkStringFilter 默认即 ignore-case + substring），页级搜索框经
      filter_items() 下发。Adw.Sidebar 内建过滤，无搜索高亮加粗。
    - 右键菜单：sidebar :menu-model + setup-menu 信号设置上下文，
      替代每行手挂 GestureClick + Gtk.PopoverMenu 的弹出代码。
    - 选中/键盘导航：Adw.Sidebar 内建（navigation-sidebar 官方高亮样式，
      原 .accent 手写高亮方案不再使用）。
    - 空态：sidebar :placeholder 在「无数据」与「搜索无命中」（过滤后
      items 为 0）两种情形下自动接管整个区域（adw-sidebar.c
      update_placeholder），文案由子类经 set_placeholder_info 提供，
      搜索时页级会改写为 No Results。

    presenter 契约与迁移前一致：
    - update_items → self.populate()（子类全量重建 sections/items）；
    - activated → self.model.on_row_activated(item)，载荷挂在
      item.item_data（AdwSidebarItem 是 GObject，PyGObject 允许挂动态
      属性），presenter 的解包代码无需改动。
    '''

    def __init__(self, model):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        self.model = model

        # 常驻过滤器：title OR subtitle 两条 StringFilter 任一命中即保留
        # （GtkAnyFilter 语义）。search 属性由 filter_items() 更新。
        self._title_filter = Gtk.StringFilter()
        self._title_filter.set_expression(Gtk.PropertyExpression.new(Adw.SidebarItem, None, 'title'))
        self._subtitle_filter = Gtk.StringFilter()
        self._subtitle_filter.set_expression(Gtk.PropertyExpression.new(Adw.SidebarItem, None, 'subtitle'))
        filter_model = Gtk.AnyFilter()
        filter_model.append(self._title_filter)
        filter_model.append(self._subtitle_filter)

        self.sidebar = Adw.Sidebar()
        # 水平边距与上方搜索框（.sidebar-search-bar 的 10px）对齐
        self.sidebar.add_css_class('structure-sidebar')
        self.sidebar.set_filter(filter_model)
        self.sidebar.set_vexpand(True)
        self.sidebar.connect('activated', self._on_activated)
        self.sidebar.connect('setup-menu', self._on_setup_menu)

        # 空态/搜索无命中统一由 placeholder 承担：Adw.Sidebar 在过滤后
        # items 为 0 时隐藏内建滚动区、显示 placeholder（官方空态机制），
        # 原 StructureWidget.empty_state + 页级 no-results 态一并退役。
        self.placeholder = Adw.StatusPage()
        self.placeholder.add_css_class('compact')
        self.placeholder.add_css_class('sidebar-empty-state')
        self.sidebar.set_placeholder(self.placeholder)

        # 子类可往 header_box 里放页内工具行（如 todos 的 show_all 开关），
        # 位于 sidebar 之上。
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.header_box)
        self.append(self.sidebar)

        self._sections = {}
        self._items = list()

        # 签名短路：与 StructureWidget.populate_if_changed 同机制。打字时
        # 数据未变 → 跳过重建；签名含 id(document) → 文档切换强制重建。
        self._last_signature = None

    # ---------- 行管理（presenter 调用） ----------

    def populate_if_changed(self, signature):
        '''相同签名返回 False（调用方跳过重建）；否则记录并返回 True。'''
        if signature == self._last_signature:
            return False
        self._last_signature = signature
        return True

    def clear_items(self):
        self._last_signature = None
        self._sections.clear()
        self._items.clear()
        self.sidebar.remove_all()

    def add_section(self, name, title):
        '''添加一个分组（AdwSidebarSection），返回其名供 append_item 使用。

        重建是全量清空再添加，空分组不会被添加，等效于按内容隐藏。
        '''
        section = Adw.SidebarSection()
        section.set_title(title)
        self.sidebar.append(section)
        self._sections[name] = section
        return section

    def make_item(self, title, icon_name=None, subtitle=None, tooltip=None, suffix=None):
        item = Adw.SidebarItem(title=title)
        if icon_name is not None:
            item.set_icon_name(icon_name)
        if subtitle:
            item.set_subtitle(subtitle)
        if tooltip is not None:
            item.set_tooltip(tooltip)
        if suffix is not None:
            item.set_suffix(suffix)
        return item

    def append_item(self, section_name, item):
        self._sections[section_name].append(item)
        self._items.append(item)

    def set_placeholder_info(self, icon_name, title, description=None):
        '''设置无数据时的 placeholder 文案。搜索无命中时页级经
        set_placeholder_query(True) 改写为 No Results。'''
        self._placeholder_info = (icon_name, title, description)
        self._apply_placeholder(query=False)

    def _apply_placeholder(self, query):
        icon_name, title, description = self._placeholder_info
        if query:
            title, description = _('No Results'), None
        self.placeholder.set_icon_name(icon_name)
        self.placeholder.set_title(title)
        self.placeholder.set_description(description)

    def select_first(self):
        '''重建后选中首项，对齐原 ListBox「选中首可见行」的兜底行为。'''
        if self._items:
            self.sidebar.set_selected(0)

    # ---------- 过滤（页级搜索下发） ----------

    def filter_items(self, query):
        '''设置过滤词并返回过滤后条目数（供页级判定与日志）。

        query 为空（None 或 ''）时 StringFilter 的 search 置 None 即不过滤。
        '''
        text = query if query else None
        self._title_filter.set_search(text)
        self._subtitle_filter.set_search(text)
        self._apply_placeholder(query=bool(query))
        return self.sidebar.get_items().get_n_items()

    # ---------- 信号 ----------

    def _on_activated(self, sidebar, index):
        item = sidebar.get_item(index)
        if item is not None and getattr(item, 'item_data', None) is not None:
            self.model.on_row_activated(item)

    def _on_setup_menu(self, sidebar, item):
        '''右键菜单弹出前回调：item 为被右键的 AdwSidebarItem（点空白处
        时为 None）。子类覆写以更新 _ctx_* 上下文；菜单模型为常驻静态模型
        的子类（labels/todos）无需重建，条件化菜单的子类（files）在此重建。
        '''
        pass

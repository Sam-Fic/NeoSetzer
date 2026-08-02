#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
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
from gi.repository import Gtk, GLib, GObject, Adw, Pango, Gio

from setzer.widgets.search_entry.search_entry import SearchEntry
from setzer.helpers.scroll_animator import ScrollAnimatorMixin


class DocumentStructurePage(Gtk.Box, ScrollAnimatorMixin):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.sections = dict()
        self.section_widgets = dict()
        self.section_titles = list()
        self.scroll_to = None
        self._current_section_title = ''  # 缓存 section title，用于变化检测
        self._groups_cache = None         # page 的 group 列表缓存
        self._closing_search = False      # 关闭搜索时阻止 set_text 副作用
        self._suppress_scroll_handler = False  # 关闭搜索时跳过 on_scroll_or_resize
        # 跟踪 section 导航滚动动画的 timeout id。原实现不跟踪，连续点击
        # 下一/上一段时多个 timeout 同时写 adjustment 造成抖动；widget 销毁
        # 时（duration 0.2s 内）timeout 仍访问已释放的 scrolled_window。
        self._scroll_timeout_id = None
        # 搜索过滤去抖 id：on_search_changed 原每次按键都对所有 section 的
        # ListBox 全量 filter_rows（大文档数百行）。150ms 停顿后合并为一次。
        self._filter_idle_id = None
        # 滚动去抖 id：on_scroll_or_resize 由 vadjustment 的 value-changed 每像素
        # 触发，原每次都遍历所有 group（get_visible_sections）+ 2 次 C 调用/group。
        # 合并为一次 idle 更新按钮敏感度 + section 标签。
        self._scroll_update_idle_id = None

        self.add_buttons()

        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)

        self.no_results_status = Adw.StatusPage()
        self.no_results_status.set_icon_name('action-unavailable-symbolic')
        self.no_results_status.set_title(_('No Results'))
        self.no_results_status.set_description(_('No sections match your search'))
        self.no_results_status.set_vexpand(True)
        self.no_results_status.set_valign(Gtk.Align.CENTER)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)
        self.content_stack.add_named(self.page, 'content')
        self.content_stack.add_named(self.no_results_status, 'no-results')

        self.no_document_status = Adw.StatusPage()
        self.no_document_status.set_icon_name('document-open-symbolic')
        self.no_document_status.set_title(_('No Document'))
        self.no_document_status.set_description(_('Open a document to see its structure.'))
        self.no_document_status.set_vexpand(True)
        self.no_document_status.set_valign(Gtk.Align.CENTER)
        self.content_stack.add_named(self.no_document_status, 'no-document')

        self.append(self.content_stack)

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

        # 初始化下拉菜单
        self._init_section_menu()

        # widget 销毁时取消进行中的滚动动画 timeout，避免回调访问已释放的
        # scrolled_window 并持有引用阻碍 GC。
        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        self._cancel_scroll_animation()
        if self._filter_idle_id is not None:
            try:
                GLib.source_remove(self._filter_idle_id)
            except (ValueError, RuntimeError):
                pass
            self._filter_idle_id = None
        if self._scroll_update_idle_id is not None:
            try:
                GLib.source_remove(self._scroll_update_idle_id)
            except (ValueError, RuntimeError):
                pass
            self._scroll_update_idle_id = None

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
        self._rebuild_section_menu()
        return group

    def set_section_visible(self, name, visible):
        self.sections[name].set_visible(visible)

    def show_no_document(self):
        self.content_stack.set_visible_child_name('no-document')

    def show_content(self):
        self.content_stack.set_visible_child_name('content')

    def add_buttons(self):
        # 顶部内嵌工具栏：左侧为随滚动更新的“当前分区”标题，右侧为
        # linked 的上一段 / 下一段导航按钮。两页（Document Structure /
        # Symbols）共享此结构，外观由 .sidebar-toolbar 统一。
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')

        # 下拉菜单：Gtk.MenuButton + .flat 样式（与项目中其他 MenuButton 一致）
        self.section_menu_button = Gtk.MenuButton()
        self.section_menu_button.add_css_class('flat')
        self.section_menu_button.set_can_focus(False)
        self.section_menu_button.set_halign(Gtk.Align.START)
        self.section_menu_button.set_hexpand(True)
        self.section_menu_button.set_tooltip_text(_('Jump to section'))

        menu_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.section_menu_label = Gtk.Label(label='')
        self.section_menu_label.add_css_class('dim-label')
        self.section_menu_label.set_xalign(0.0)
        self.section_menu_label.set_ellipsize(Pango.EllipsizeMode.END)
        menu_box.append(self.section_menu_label)

        arrow_icon = Gtk.Image(icon_name='pan-down-symbolic')
        arrow_icon.set_pixel_size(12)
        arrow_icon.add_css_class('dim-label')
        menu_box.append(arrow_icon)

        self.section_menu_button.set_child(menu_box)
        self.toolbar.append(self.section_menu_button)

        self.nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.prev_button = Gtk.Button()
        self.prev_button.set_icon_name('go-up-symbolic')
        self.prev_button.set_tooltip_text(_('Previous section') + '  (Alt+Up)')
        self.prev_button.add_css_class('flat')
        self.prev_button.set_can_focus(False)
        self.nav_box.append(self.prev_button)

        self.next_button = Gtk.Button()
        self.next_button.set_icon_name('go-down-symbolic')
        self.next_button.set_tooltip_text(_('Next section') + '  (Alt+Down)')
        self.next_button.add_css_class('flat')
        self.next_button.set_can_focus(False)
        self.nav_box.append(self.next_button)

        self.toolbar.append(self.nav_box)

        self.search_button = Gtk.ToggleButton()
        self.search_button.set_icon_name('edit-find-symbolic')
        self.search_button.set_can_focus(False)
        self.search_button.add_css_class('flat')
        self.search_button.set_tooltip_text(_('Search document structure'))
        self.toolbar.append(self.search_button)

        self.switch_button = Gtk.Button()
        self.switch_button.set_child(Gtk.Image(icon_name='emoji-symbols-symbolic'))
        self.switch_button.set_can_focus(False)
        self.switch_button.set_tooltip_text(_('Switch to Symbols'))
        self.switch_button.add_css_class('flat')
        self.toolbar.append(self.switch_button)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_revealer.set_transition_duration(250)
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.search_box.add_css_class('sidebar-search-bar')

        self.search_entry = SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_box.append(self.search_entry)

        self.search_revealer.set_child(self.search_box)

        self.append(self.toolbar)
        self.append(self.search_revealer)

    def on_scroll_or_resize(self, *args):
        # 去抖：vadjustment 的 value-changed 每像素触发一次，原每次都遍历所有
        # group（get_visible_sections，每组 2 次 C 调用）+ 重算 section 标题。
        # 合并为一次 idle：连续滚动期间仅在每个事件循环空隙更新一次 UI。
        if self._scroll_update_idle_id is None:
            self._scroll_update_idle_id = GLib.idle_add(self._on_scroll_or_resize_idle)

    def _on_scroll_or_resize_idle(self):
        self._scroll_update_idle_id = None
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
            self.section_menu_label.set_text(current_title)
        return False

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

    def _get_scrolled_window(self):
        return self.scrolled_window

    def on_search_button_toggled(self, button):
        if button.get_active():
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(True)
            self.search_entry.grab_focus()
            self.filter_sections('')
        else:
            # 先转移焦点到 scrolled_window，避免 Revealer 隐藏时搜索框失去焦点
            # 导致 GTK 自动滚动到"下一个可聚焦控件"（越界到底部）。
            self.scrolled_window.grab_focus()
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(False)

    def on_search_stopped(self, entry):
        self.search_button.set_active(False)

    def on_search_changed(self, entry):
        # 去抖：取消上一次待执行的过滤，150ms 停顿后合并为一次 filter_sections。
        # 连续输入一个词的每个字符原本都触发全量 filter_rows，现合并为词末一次。
        if self._filter_idle_id is not None:
            GLib.source_remove(self._filter_idle_id)
        self._filter_idle_id = GLib.timeout_add(150, self._do_filter_sections)
        return True

    def _do_filter_sections(self):
        self._filter_idle_id = None
        self.filter_sections(self.search_entry.get_text())
        return False

    def filter_sections(self, query):
        any_visible = False
        for name, group in self.sections.items():
            widget = self.section_widgets.get(name)
            if widget and hasattr(widget, 'filter_rows'):
                section_visible = widget.filter_rows(query)
                group.set_visible(section_visible if query else True)
                any_visible |= section_visible
            else:
                group.set_visible(not bool(query))
        if query and not any_visible:
            self.content_stack.set_visible_child_name('no-results')
        else:
            self.content_stack.set_visible_child_name('content')
        # set_visible 仅改 group 可见性，不改变 group 列表结构，故 _groups_cache
        #（持有全部 group 引用）仍有效。get_visible_sections 会重新读
        # group.get_visible()，无需在此清空 cache 诱发 _collect_groups 全树遍历。
        # 原实现每次搜索字符都清空，搜索期间滚动结果会反复重建 cache。

        # 搜索无结果时隐藏菜单按钮
        self.section_menu_button.set_visible(not query or any_visible)

        # 过滤后重建菜单（只显示可见 section）
        self._rebuild_section_menu()

    def _init_section_menu(self):
        """初始化下拉菜单的基础设施（menu model / action group / popover class）。"""
        self._section_menu = Gio.Menu()
        self._section_menu_section = Gio.Menu()
        self._section_menu.append_section(None, self._section_menu_section)

        self._section_action_group = Gio.SimpleActionGroup()
        self.section_menu_button.insert_action_group('structure', self._section_action_group)
        self.section_menu_button.set_menu_model(self._section_menu)

        popover = self.section_menu_button.get_popover()
        if popover is not None:
            popover.add_css_class('menu')

    def _rebuild_section_menu(self):
        """根据当前 sections 重建菜单项和 actions。"""
        # 清除旧 actions
        for action in getattr(self, '_section_actions', {}).values():
            self._section_action_group.remove_action(action.get_name())
        self._section_actions = {}

        self._section_menu_section.remove_all()
        groups = self.get_page_groups()
        for i, (name, group) in enumerate(self.sections.items()):
            if group.get_visible():
                action_name = f'jump-{i}'
                action = Gio.SimpleAction.new(action_name, None)
                action.connect('activate', self._on_section_menu_item_activated, name)
                self._section_action_group.add_action(action)
                self._section_actions[i] = action

                title = group.get_title() or name
                menu_item = Gio.MenuItem.new(title, f'structure.{action_name}')
                self._section_menu_section.append_item(menu_item)

    def _on_section_menu_item_activated(self, action, parameter, name):
        """菜单项点击：滚动到对应 section。"""
        if name in self.sections:
            group = self.sections[name]
            self.scroll_view(group.get_allocation().y)

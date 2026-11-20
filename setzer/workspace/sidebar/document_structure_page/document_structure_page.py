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
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Adw

from setzer.widgets.search_entry.search_entry import SearchEntry
from setzer.helpers import animation_prefs
from setzer.workspace.sidebar.sidebar_viewgtk import build_panel_switch_group


# 子视图档位：切换式重构后，原「纵向堆叠的 PreferencesPage 长页」拆为五页
# （Adw.Sidebar 内建滚动视口塞不进整页滚动流，见 adw-sidebar.c:2183）。
# 档位顺序即默认页序，图标全部取自本库/Adwaita 已有素材。
SUBVIEW_OPTIONS = (
    ('structure', 'catlog-symbolic', 'Document Structure'),
    ('files', 'folder-symbolic', 'Files'),
    ('labels', 'tag-symbolic', 'Labels'),
    ('todos', 'star-symbolic', 'To-Dos'),
    ('stats', 'info-outline-symbolic', 'Document Stats'),
)


def build_subview_switch_group():
    '''构建子视图切换器（Adw.ToggleGroup 图标档）。

    与顶层面板切换器（build_panel_switch_group）同款写法：图标档位 +
    当前档高亮，不加 .flat 保留分段底槽。
    '''
    group = Adw.ToggleGroup()
    group.set_valign(Gtk.Align.CENTER)
    group.set_homogeneous(True)
    for name, icon_name, label in SUBVIEW_OPTIONS:
        group.add(Adw.Toggle(name=name, icon_name=icon_name, tooltip=_(label)))
    return group


class DocumentStructurePage(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        # sections/section_widgets：name → 子视图页容器。stats 的
        # DocumentStats.set_group(container) 用 sections['stats'] 做
        # 非 latex 文档时的整页隐藏。
        self.sections = dict()
        self.section_widgets = dict()
        self._filter_idle_id = None
        # 子视图切换防递归：ToggleGroup notify::active → set_visible_child_name，
        # stack notify::visible-child → set_active_name，双向同步需打标。
        self._syncing_subview = False

        self.add_buttons()

        # 五页子视图 stack（content 态的实体）。
        self.view_stack = Gtk.Stack()
        self.view_stack.set_vexpand(True)
        animation_prefs.set_transition(
            self.view_stack, Gtk.StackTransitionType.CROSSFADE, 150)

        self.no_document_status = Adw.StatusPage()
        self.no_document_status.add_css_class('compact')
        self.no_document_status.add_css_class('sidebar-empty-state')
        self.no_document_status.set_icon_name('document-open-symbolic')
        self.no_document_status.set_title(_('No Document'))
        self.no_document_status.set_description(_('Open a document to see its structure.'))
        self.no_document_status.set_vexpand(True)
        self.no_document_status.set_valign(Gtk.Align.CENTER)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        animation_prefs.set_transition(
            self.content_stack, Gtk.StackTransitionType.CROSSFADE, 150)
        self.content_stack.add_named(self.view_stack, 'content')
        self.content_stack.add_named(self.no_document_status, 'no-document')

        self.append(self.content_stack)

        self.search_button.connect('toggled', self.on_search_button_toggled)
        self.search_entry.connect('stop-search', self.on_search_stopped)
        self.search_entry.connect('changed', self.on_search_changed)

        # 子视图切换器与 view_stack 双向同步（同 Sidebar 面板切换器的镜像
        # 模式；权威状态是 stack 的可见子页）。初始对齐延迟到首个 add_section：
        # __init__ 时 sidebar.py 尚未调用 add_section，view_stack 还是空的
        # （set_visible_child_name 会打 CRITICAL）；而 Gtk.Stack 默认显示第一个
        # 添加的页（files 先于 structure 注册），与 ToggleGroup 默认激活的第一档
        # （structure）错位——首个子页落位时按档位切正。
        self.subview_switch_group.connect('notify::active', self._on_subview_switch_activated)
        self.view_stack.connect('notify::visible-child', self._on_view_stack_changed)
        # 默认档固定为第一档——不能在此读 get_active_name()：add_section 时
        # Gtk.Stack 首页注册会发射 notify 把 ToggleGroup 拨到 files，读到的
        # 是被污染的状态（详见 _align_initial_subview）。
        self._pending_initial_subview = SUBVIEW_OPTIONS[0][0]

        # widget 销毁时取消待执行的过滤去抖。
        self.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        if self._filter_idle_id is not None:
            try:
                GLib.source_remove(self._filter_idle_id)
            except (ValueError, RuntimeError):
                pass
            self._filter_idle_id = None

    def add_section(self, name, title, widget):
        '''登记子视图页。title 参数保留（原 PreferencesGroup 标题），分组
        标题已由 Adw.SidebarSection / 各页内部承担，此处不再渲染。

        structure 与 stats 原依赖 PreferencesPage 的整页滚动，拆页后各自
        补一个 ScrolledWindow；三个 Adw.Sidebar 子页自带滚动视口，直接入栈。
        '''
        if name == 'structure':
            container = self._wrap_tree_view(widget)
        elif name == 'stats':
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_child(widget)
            container = scrolled
        else:
            container = widget
        self.view_stack.add_named(container, name)
        self.sections[name] = container
        self.section_widgets[name] = widget
        # 默认档位页落位时才对齐两端：add_section 首页（files）注册时
        # Gtk.Stack 自动设其为可见页并发射 notify，_on_view_stack_changed
        # 会把 ToggleGroup 拨到 files——对齐值不能用此时的 get_active_name()
        # （被污染），必须用 pending 本身。
        if name == self._pending_initial_subview:
            self._pending_initial_subview = None
            self._syncing_subview = True
            try:
                self.subview_switch_group.set_active_name(name)
                self.view_stack.set_visible_child_name(name)
            finally:
                self._syncing_subview = False
        return container

    def _wrap_tree_view(self, widget):
        '''树页容器：滚动列表 + 搜索无命中页（树不走 Adw.Sidebar 的
        placeholder 机制，filter_rows 后无命中时在页内切换）。'''
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(widget)

        no_results = Adw.StatusPage()
        no_results.add_css_class('compact')
        no_results.add_css_class('sidebar-empty-state')
        no_results.set_icon_name('action-unavailable-symbolic')
        no_results.set_title(_('No Results'))

        stack = Gtk.Stack()
        animation_prefs.set_transition(
            stack, Gtk.StackTransitionType.CROSSFADE, 150)
        stack.add_named(scrolled, 'list')
        stack.add_named(no_results, 'no-results')
        self._tree_stack = stack
        return stack

    def show_no_document(self):
        self.content_stack.set_visible_child_name('no-document')

    def show_content(self):
        self.content_stack.set_visible_child_name('content')

    def add_buttons(self):
        # 第一行工具栏：左上搜索开关，右侧顶层面板切换器（Document
        # Structure / Symbols / Git / AI Agent）。两页（Document Structure /
        # Symbols）共享 .sidebar-toolbar 外观。
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')

        self.search_button = Gtk.ToggleButton()
        self.search_button.set_icon_name('edit-find-symbolic')
        self.search_button.set_can_focus(True)
        self.search_button.add_css_class('flat')
        self.search_button.set_tooltip_text(_('Search document structure'))
        self.search_button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Search document structure')])
        self.toolbar.append(self.search_button)

        # 弹性隔断：把顶层面板切换器推到右侧。
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.toolbar.append(spacer)

        # 面板切换：Adw.ToggleGroup 图标档（当前档高亮），取代原来「单按钮、
        # 图标显示目标面板」的写法；同步由 Sidebar.set_visible_child_name() 统一做。
        self.switch_group = build_panel_switch_group()
        self.toolbar.append(self.switch_group)

        # 第二行：五档子视图切换器单独一行（Jump-to-section 菜单已被其
        # 取代——原长页的分区跳转对象不复存在）。.subview-row 顶距归零：
        # 行间空隙由第一行的 7px 底距单独承担，见 style_gtk.css。
        self.subview_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.subview_toolbar.add_css_class('sidebar-toolbar')
        self.subview_toolbar.add_css_class('subview-row')
        self.subview_switch_group = build_subview_switch_group()
        self.subview_switch_group.set_hexpand(True)
        self.subview_toolbar.append(self.subview_switch_group)

        self.search_revealer = Gtk.Revealer()
        animation_prefs.set_transition(
            self.search_revealer, Gtk.RevealerTransitionType.SLIDE_DOWN, 250)
        self.search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.search_box.add_css_class('sidebar-search-bar')

        self.search_entry = SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_box.append(self.search_entry)

        self.search_revealer.set_child(self.search_box)

        self.append(self.toolbar)
        self.append(self.subview_toolbar)
        self.append(self.search_revealer)

    # ---------- 子视图切换 ----------

    def _on_subview_switch_activated(self, group, pspec):
        if self._syncing_subview:
            return
        name = group.get_active_name()
        if name is not None:
            self._syncing_subview = True
            try:
                self.view_stack.set_visible_child_name(name)
            finally:
                self._syncing_subview = False

    def _on_view_stack_changed(self, stack, pspec):
        if self._syncing_subview:
            return
        name = stack.get_visible_child_name()
        self._syncing_subview = True
        try:
            if name is not None and self.subview_switch_group.get_toggle_by_name(name) is not None:
                self.subview_switch_group.set_active_name(name)
        finally:
            self._syncing_subview = False

    def scroll_next_section(self):
        '''Alt+Down 快捷键：原为长页内跨分区滚动，切换式重构后对象消失，
        重绑为下一个子视图档位（循环）。方法名保留以兼容 shortcut_accels.py
        与 symbols_page 的同名鸭子接口。'''
        self._cycle_subview(1)

    def scroll_prev_section(self):
        '''Alt+Up 快捷键，见 scroll_next_section 注释。'''
        self._cycle_subview(-1)

    def _cycle_subview(self, direction):
        names = [option[0] for option in SUBVIEW_OPTIONS]
        current = self.view_stack.get_visible_child_name()
        try:
            index = names.index(current)
        except ValueError:
            index = 0
        self._syncing_subview = True
        try:
            self.view_stack.set_visible_child_name(names[(index + direction) % len(names)])
        finally:
            self._syncing_subview = False

    # ---------- 搜索过滤 ----------

    def on_search_button_toggled(self, button):
        if button.get_active():
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(True)
            self.search_entry.grab_focus()
            self.filter_sections('')
        else:
            self.search_entry.set_text('')
            self.search_revealer.set_reveal_child(False)

    def on_search_stopped(self, entry):
        self.search_button.set_active(False)

    def on_search_changed(self, entry):
        # 去抖：取消上一次待执行的过滤，150ms 停顿后合并为一次 filter_sections。
        if self._filter_idle_id is not None:
            GLib.source_remove(self._filter_idle_id)
        self._filter_idle_id = GLib.timeout_add(150, self._do_filter_sections)
        return True

    def _do_filter_sections(self):
        self._filter_idle_id = None
        self.filter_sections(self.search_entry.get_text())
        return False

    def filter_sections(self, query):
        '''把搜索词下发到全部子视图。

        三个 Adw.Sidebar 子视图走内建 GtkFilter（filter_items 内部同步改写
        placeholder 文案：有搜索词 → No Results）；树页走 filter_rows 并按
        命中与否在页内 list/no-results 两态间切换。搜索全局生效：切换子视图
        可查看各页各自的命中情况，无需页级 no-results 态（已删）。
        '''
        any_tree_visible = self.section_widgets['structure'].filter_rows(query)
        self._tree_stack.set_visible_child_name(
            'no-results' if (query and not any_tree_visible) else 'list')
        for name in ('files', 'labels', 'todos'):
            widget = self.section_widgets.get(name)
            if widget is not None and hasattr(widget, 'filter_items'):
                widget.filter_items(query)

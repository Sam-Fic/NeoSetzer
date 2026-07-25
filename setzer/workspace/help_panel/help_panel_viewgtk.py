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
from gi.repository import Gtk, Adw, Pango

# WebKit 是可选依赖：某些 Linux 发行版（如极简 Flatpak 运行时）可能未安装
# webkit2gtk-6.0。缺失时帮助面板降级为"纯搜索 + 系统浏览器打开 HTML"模式：
# 搜索索引不依赖 WebKit，结果列表照常显示；点击结果时由 controller 调
# webbrowser.open 打开本地 HTML 文件。WebView 不可用时用 Gtk.Label 占位。
try:
    gi.require_version('WebKit', '6.0')
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    HAS_WEBKIT = False

from setzer.widgets.search_entry.search_entry import SearchEntry


class HelpPanelView(Gtk.Box):
    '''帮助面板的视图层。

    Pass-12 重构：与左侧栏（Symbols / Document Structure）保持一致的
    "内嵌工具栏 + 内容区" 结构：
      - 顶部 .sidebar-toolbar 工具栏：home / up / back / next（左侧），
        search_button（右侧）。
      - 下方 WebView 内容栈 + 搜索页。
    工具栏样式与左侧栏统一（.sidebar-toolbar CSS class），不再使用 Gtk.ActionBar
    （原 ActionBar 顶部有 inset 分隔线，与左侧栏样式不一致）。
    标题栏不再覆盖帮助面板，由 workspace_viewgtk 将 headerbar overlay 移到
    document_stack_wrapper 上，帮助侧栏整体与左侧栏行为一致。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_size_request(396, -1)
        self.add_css_class('help')

        # ---- 顶部内嵌工具栏（与左侧栏 .sidebar-toolbar 统一外观）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        self.home_button = Gtk.Button(icon_name='go-home-symbolic')
        self.home_button.set_tooltip_text(_('Home'))
        self.home_button.add_css_class('flat')
        self.home_button.set_can_focus(False)
        self.toolbar.append(self.home_button)

        self.up_button = Gtk.Button(icon_name='go-up-symbolic')
        self.up_button.set_tooltip_text(_('Top'))
        self.up_button.add_css_class('flat')
        self.up_button.set_can_focus(False)
        self.toolbar.append(self.up_button)

        self.back_button = Gtk.Button(icon_name='go-previous-symbolic')
        self.back_button.set_tooltip_text(_('Back'))
        self.back_button.add_css_class('flat')
        self.back_button.set_can_focus(False)
        self.toolbar.append(self.back_button)

        self.next_button = Gtk.Button(icon_name='go-next-symbolic')
        self.next_button.set_tooltip_text(_('Forward'))
        self.next_button.add_css_class('flat')
        self.next_button.set_can_focus(False)
        self.toolbar.append(self.next_button)

        # 占位 spacer 把 search_button 推到右侧
        self.toolbar_spacer = Gtk.Box()
        self.toolbar_spacer.set_hexpand(True)
        self.toolbar.append(self.toolbar_spacer)

        self.search_button = Gtk.ToggleButton()
        self.search_button.set_icon_name('edit-find-symbolic')
        self.search_button.set_tooltip_text(_('Find'))
        self.search_button.add_css_class('flat')
        self.search_button.set_can_focus(False)
        self.toolbar.append(self.search_button)

        self.append(self.toolbar)

        # Search page: a single Adw.Clamp wraps the vertical search content,
        # giving native bounded/centered width. The entry sits at the top,
        # results scroll below, and a compact StatusPage shows the no-results
        # empty state. This replaces the former double-CenterBox floating blob.
        self.search_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search_content_box.set_margin_top(12)
        self.search_content_box.set_margin_bottom(12)

        self.search_entry = SearchEntry()
        self.search_entry.set_placeholder_text(_('Search help'))
        self.search_content_box.append(self.search_entry)

        # 搜索结果计数：显示 "{n} results" 或空查询时隐藏。
        # dim-label 让计数低调不抢眼，与结果列表区分。
        self.result_count_label = Gtk.Label()
        self.result_count_label.set_xalign(0)
        self.result_count_label.set_margin_top(8)
        self.result_count_label.set_margin_start(2)
        self.result_count_label.add_css_class('dim-label')
        self.result_count_label.set_visible(False)
        self.search_content_box.append(self.result_count_label)

        self.search_results = Gtk.ListBox()
        self.search_results.set_selection_mode(Gtk.SelectionMode.NONE)
        self.search_results.set_can_focus(False)
        self.search_results.set_margin_top(12)
        self.search_scroll = Gtk.ScrolledWindow()
        self.search_scroll.set_vexpand(True)
        self.search_scroll.kinetic_scrolling = True
        self.search_scroll.overlay_scrolling = True
        self.search_scroll.set_child(self.search_results)
        self.search_content_box.append(self.search_scroll)

        self.no_results_slate = Adw.StatusPage()
        self.no_results_slate.add_css_class('compact')
        self.no_results_slate.set_icon_name('system-search-symbolic')
        self.no_results_slate.set_title(_('No results found'))
        self.no_results_slate.set_visible(False)
        self.no_results_slate.set_vexpand(True)
        self.no_results_slate.set_valign(Gtk.Align.CENTER)
        self.search_content_box.append(self.no_results_slate)

        self.initial_slate = Adw.StatusPage()
        self.initial_slate.add_css_class('compact')
        self.initial_slate.set_icon_name('system-search-symbolic')
        self.initial_slate.set_title(_('Search Help'))
        self.initial_slate.set_description(_('Type a keyword to search the documentation.'))
        self.initial_slate.set_visible(True)
        self.initial_slate.set_vexpand(True)
        self.initial_slate.set_valign(Gtk.Align.CENTER)
        self.search_content_box.append(self.initial_slate)

        self.search_clamp = Adw.Clamp()
        self.search_clamp.set_maximum_size(600)
        self.search_clamp.set_tightening_threshold(400)
        self.search_clamp.set_margin_start(12)
        self.search_clamp.set_margin_end(12)
        self.search_clamp.set_child(self.search_content_box)

        # WebKit 可用时创建 WebView 渲染帮助页面；不可用时降级为 Gtk.Label
        # 占位（显示提示文案）。搜索功能不依赖 WebKit，仍正常工作。
        if HAS_WEBKIT:
            self.content = WebKit.WebView()
            self.content.set_hexpand(True)
            self.content.set_vexpand(True)
            self.user_content_manager = self.content.get_user_content_manager()

            self.settings = self.content.get_settings()
            self.settings.set_enable_javascript(False)
            self.settings.set_enable_javascript_markup(False)
            self.settings.set_enable_developer_extras(False)
            self.settings.set_enable_page_cache(False)
            # Make help pages scroll smoothly with touchpads/mice.
            self.settings.set_enable_smooth_scrolling(True)

            # JavaScript 已禁用的可见指示器：原实现仅静默 set_enable_javascript(False)，
            # 若某帮助页依赖 JS 渲染（数学公式、交互示例），用户看到空白却不知原因。
            # 这里在工具栏右侧加一个 flat info 按钮，点击弹出 popover 说明策略。
            # 仅在 HAS_WEBKIT 时创建——无 WebKit 分支已有占位 Label 解释降级情形。
            # 用 MenuButton + Popover 而非常驻 Label：info 图标低调不抢眼，需要时
            # 点击查看详情，避免对绝大多数不依赖 JS 的静态帮助页造成视觉噪声。
            self.js_info_button = Gtk.MenuButton()
            self.js_info_button.set_icon_name('dialog-information-symbolic')
            self.js_info_button.set_tooltip_text(_('JavaScript is disabled'))
            self.js_info_button.add_css_class('flat')
            self.js_info_button.set_can_focus(False)

            js_popover = Gtk.Popover()
            js_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            js_box.set_spacing(6)
            js_box.set_margin_start(12)
            js_box.set_margin_end(12)
            js_box.set_margin_top(12)
            js_box.set_margin_bottom(12)

            js_title = Gtk.Label(label=_('JavaScript is disabled'))
            js_title.set_xalign(0)
            js_title.add_css_class('heading')
            js_box.append(js_title)

            js_body = Gtk.Label(
                label=_('Help pages render with JavaScript disabled for security. '
                        'Static documentation displays normally; interactive '
                        'examples that depend on JavaScript will not run.'))
            js_body.set_xalign(0)
            js_body.set_wrap(True)
            js_body.set_max_width_chars(42)
            js_body.add_css_class('dim-label')
            js_box.append(js_body)

            js_popover.set_child(js_box)
            self.js_info_button.set_popover(js_popover)
            self.toolbar.append(self.js_info_button)
        else:
            # 无 WebKit：用 Gtk.Label 提示用户。搜索仍可用（索引不依赖 WebKit），
            # 点击搜索结果时 controller 会调 webbrowser.open 打开 HTML。
            self.content = Gtk.Label()
            self.content.set_hexpand(True)
            self.content.set_vexpand(True)
            self.content.set_wrap(True)
            self.content.set_text(
                _('Help page rendering requires WebKit6. '
                  'Search is still available — click a result to open it '
                  'in your web browser.'))
            self.content.add_css_class('dim-label')
            self.content.set_margin_start(12)
            self.content.set_margin_end(12)
            self.content.set_margin_top(12)
            self.content.set_margin_bottom(12)
            self.user_content_manager = None
            self.settings = None

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_css_class('preview-card')
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.stack.add_named(self.content, 'content')
        self.stack.add_named(self.search_clamp, 'search')

        self.append(self.stack)

        self.search_result_items = list()


class SearchResultView(Gtk.ListBoxRow):

    def __init__(self, data):
        Gtk.ListBoxRow.__init__(self)
        self.set_can_focus(False)
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self.set_margin_start(15)
        self.set_margin_end(15)
        self.uri_ending = data[0]
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.box.set_spacing(2)
        self.text_label = Gtk.Label()
        self.text_label.set_markup(data[1])
        self.text_label.set_xalign(0)
        self.text_label.set_wrap(True)
        self.text_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text_label.set_selectable(False)
        self.location_label = Gtk.Label()
        self.location_label.set_markup(data[2])
        self.location_label.set_xalign(0)
        self.location_label.set_wrap(True)
        self.location_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.location_label.add_css_class('dim-label')
        self.location_label.set_selectable(False)
        self.box.append(self.text_label)
        self.box.append(self.location_label)
        self.set_child(self.box)

    def update_content(self, data):
        '''复用已有 row 仅更新内容，避免每次搜索都销毁/重建 4 个 widget
        （ListBoxRow + Box + 2 Label）。搜索结果上限 8 条，原实现每次
        按键（去抖后）仍要 8×4 = 32 次 widget 销毁 + 32 次创建。'''
        self.uri_ending = data[0]
        self.text_label.set_markup(data[1])
        self.location_label.set_markup(data[2])

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
from gi.repository import Gtk, Adw

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
        self.set_size_request(300, -1)
        self.add_css_class('help')

        # ---- 顶部内嵌工具栏（与左侧栏 .sidebar-toolbar 统一外观）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        self.home_button = Gtk.Button(icon_name='go-home-symbolic')
        self.home_button.set_tooltip_text(_('Help home'))
        self.home_button.add_css_class('flat')
        self.home_button.set_can_focus(False)
        self.toolbar.append(self.home_button)

        self.up_button = Gtk.Button(icon_name='go-up-symbolic')
        self.up_button.set_tooltip_text(_('Scroll to top'))
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
        self.search_button.set_tooltip_text(_('Search help documentation'))
        self.search_button.add_css_class('flat')
        self.search_button.set_can_focus(False)
        self.toolbar.append(self.search_button)

        # 切换到 PDF Preview 按钮：始终放在工具栏最右端（最后 append）。
        self.switch_button = Gtk.Button()
        self.switch_button.set_child(Gtk.Image(icon_name='view-paged-symbolic'))
        self.switch_button.set_can_focus(False)
        self.switch_button.set_tooltip_text(_('Switch to PDF Preview'))
        self.switch_button.add_css_class('flat')

        self.append(self.toolbar)

        # Search page: a single Adw.Clamp wraps the vertical search content,
        # giving native bounded/centered width. The entry sits at the top,
        # results scroll below, and a compact StatusPage shows the no-results
        # empty state. This replaces the former double-CenterBox floating blob.
        self.search_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.search_content_box.set_margin_top(12)
        self.search_content_box.set_margin_bottom(12)
        # 左右内边距：让搜索框 / 计数 / 结果卡片都不贴侧栏边缘，留出呼吸空间，
        # 整体更紧凑、与设置列表页一致（设置页内容也有左右留白）。
        self.search_content_box.set_margin_start(12)
        self.search_content_box.set_margin_end(12)

        self.search_entry = SearchEntry()
        self.search_entry.set_placeholder_text(_('Search help'))
        # 搜索框左右边距与结果卡片（search_results）一致（各 12px），使两者
        # 等宽对齐，整体与最初截图一致、且不贴侧栏边缘。
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
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

        # 搜索结果用 Adw.PreferencesGroup 作为容器，与"设置"列表外观一致：
        # PreferencesGroup 内部自带带 boxed-list 类的 ListBox，提供圆角卡片 +
        # 分隔线，直接 add(Adw.ActionRow) 即为标准设置列表样式。行激活改用
        # Adw.ActionRow 的 'activated' 信号（由 controller 逐行连接），不再依赖
        # ListBox 的 row-activated。整体放入 ScrolledWindow 以支持结果过多滚动。
        self.search_results = Adw.PreferencesGroup()
        self.search_results.set_margin_top(12)
        # 左右留白放在卡片自身（而非外层容器）：PreferencesGroup 卡片若直接
        # 填满 ScrolledWindow，圆角会落在 viewport 的直角边缘被 clip 裁平
        # （表现为左右被切）。这里给卡片左右 margin，使圆角落在 viewport
        # 内侧、不被裁，与设置列表在 preferencespage 里的留白行为一致。
        self.search_results.set_margin_start(12)
        self.search_results.set_margin_end(12)
        self.search_scroll = Gtk.ScrolledWindow()
        self.search_scroll.set_vexpand(True)
        self.search_scroll.kinetic_scrolling = True
        self.search_scroll.overlay_scrolling = True
        self.search_scroll.set_child(self.search_results)
        self.search_content_box.append(self.search_scroll)

        self.no_results_slate = Adw.StatusPage()
        self.no_results_slate.add_css_class('compact')
        self.no_results_slate.set_icon_name('action-unavailable-symbolic')
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

        # 不再用 Adw.Clamp 包裹：固定宽侧栏（396px）里 Clamp 的动态限宽会把
        # 内容区域算窄于卡片，导致卡片左右被 Clamp 的可视区裁切。改为由
        # search_content_box 的左右 margin 直接控制留白，卡片宽度=侧栏宽-24，
        # 与设置列表在固定侧栏里的表现一致。search_content_box 在下方 stack
        # 创建后通过 add_named(..., 'search') 加入。

        # WebKit 可用时创建 WebView 渲染帮助页面；不可用时降级为 Gtk.Label
        # 占位（显示提示文案）。搜索功能不依赖 WebKit，仍正常工作。
        if HAS_WEBKIT:
            self.content = WebKit.WebView()
            self.content.set_hexpand(True)
            self.content.set_vexpand(True)
            # preview-card 圆角 + 裁切加在 WebView 自身（而非共享 stack），
            # 使 WebView 内容被圆角裁出；搜索页的 boxed-list 不再被 stack 裁切。
            self.content.add_css_class('preview-card')
            self.content.set_overflow(Gtk.Overflow.HIDDEN)
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
            self.content.add_css_class('preview-card')
            self.content.set_overflow(Gtk.Overflow.HIDDEN)
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

        # 不用 Gtk.Stack 叠放 content/search 两页：Stack 会把两页重叠，
        # content 页（WebView + preview-card/overflow:hidden）绘制时覆盖并
        # 裁切 search 页边缘（表现为列表两侧被切）。改为互斥可见的两个容器
        # 直接挂在面板 VBox 上，search 页（红卡片）不再被 content 页遮挡。
        self.content.set_vexpand(True)
        self.search_content_box.set_vexpand(True)
        self.append(self.content)
        self.append(self.search_content_box)
        self.search_content_box.set_visible(False)

        # switch_button 始终最后 append 到 toolbar，确保在工具栏最右端
        # （在 search_button 和 js_info_button 之后）。
        self.toolbar.append(self.switch_button)

        self.search_result_items = list()


class SearchResultView(Adw.ActionRow):
    '''搜索结果行：用原生 Adw.ActionRow 替代手写 Gtk.ListBoxRow + 2 Label。

    title = 标题（如 "first-latex-doc document"），subtitle = 位置（如
    "About this document"）。    两者均开启 use-markup，复用 setzer.widgets.search_highlight 的
    highlight_words() 生成的 <b> 标记实现搜索词粗体高亮，无需自定义 Label。
    title-lines/subtitle-lines 设为 0 不限制行数，并开启自动换行，
    避免长标题被截断。

    Adw.ActionRow 本身是 Gtk.ListBoxRow 子类，点击通过 'activated' 信号
    （由 presenter 逐行连接）触发跳转，与原 ListBox 的 row-activated 行为一致。
    '''

    def __init__(self, data):
        Adw.ActionRow.__init__(self)
        self.set_can_focus(False)
        # Adw.ActionRow 默认 activatable=False（继承 PreferencesRow），点击不会
        # 发射 'activated' 信号，导致结果项无法跳转。显式开启可点击激活。
        self.set_activatable(True)
        self.set_use_markup(True)
        # title-lines/subtitle-lines = 0 表示不限制行数，Adwaita 会长文本自动换行；
        # 不放回 set_wrap（ActionRow 无此属性）。
        self.set_title_lines(0)
        self.set_subtitle_lines(0)
        self.uri_ending = data[0]
        self.set_title(data[1])
        self.set_subtitle(data[2])

    def update_content(self, data):
        '''复用已有 row 仅更新内容，避免每次搜索都销毁/重建 widget。
        搜索结果上限 8 条，原实现每次按键（去抖后）仍要多次 widget 销毁
        + 创建；改为 set_title/set_subtitle 仅更新文本即可。'''
        self.uri_ending = data[0]
        self.set_title(data[1])
        self.set_subtitle(data[2])

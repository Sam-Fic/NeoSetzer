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
from gi.repository import Adw, Gtk, GLib, GObject, Gio, Gdk

import os

from setzer.app.service_locator import ServiceLocator

import setzer.workspace.headerbar.headerbar_viewgtk as headerbar_view
import setzer.workspace.shortcutsbar.shortcutsbar_viewgtk as shortcutsbar_view
import setzer.workspace.preview_panel.preview_panel_viewgtk as preview_panel_view
import setzer.workspace.help_panel.help_panel_viewgtk as help_panel_view
import setzer.workspace.sidebar.sidebar_viewgtk as sidebar_view
import setzer.workspace.welcome_screen.welcome_screen_viewgtk as welcome_screen_view


class MainWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        Adw.ApplicationWindow.__init__(self, application=app)

        self.app = app
        # 设置最小宽度：使用 breakpoint（窄窗口折叠侧边栏）时 Adw 要求窗口有
        # width-request，否则会告警。360 为 libadwaita 惯用的窄窗口下限。
        self.set_size_request(360, 550)

        self.popoverlay = Gtk.Overlay()
        # ToastOverlay 包裹整个窗口内容，供全局 toast 通知使用
        # （保存失败、工作区状态丢失等非阻塞提示）。
        # hexpand/vexpand 确保 toast overlay 填满窗口，避免其 natural size
        # 因内部 toast 间距/阴影而略大于窗口宽度，触发 Adwaita 告警。
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_hexpand(True)
        self.toast_overlay.set_vexpand(True)
        self.toast_overlay.set_child(self.popoverlay)
        self.set_content(self.toast_overlay)

        # 全屏状态追踪
        self._is_fullscreen = False
        self._headerbar_visible_in_fullscreen = False
        self._sidebar_was_visible = False
        # popover 是否打开：防止鼠标进入 popover（独立 surface）时主窗口
        # leave 导致顶栏收起。该标志由 popover 的 map / closed 信号维护。
        self._popover_open = False
        # 顶栏隐藏延时计时器：隐藏不再「立即」执行，而是经计时器再判定，
        # 以消解「主窗口 leave」与「popover 打开」之间的竞态。
        self._headerbar_hide_timeout_id = None
        # 鼠标是否在主窗口内、最近一次纵向位置：供隐藏判定使用。
        self._pointer_inside = True
        self._last_pointer_y = 0

    def create_widgets(self):
        self.shortcutsbar = shortcutsbar_view.ShortcutsBar()

        self.document_stack = Gtk.Stack()
        # 短 CROSSFADE（100ms）：原用 NONE（无过渡），文档切换显得突兀。
        # 标准 200ms 在「新建 latex」时会让编辑器延迟出现，100ms 足够提供
        # 视觉反馈又不影响响应感。
        self.document_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.document_stack.set_transition_duration(100)
        # 不设 set_size_request(550)——shortcutsbar 的 overflow reflow 会让
        # 按钮在窄宽时自动收起，不再需要硬性最小宽度。这样窗口可以拖到更小。
        self.document_stack.set_vexpand(True)

        self.document_stack_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.document_stack_wrapper.append(self.shortcutsbar)
        self.document_stack_wrapper.append(self.document_stack)

        # Pass-12: headerbar 只覆盖编辑器区域（document_stack_wrapper），
        # 不再覆盖 preview_paned_overlay（那会把预览/帮助侧栏也压在标题栏下）。
        # 把 document_stack_wrapper 包进 document_stack_overlay，headerbar 作为
        # 其 overlay。这样预览/帮助侧栏出现时，document_stack_overlay（连同
        # headerbar）随编辑器列一起被推到左侧——与左侧 sidebar_split 的行为一致：
        # 侧栏占自己的空间，标题栏按钮被推开而非被覆盖。
        # 当侧栏关闭时，document_stack_overlay 占满 preview_split 的内容列，
        # 标题栏按钮回到右侧靠边位置。
        self.document_stack_overlay = Gtk.Overlay()
        self.document_stack_overlay.set_child(self.document_stack_wrapper)

        # Pass-10: build_log 从底部嵌入式 Gtk.Paned 改为 Adw.Dialog 弹窗
        # （BuildLogDialog），不再常驻 widget tree。原 build_log_paned（纵向
        # Gtk.Paned，编辑器在上、build_log 在下）整体移除，preview_paned 直接
        # 以 document_stack_overlay 为 start_child。build_log 实例由 workspace
        # 持有（workspace.py:80），按需 present/close。
        self.preview_panel = preview_panel_view.PreviewPanelView()

        self.help_panel = help_panel_view.HelpPanelView()

        self.sidebar = sidebar_view.Sidebar()

        self.preview_paned_overlay = Gtk.Overlay()
        self.preview_help_stack = Gtk.Stack()
        # Pass-12: 预览/帮助侧栏不再覆盖标题栏区域——headerbar 已移到
        # document_stack_overlay 上，侧栏顶部有自己的 .sidebar-toolbar 工具栏。
        # 侧栏整体（含工具栏）与左侧栏行为一致：占自己的空间，不被标题栏覆盖。
        self.preview_help_stack.add_named(self.preview_panel, 'preview')
        self.preview_help_stack.add_named(self.help_panel, 'help')
        # PDF 预览弹出为独立窗口后，preview_panel 从 stack 移除，侧栏只保留
        # help 页面（无 status page、无 switch button）。pop_out 时 stack 切到
        # 'help'；pop_in 时 re-add preview_panel 回 'preview'。
        # 预览↔帮助互斥切换时给页面本身加 CROSSFADE 过渡（200ms 与 libadwaita
        # 默认动画时长一致）。整体侧栏的滑入/滑出已由 Adw.OverlaySplitView
        # 的 set_show_sidebar() 提供，这里只补页面间切换的过渡，避免硬切。
        # 不用 Gtk.Revealer 包裹：那样需重构 widget 树且会与 OverlaySplitView
        # 的滑入动画叠加产生视觉冲突；Stack 内建 CROSSFADE 更轻量、更合适。
        self.preview_help_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.preview_help_stack.set_transition_duration(200)

        # preview_split: 横向 Adw.OverlaySplitView（预览/帮助在右 = sidebar）。
        # 与 sidebar_split 同款控件，set_show_sidebar() 自带滑入/滑出动画，
        # 因此 toggle preview / toggle help 能得到与 toggle document structure 一致的
        # 滑入动画。分隔条仍可拖拽调整宽度（notify::sidebar-width-fraction）。
        # collapsed 保持 False：永不折叠为浮层抽屉，始终 inline 推挤编辑器列。
        # 内容列（编辑器 + headerbar overlay）= sidebar 之外的剩余空间，
        # 窗口变窄时自动收缩，shortcutsbar 的 reflow 逻辑不受影响
        # （reflow 监听自身分配宽度，与外层容器无关）。
        self.preview_split = Adw.OverlaySplitView()
        self.preview_split.set_content(self.document_stack_overlay)
        self.preview_split.set_sidebar(self.preview_help_stack)
        self.preview_split.set_sidebar_position(Gtk.PackType.END)  # 预览在右侧
        self.preview_split.set_min_sidebar_width(300)   # preview 自然宽 300；help 396 由子部件 min request 自动抬高
        self.preview_split.set_max_sidebar_width(900)
        self.preview_split.set_sidebar_width_fraction(0.5)
        self.preview_paned_overlay.set_child(self.preview_split)

        # sidebar_split: Adw.OverlaySplitView —— 原生可折叠侧边栏。
        # 宽窗口：侧边栏内联（与内容并排，等价原 Gtk.Paned 行为）；
        # 窄窗口（<700px breakpoint）：侧边栏折叠为浮层抽屉。
        # sidebar 为 Sidebar(Gtk.Stack)，含 symbols / document_structure 两页，共享同一抽屉。
        self.sidebar_split = Adw.OverlaySplitView()
        self.sidebar_split.set_sidebar(self.sidebar)
        self.sidebar_split.set_content(self.preview_paned_overlay)
        self.sidebar_split.set_min_sidebar_width(120)
        self.sidebar_split.set_max_sidebar_width(600)
        # 初始值仅为占位：真正的宽度由 setup_paneds() 按持久化的
        # window_state/sidebar_width_fraction（默认 0.20）覆盖设定。
        self.sidebar_split.set_sidebar_width_fraction(0.20)

        self.welcome_screen = welcome_screen_view.WelcomeScreenView()

        # welcome_overlay：把欢迎页包进 Gtk.Overlay，以便 headerbar 在欢迎页
        # 模式下作为浮层叠在欢迎页顶部（无文档时 mode_stack 显示 welcome_screen）。
        # 注意：欢迎模式下 open/create 按钮已移到欢迎页正文（activate_welcome_screen_mode
        # 会隐藏 headerbar 的 open/new/save 按钮），headerbar 仅保留居中标题与菜单按钮；
        # 迁到 welcome_overlay 是为了让这个菜单/标题在欢迎页依然可见可用。headerbar 是
        # 单一控件，通过 reparent_headerbar() 在 welcome_overlay 与 document_stack_overlay
        # 之间迁移，详见该方法注释。
        self.welcome_overlay = Gtk.Overlay()
        self.welcome_overlay.set_child(self.welcome_screen)

        self.mode_stack = Gtk.Stack()
        self.mode_stack.add_named(self.welcome_overlay, 'welcome_screen')
        self.mode_stack.add_named(self.sidebar_split, 'documents')
        # welcome↔documents 模式切换时加 CROSSFADE 过渡（200ms 与 libadwaita
        # 默认动画时长一致）。这是一次性切换，原用 NONE（无过渡）显得硬切，
        # 与 document_stack / preview_help_stack / headerbar center_widget 的
        # 过渡风格统一。200ms 与 headerbar 的 welcome↔document 切换等长，
        # 避免两者动画节奏不一致。
        self.mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.mode_stack.set_transition_duration(200)

        self.headerbar = headerbar_view.HeaderBar()

        # 浮层 headerbar 会覆盖下方内容区顶部，给内容区整体留出 headerbar
        # 高度的上边距，避免编辑器内容被标题栏遮住。
        # 初始使用 46px 作为兜底，随后由 do_size_allocate 根据 headerbar
        # 实际分配高度动态调整，避免写死高度。welcome_screen 同样留上边距，
        # 否则欢迎页顶部的图标/标题会被浮层 headerbar 遮住。
        # Pass-12: preview_panel / help_panel 不再设 margin_top——它们不再
        # 被标题栏覆盖（侧栏有自己的 .sidebar-toolbar 工具栏）。
        self.document_stack_wrapper.set_margin_top(46)
        self.welcome_screen.set_margin_top(46)

        # Pass-12: headerbar 作为 document_stack_overlay 的 overlay（只覆盖编辑器列）。
        # 不再放在 preview_paned_overlay 上——那样会连预览/帮助侧栏一起覆盖，
        # 与左侧栏行为不一致。现在侧栏出现时 headerbar 随编辑器列被推开，
        # 侧栏关闭时 headerbar 回到右侧靠边位置，与 sidebar_split 一致。
        # 欢迎页模式下 headerbar 由 reparent_headerbar 迁移到 welcome_overlay，
        # 保证无文档时 open/create 按钮依然可见。
        self.headerbar.widget.set_valign(Gtk.Align.START)
        self.document_stack_overlay.add_overlay(self.headerbar.widget)

        # 全屏模式下 headerbar 是浮层，默认隐藏；鼠标移到顶部边缘时弹出。
        # 对有下拉气泡（popover）的按钮（open / new 箭头、汉堡菜单），点击后
        # 气泡作为独立 surface 弹出，鼠标从主窗口移入气泡会触发主窗口的 leave；
        # 若此时直接隐藏顶栏，用户操作菜单时顶栏会闪退。处理要点：
        #   1. 监听每个 popover 的 map / closed，可靠维护 _popover_open；
        #      popover 可能首次激活时才创建，故按钮回调里也补连（用 _hb_tracked
        #      标记防重复连接）。
        #   2. 顶栏隐藏不再「立即」执行，而是经延时计时器再判定：计时器触发时
        #      才核对 _popover_open 与鼠标位置，从而消解「leave 先于 activate /
        #      map 设置标志」的竞态——即便 leave 先到，气泡一旦 map，_popover_open
        #      即被置 True，延时回调会跳过隐藏。
        self.headerbar.sidebar_toggle.connect('clicked', self._on_headerbar_clicked)
        self.headerbar.open_document_button.connect('clicked', self._on_headerbar_clicked)
        self.headerbar.open_document_button.connect('activate', self._on_split_button_activate)
        self.headerbar.new_document_button.connect('clicked', self._on_headerbar_clicked)
        self.headerbar.new_document_button.connect('activate', self._on_split_button_activate)
        self.headerbar.center_button.connect('clicked', self._on_headerbar_clicked)
        self.headerbar.preview_help_toggle.connect('clicked', self._on_headerbar_clicked)
        # Gtk.MenuButton（汉堡菜单）没有 clicked 信号，用 activate 替代。
        self.headerbar.menu_button.connect('activate', self._on_menu_button_activate)

        # 预创建阶段 popover 已存在则直接连接；不存在则等首次激活时再连。
        self._track_popover(self.headerbar.open_document_button.get_popover())
        self._track_popover(self.headerbar.new_document_button.get_popover())
        self._track_popover(self.headerbar.menu_button.get_popover())

        # 记录 headerbar 当前所在 overlay，供 reparent_headerbar 判断迁移方向。
        # 不用 get_parent()：Gtk.Overlay 的 overlay 子部件实际父级是内部 Bin，
        # 而非 Gtk.Overlay 本身，get_parent() 比较会失真。
        self._headerbar_in_welcome = False

        self.content_overlay = Gtk.Overlay()
        self.content_overlay.set_child(self.mode_stack)
        self.popoverlay.set_child(self.content_overlay)

        # 加载指示器：覆盖整个内容区，在打开文档/启动恢复时显示
        self._loading_spinner = Gtk.Spinner()
        self._loading_spinner.set_size_request(48, 48)
        self._loading_spinner.set_halign(Gtk.Align.CENTER)
        self._loading_spinner.set_valign(Gtk.Align.CENTER)
        self._loading_spinner.set_visible(False)
        self.content_overlay.add_overlay(self._loading_spinner)

        # 文件拖放视觉反馈浮层：圆角描边 + 居中计数标签。
        # can_target=False 让拖放事件穿透到挂在同一 overlay 上的 DropTarget（不被此浮层拦截），
        # 否则它会抢走 drag/drop 事件导致 on_drop 收不到。enter 时显示、
        # leave 时隐藏，motion 时根据可接受文件数更新标签文字。
        # 文件类型不可接受时叠加 .drop-reject（描边转错误色），配合 GTK「禁止」光标。
        # 该浮层随 headerbar 在 welcome_overlay 与 document_stack_overlay 间迁移：
        # welcome 模式下覆盖整窗；documents 模式下只覆盖编辑器列，并通过 .editor-mode 类
        # 复用 .editor-card 的 8px 圆角与 6px 内边距，正好盖在编辑器卡片上。
        self.drop_highlight = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.drop_highlight.set_can_target(False)
        self.drop_highlight.add_css_class('drop-highlight')
        self.drop_highlight.set_visible(False)
        # 让浮层铺满所在 overlay，描述边框覆盖整片区域；
        # 标签则向四个方向撑开并居中，使提示语落在正中。
        self.drop_highlight.set_halign(Gtk.Align.FILL)
        self.drop_highlight.set_valign(Gtk.Align.FILL)
        self.drop_label = Gtk.Label()
        self.drop_label.add_css_class('drop-label')
        self.drop_label.set_hexpand(True)
        self.drop_label.set_vexpand(True)
        self.drop_label.set_valign(Gtk.Align.CENTER)
        self.drop_label.set_halign(Gtk.Align.CENTER)
        self.drop_label.set_text(_('Drop to open file'))
        self.drop_highlight.append(self.drop_label)
        # 初始挂到 document_stack_overlay，与 headerbar 初始位置一致（_headerbar_in_welcome=False）；
        # 欢迎模式下 reparent_headerbar(True) 会把 headerbar 与浮层一并迁到 welcome_overlay。
        self.document_stack_overlay.add_overlay(self.drop_highlight)
        self._drop_highlight_in_welcome = False

        # 窄窗口（<700px）自动把侧边栏与预览侧栏折叠为浮层抽屉
        # （Adw.OverlaySplitView 的 collapsed 属性）。两者同款控件、同一 breakpoint，
        # 行为一致：宽窗 inline 推挤，窄窗 overlay drawer（自带 backdrop + 点击外部关闭）。
        # preview_split 之前显式保持 collapsed=False，导致窄窗时预览（min 300px）inline
        # 挤死编辑器（360px 窗口下编辑器仅剩 ~60px）。折叠为 drawer 后编辑器不再被挤，
        # 预览按需打开（preview_toggle / F9），与 sidebar 行为对称。
        # collapsed 属性 presenter 从不触碰（只调 set_show_sidebar），add_setter 无冲突。
        sidebar_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 700, Adw.LengthUnit.PX))
        sidebar_breakpoint.add_setter(self.sidebar_split, 'collapsed', True)
        sidebar_breakpoint.add_setter(self.preview_split, 'collapsed', True)
        # 暴露 narrow breakpoint 引用供 headerbar presenter 比较
        # （notify::current-breakpoint 触发 compact 模式切换）。
        self.narrow_breakpoint = sidebar_breakpoint
        self.add_breakpoint(sidebar_breakpoint)
        # 注意：shortcutsbar overflow 现在由 Shortcutsbar.do_size_allocate
        # 连续测量后动态计算（每像素自适应），不再用 Adw.Breakpoint 阶梯。

        # 欢迎页快速操作按钮窄窗纵向堆叠：3 个按钮（New LaTeX / New BibTeX /
        # Use a Template）默认水平均分，<500px 时 "New LaTeX Document" 会
        # ellipsize。切到 VERTICAL 后按钮各自占满 clamp 宽度，标签完整可读。
        # 500px 阈值与 Adw.Clamp 的 tightening_threshold(400) + maximum_size(520)
        # 区间协调：clamp 在 400-520 之间已经开始收紧，500 是按钮标签开始挤的
        # 实测临界点。breakpoint 只在 welcome_screen 模式下生效（documents 模式
        # 下 actions_box 不可见，setter 无副作用）。
        welcome_buttons_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.new_length(Adw.BreakpointConditionLengthType.MAX_WIDTH, 500, Adw.LengthUnit.PX))
        welcome_buttons_breakpoint.add_setter(
            self.welcome_screen.actions_box, 'orientation', Gtk.Orientation.VERTICAL)
        self.add_breakpoint(welcome_buttons_breakpoint)

        self.css_provider_font_size = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.css_provider_font_size, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # 加载项目自定义 CSS（仅 shortcutsbar 的 FlowBoxChild padding 归零等少量微调，
        # 见 data/resources/style_gtk.css）。与上面的 css_provider_font_size 同用
        # STYLE_PROVIDER_PRIORITY_USER 优先级（800），高于 libadwaita 默认样式
        # （APPLICATION 级 600），确保微调规则不被默认 flowbox 样式覆盖。
        css_file = os.path.join(ServiceLocator.get_resources_path(), 'style_gtk.css')
        if os.path.exists(css_file):
            self.css_provider_app = Gtk.CssProvider()
            self.css_provider_app.load_from_path(css_file)
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self.css_provider_app,
                Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # shortcutsbar overflow reflow 安全网：Shortcutsbar.do_size_allocate
        # 是主触发路径（同步 reflow，零延迟）。这里每 250ms 轮询一次作为兜底，
        # 覆盖 do_size_allocate 可能漏掉的边缘情况。主路径可靠时此轮询不做实际工作。
        # 直接用 _last_allocated_width 判断，无需独立的 _last_sb_width 变量。
        def _poll_sb_width():
            width = self.shortcutsbar.get_allocated_width()
            if width > 1 and width != self.shortcutsbar._last_allocated_width:
                self.shortcutsbar.reflow_for_width(width)
                self.shortcutsbar._last_allocated_width = width
            return True
        GLib.timeout_add(250, _poll_sb_width)

        # 文件拖放：欢迎页（welcome_overlay）与编辑器列（document_stack_overlay）各挂一个
        # 同配置的 DropTarget，共用同一套 on_drag_* 处理函数。两 overlay 分别只在对应模式下
        # 可见/激活，所以拖放自然只在该模式生效。优先用 Gdk.FileList 接收整批文件
        # （多文件拖入时一次拿到全部路径用于计数），无 Gdk.FileList 的环境退回 Gio.File。
        # preload=True：拖拽过程中即可通过 get_value() 读取文件列表，实时显示「将打开 N 个文件」。
        # 扩展名过滤与 do_open（setzer_dev.py）一致：仅打开 .tex/.bib/.cls/.sty；其它文件不打开
        # 但拖放仍被消费（返回 True），以红色描边提示。CAPTURE 阶段：先于 overlay 内子控件接管，
        # 避免任何子控件抢走文件拖放（编辑器 Gtk.TextView 自带文本拖放不受影响——本 DropTarget
        # 的 gtypes 仅含文件类型，纯文本拖放不匹配，不会触发这些 handler）。
        self._drop_exts = ('.tex', '.bib', '.cls', '.sty')

        def _make_drop_target():
            drop_target = Gtk.DropTarget()
            drop_target.set_actions(Gdk.DragAction.COPY)
            gtypes = [Gio.File]
            if hasattr(Gdk, 'FileList'):
                gtypes = [Gdk.FileList, Gio.File]
            drop_target.set_gtypes(gtypes)
            drop_target.set_preload(True)
            drop_target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            drop_target.connect('enter', self.on_drag_enter)
            drop_target.connect('leave', self.on_drag_leave)
            drop_target.connect('motion', self.on_drag_motion)
            drop_target.connect('accept', self.on_drag_accept)
            drop_target.connect('drop', self.on_drop)
            return drop_target
        self.welcome_overlay.add_controller(_make_drop_target())
        self.document_stack_overlay.add_controller(_make_drop_target())

        # 关闭 GTK/Adwaita/Yaru 自带的 drop(active) 默认高亮：该状态会向祖先控件传播，
        # 每个带状态的祖先都会画一圈直角矩形提示（与圆角浮层叠在一起）。拖放期间给拖放
        # 目标及其所有祖先临时加 .dnd-no-indicator 类（普通 CSS 类，USER 优先级高于主题，
        # 可覆盖 Yaru 的 box-shadow/border/outline 默认描边）；leave/drop 后移除。
        # 注意：此处不能用 :drop(active) 伪类去覆盖——在 CAPTURE 阶段 + 主题组合下不稳定。
        # 拖放目标链取 welcome_overlay 与 document_stack_overlay 祖先的并集：
        # 两 overlay 分别只在各自模式下成为实际拖放目标，但其祖先（含 content_overlay、
        # mode_stack、popoverlay 等）在任一模式下都可能需要清掉 Yaru 默认 drop 高亮，
        # 故并集统一挂/摘 .dnd-no-indicator。
        self._dnd_chain = []
        for _start in (self.welcome_overlay, self.document_stack_overlay):
            widget = _start
            while widget is not None:
                if widget not in self._dnd_chain:
                    self._dnd_chain.append(widget)
                widget = widget.get_parent()

        # 全屏鼠标追踪：检测鼠标是否在窗口顶部边缘，以显示/隐藏 headerbar
        self._motion_controller = Gtk.EventControllerMotion()
        self._motion_controller.connect('motion', self._on_motion)
        self._motion_controller.connect('leave', self._on_leave)
        self.add_controller(self._motion_controller)

        # 全屏状态变化监听
        self.connect('notify::fullscreened', self._on_fullscreened_changed)

        # 监听 shortcut 栏可见性变化，与全屏状态联动更新编辑器卡片边距
        self.shortcutsbar.connect('notify::visible', self._on_shortcutsbar_visibility_changed)

    # ---- 文件拖放（DnD）处理 ----

    def _set_dnd_indicator(self, active):
        # 拖放期间临时给目标及其所有祖先挂/摘 .dnd-no-indicator，
        # 用普通类覆盖 Yaru 的默认 drop 高亮（圆角浮层由我们自己的 drop_highlight 负责）。
        for w in self._dnd_chain:
            if active:
                w.add_css_class('dnd-no-indicator')
            else:
                w.remove_css_class('dnd-no-indicator')

    def _set_dnd_blank(self, active):
        # 拖放期间把背景淡出为空白以增强圆角浮层下文字的可读性：
        # welcome 模式淡出欢迎页正文；documents 模式只淡出编辑器卡片区域（document_stack，
        # 即 .editor-card 的容器），保留快捷键栏可见——即使有快捷键栏也不隐藏它。
        # leave/drop 后移除类即复原。
        if self._headerbar_in_welcome:
            target = self.welcome_screen
        else:
            target = self.document_stack
        if active:
            target.add_css_class('dnd-blank')
        else:
            target.remove_css_class('dnd-blank')

    def _extract_drop_files(self, value):
        '''从拖放值中归一化出文件路径列表（兼容 Gdk.FileList 与单 Gio.File）。'''
        if value is None:
            return []
        if hasattr(Gdk, 'FileList') and isinstance(value, Gdk.FileList):
            return value.get_files()
        if isinstance(value, Gio.File):
            return [value]
        return []

    def _is_acceptable_file(self, file):
        path = file.get_path()
        if path is None:
            return False
        return path.endswith(self._drop_exts)

    def _layout_drop_highlight(self):
        # 按当前模式布局拖放浮层框：
        # - welcome 模式：浮层在 welcome_overlay（整窗），四向 12px 内边距，顶部用标题栏高度
        #   （headerbar 浮在 welcome_overlay 顶部）使框线停在标题栏下沿、不覆盖它；全屏且标题栏
        #   隐藏时回落 12px。圆角沿用 .drop-highlight 的 12px。
        # - documents 模式：浮层在 document_stack_overlay（仅编辑器列），通过 .editor-mode 类
        #   复用 .editor-card 的 8px 圆角（与卡片同形），左右 6px 内边距也与 .editor-card 对齐；
        #   顶部跳过浮层标题栏与快捷键栏，正好落在编辑器卡片上沿，使圆角框覆盖在卡片之上。
        # 每次 motion 都重算，覆盖拖放期间标题栏显隐的动态变化。
        if self._headerbar_in_welcome:
            top = 12
            fullscreen_hidden = self._is_fullscreen and not self._headerbar_visible_in_fullscreen
            if not fullscreen_hidden:
                top = self.headerbar.widget.get_allocated_height()
            self.drop_highlight.set_margin_top(top)
            self.drop_highlight.set_margin_bottom(12)
            self.drop_highlight.set_margin_start(12)
            self.drop_highlight.set_margin_end(12)
            self.drop_highlight.remove_css_class('editor-mode')
        else:
            # documents 模式：高亮浮层覆盖编辑器卡片（.editor-card）。
            # 卡片几何会随快捷键栏有无、全屏/标题栏显隐等动态变化，故直接取当前文档
            # 编辑器卡片在编辑器列 overlay 中的实际矩形来对齐（8px 圆角由 .editor-mode
            # 复用卡片形状；左右内边距随卡片自身的 6px margin 一同对齐）。底部额外留 6px
            # 呼吸空隙。compute_bounds 不可用时回退到按标题栏/快捷键栏高度估算。
            self.drop_highlight.add_css_class('editor-mode')
            overlay = self.document_stack_overlay
            geom = self._get_card_geom_in_overlay(overlay)
            if geom is not None:
                x, y, w, h = geom
                overlay_w = overlay.get_allocated_width()
                # 钳制为非负，避免拖放瞬间的布局抖动产生负尺寸分配告警。
                # 底部固定距编辑器列（窗口）底部 6px，不随卡片几何变化。
                self.drop_highlight.set_margin_top(max(0, int(y)))
                self.drop_highlight.set_margin_bottom(6)
                self.drop_highlight.set_margin_start(max(0, int(x)))
                self.drop_highlight.set_margin_end(max(0, int(overlay_w - x - w)))
            else:
                in_fullscreen_hidden = self._is_fullscreen and not self._headerbar_visible_in_fullscreen
                headerbar_height = 0 if in_fullscreen_hidden else self.headerbar.widget.get_allocated_height()
                shortcuts_height = self.shortcutsbar.get_allocated_height() if self.shortcutsbar.get_visible() else 0
                self.drop_highlight.set_margin_top(headerbar_height + shortcuts_height)
                self.drop_highlight.set_margin_bottom(6)
                self.drop_highlight.set_margin_start(6)
                self.drop_highlight.set_margin_end(6)

    def _get_card_geom_in_overlay(self, overlay):
        '''返回当前文档编辑器卡片（.editor-card）在 overlay 坐标系中的 (x, y, w, h)。
        兼容 Gdk.Rectangle 与 graphene.Rect（compute_bounds 的返回类型随 PyGObject/GTK
        版本而异），以及 (success, rect) 元组形式；取不到时返回 None。'''
        try:
            doc = ServiceLocator.get_workspace().get_active_document()
            card = doc.view.vbox if doc is not None else None
            if card is None:
                return None
            result = card.compute_bounds(overlay)
            # compute_bounds 可能返回 (success, rect) 元组（PyGObject 绑定形式）。
            if isinstance(result, tuple):
                if len(result) == 2 and isinstance(result[0], bool):
                    if not result[0]:
                        return None
                    rect = result[1]
                else:
                    rect = result[0]
            else:
                rect = result
            if rect is None:
                return None
            x = getattr(rect, 'x', None)
            if x is None:
                try:
                    x = rect.get_x()
                except Exception:
                    x = 0
            y = getattr(rect, 'y', None)
            if y is None:
                try:
                    y = rect.get_y()
                except Exception:
                    y = 0
            w = getattr(rect, 'width', None)
            if w is None:
                try:
                    w = rect.get_width()
                except Exception:
                    w = 0
            h = getattr(rect, 'height', None)
            if h is None:
                try:
                    h = rect.get_height()
                except Exception:
                    h = 0
            return (x, y, w, h)
        except Exception:
            return None

    def _update_drop_feedback(self, target):
        '''根据当前拖放值刷新浮层标签与描边（文件计数 / 可接受性提示）。
        target 为触发本次回调的 DropTarget（welcome 或 document_stack_overlay 上的那份），
        用它的 get_value() 读取本拖拽已 preload 的文件列表。描边状态必须在这里同步，
        因为 enter/motion 时 preload 已读到文件列表，而 accept 可能在列表就绪前就跑过
        一次（那时 get_value() 为空）——若只在 accept 里设描边，红框会卡住。'''
        self._layout_drop_highlight()
        if not hasattr(self, 'drop_highlight') or not self.drop_highlight.get_visible():
            return
        files = self._extract_drop_files(target.get_value())
        if not files:
            # preload 尚未拿到文件列表，保持中性提示，避免误闪「无法打开」
            self.drop_label.set_text(_('Drop to open file'))
            self.drop_highlight.remove_css_class('drop-reject')
            return
        count = sum(1 for f in files if self._is_acceptable_file(f))
        if count > 1:
            self.drop_label.set_text(_('Will open {count} files').format(count=count))
        elif count == 1:
            self.drop_label.set_text(_('Drop to open file'))
        else:
            self.drop_label.set_text(_('Cannot open this file type'))
        # 同步描边：有可接受文件→强调色；全不可接受→红色禁止样式
        if count > 0:
            self.drop_highlight.remove_css_class('drop-reject')
        else:
            self.drop_highlight.add_css_class('drop-reject')

    def on_drag_enter(self, target, x, y):
        self._set_dnd_indicator(True)
        self._set_dnd_blank(True)
        self.drop_highlight.set_visible(True)
        self._update_drop_feedback(target)
        # GTK4 中 enter/motion 信号返回的是 GdkDragAction；返回 False 会被当作
        # GDK_ACTION_NONE（不接受该拖放），导致目标被立即否决、drop 永不触发。
        # 必须返回一个有效动作（COPY）。是否真正可接受由 on_drag_accept 与视觉
        # 反馈决定；这里始终接收以消费拖放（阻止文本视图插入路径）。
        return Gdk.DragAction.COPY

    def on_drag_motion(self, target, x, y):
        self._update_drop_feedback(target)
        return Gdk.DragAction.COPY

    def on_drag_leave(self, target):
        self._set_dnd_indicator(False)
        self._set_dnd_blank(False)
        self.drop_highlight.set_visible(False)
        self.drop_highlight.remove_css_class('drop-reject')
        return False

    def on_drag_accept(self, target, drop):
        '''必须返回 True 才能被 GTK 选为拖放目标。一旦返回 False，GTK 会直接
        否决该目标，导致 enter/motion/drop 永远不触发。文件类型已由 gtypes 限定，
        这里始终接收；红框/计数等视觉状态统一由 _update_drop_feedback 根据已读取
        的文件列表同步，拖放仍由 on_drop 消费掉。'''
        self._update_drop_feedback(target)
        return True

    def on_drop(self, target, value, x, y):
        workspace = ServiceLocator.get_workspace()
        self._set_dnd_indicator(False)
        self._set_dnd_blank(False)
        self.drop_highlight.set_visible(False)
        self.drop_highlight.remove_css_class('drop-reject')
        # drop 信号的 value 参数在某些构建中未被正确解包（为空），此时回退到
        # target.get_value()——拖拽过程中 _update_drop_feedback 正是靠它拿到文件列表。
        files = self._extract_drop_files(value)
        if not files:
            files = self._extract_drop_files(target.get_value())
        if not files:
            return False
        # 始终消费拖放（返回 True），避免文本视图接管并插入路径。
        # 仅打开白名单内的文件，其余静默忽略。
        if workspace is not None:
            for file in files:
                path = file.get_path()
                if path and self._is_acceptable_file(file):
                    workspace.open_document_by_filename(path)
        return True


    def do_size_allocate(self, width, height, baseline):
        Adw.ApplicationWindow.do_size_allocate(self, width, height, baseline)

        # 根据浮层 headerbar 的实际高度动态调整编辑器内容/欢迎页的上边距，
        # 保证内容不会被标题栏遮住，同时避免硬编码固定高度。
        # Pass-12: 预览/帮助侧栏不再被标题栏覆盖——它们有自己的工具栏，
        # 故不再设置 preview_panel.stack / help_panel.stack 的 margin_top。
        # 全屏隐藏 headerbar 时 margin_top 设为 0，让内容占满全高。
        if hasattr(self, 'headerbar') and hasattr(self, 'document_stack_wrapper'):
            headerbar_height = self.headerbar.widget.get_allocated_height()
            if self._is_fullscreen and not self._headerbar_visible_in_fullscreen:
                # 全屏且 headerbar 隐藏：内容不需要上边距
                target_margin = 0
            else:
                target_margin = headerbar_height
            if target_margin >= 0 and self.document_stack_wrapper.get_margin_top() != target_margin:
                self.document_stack_wrapper.set_margin_top(target_margin)
            if hasattr(self, 'welcome_screen') and self.welcome_screen.get_margin_top() != target_margin:
                self.welcome_screen.set_margin_top(target_margin)

    def reparent_headerbar(self, to_welcome):
        '''在 welcome_overlay 与 document_stack_overlay 之间迁移 headerbar。

        根因（为什么必须搬运，而不是更简单的方式）：
        - headerbar 是单一控件实例（按钮/信号/菜单气泡唯一绑定），同一时刻只能有
          一个父容器，无法同时出现在两个 overlay 上。
        - 设计上 headerbar 在文档模式下只浮在「编辑器列」上方（document_stack_overlay），
          以便左右预览/帮助侧栏保留各自顶部那一条 .sidebar-toolbar，不被标题栏遮挡。
        - 但欢迎模式没有「编辑器列/侧栏」这套结构，headerbar 必须改挂到 welcome_overlay
          才能盖住整个欢迎页。于是切模式时只能把它从一处物理搬到另一处。

        为什么不干脆做成窗口级标题栏（set_titlebar）来彻底避免搬运？
        ——那样标题栏会横跨整窗宽度，左右侧栏顶部工具栏会被压到标题栏下方，破坏
        「侧栏工具栏与标题栏顶对齐」的观感。当前设计优先保留该观感，因此接受搬运的
        脆弱性（可能丢焦点/关闭已打开的菜单气泡/触发断点重算），属刻意为之。

        无文档时 mode_stack 显示 welcome_screen，headerbar 叠在 welcome_overlay 上，
        使欢迎页仍能显示菜单按钮与「Welcome to Setzer」标题（open/create 按钮已在
        欢迎页正文，由 activate_welcome_screen_mode 隐藏）；有文档时回到
        document_stack_overlay，只覆盖编辑器列。迁移由 presenter 在 mode_stack 切页
        前后调用，避免可见瞬间的空标题栏。'''
        if to_welcome == self._headerbar_in_welcome:
            return
        hb = self.headerbar.widget
        if to_welcome:
            self.document_stack_overlay.remove_overlay(hb)
            self.welcome_overlay.add_overlay(hb)
            self._headerbar_in_welcome = True
        else:
            self.welcome_overlay.remove_overlay(hb)
            self.document_stack_overlay.add_overlay(hb)
            self._headerbar_in_welcome = False
        # 拖放高亮浮层与 headerbar 始终位于同一 overlay：documents 模式下它只覆盖
        # 编辑器列（与卡片同形），welcome 模式下覆盖整窗。二者随模式切换同步迁移。
        self._reparent_drop_highlight(to_welcome)

    def _reparent_drop_highlight(self, to_welcome):
        '''把拖放高亮浮层随 headerbar 一起在 welcome_overlay 与 document_stack_overlay
        间迁移，使其始终与标题栏同处一个 overlay（从而自动对齐到对应区域）。'''
        if to_welcome == self._drop_highlight_in_welcome:
            return
        if to_welcome:
            self.document_stack_overlay.remove_overlay(self.drop_highlight)
            self.welcome_overlay.add_overlay(self.drop_highlight)
        else:
            self.welcome_overlay.remove_overlay(self.drop_highlight)
            self.document_stack_overlay.add_overlay(self.drop_highlight)
        self._drop_highlight_in_welcome = to_welcome

    def toggle_fullscreen(self):
        '''切换全屏模式。F11 快捷键入口。'''
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_fullscreened_changed(self, window, param):
        '''全屏状态变化回调：保存/恢复 UI 状态。'''
        self._is_fullscreen = self.is_fullscreen()
        if self._is_fullscreen:
            self._enter_fullscreen()
        else:
            self._exit_fullscreen()
        # 更新编辑器卡片在全屏 + shortcut 隐藏时的顶部边距
        self._update_fullscreen_editor_margin()

    def _on_shortcutsbar_visibility_changed(self, shortcutsbar, param):
        '''shortcut 栏可见性变化回调：联动更新编辑器卡片顶部边距。'''
        self._update_fullscreen_editor_margin()

    def _update_fullscreen_editor_margin(self):
        '''F11 全屏 + shortcut 栏隐藏 + headerbar 也隐藏时，编辑器卡片顶部加 6px 间距。

        鼠标靠近顶部边缘时 headerbar 会临时显示（_headerbar_visible_in_fullscreen=True），
        此时不需要额外的顶部间距，避免内容与 headerbar 重叠。'''
        fullscreen = getattr(self, '_is_fullscreen', False)
        shortcuts_hidden = not self.shortcutsbar.get_visible()
        headerbar_hidden = fullscreen and not getattr(self, '_headerbar_visible_in_fullscreen', True)
        if fullscreen and shortcuts_hidden and headerbar_hidden:
            self.document_stack_wrapper.add_css_class('fullscreen-no-shortcuts')
        else:
            self.document_stack_wrapper.remove_css_class('fullscreen-no-shortcuts')

    def _enter_fullscreen(self):
        '''进入全屏：隐藏 headerbar，折叠 sidebar。'''
        workspace = ServiceLocator.get_workspace()
        if workspace is None:
            return
        # 重置 popover / 指针追踪状态，避免上次全屏残留的标志影响本次判定。
        if self._headerbar_hide_timeout_id is not None:
            GLib.Source.remove(self._headerbar_hide_timeout_id)
            self._headerbar_hide_timeout_id = None
        self._popover_open = False
        self._pointer_inside = True
        self._last_pointer_y = 0
        # 保存并隐藏 headerbar
        self._show_fullscreen_headerbar(False)
        # 折叠 sidebar（如果开启）
        self._sidebar_was_visible = (workspace.show_symbols or workspace.show_document_structure)
        if self._sidebar_was_visible:
            workspace.set_show_sidebar(False)

    def _exit_fullscreen(self):
        '''退出全屏：恢复 headerbar 和 sidebar。'''
        workspace = ServiceLocator.get_workspace()
        if workspace is None:
            return
        # 取消任何待隐藏计时并重置追踪状态。
        if self._headerbar_hide_timeout_id is not None:
            GLib.Source.remove(self._headerbar_hide_timeout_id)
            self._headerbar_hide_timeout_id = None
        self._popover_open = False
        self._pointer_inside = True
        # 恢复 headerbar
        self._show_fullscreen_headerbar(True)
        # 恢复 sidebar
        if self._sidebar_was_visible:
            workspace.set_show_sidebar(True)

    def _show_fullscreen_headerbar(self, show):
        '''全屏时显示/隐藏 headerbar（通过 opacity 过渡）。'''
        self._headerbar_visible_in_fullscreen = show
        headerbar = self.headerbar.widget
        if show:
            headerbar.set_opacity(1.0)
            headerbar.set_can_target(True)
        else:
            headerbar.set_opacity(0.0)
            headerbar.set_can_target(False)
        # 触发重新分配以更新 margin
        self.queue_resize()
        # 同步更新编辑器卡片在全屏 + shortcut 隐藏时的顶部边距
        self._update_fullscreen_editor_margin()

    def _on_motion(self, controller, x, y):
        '''全屏下根据鼠标位置显隐 headerbar。

        - headerbar 隐藏时：鼠标进入顶部边缘（<=阈值）即显示。
        - headerbar 显示后：鼠标仍在 headerbar 高度内则保持；移动到 headerbar
          之外且无打开 popover 时，经延时再隐藏，避免点击按钮打开下拉菜单后
          鼠标短暂移出顶栏导致闪退。'''
        if not self._is_fullscreen:
            return
        self._pointer_inside = True
        self._last_pointer_y = y

        if not self._headerbar_visible_in_fullscreen:
            show = y <= 5
        else:
            headerbar_height = self.headerbar.widget.get_allocated_height()
            show = y <= headerbar_height

        if show != self._headerbar_visible_in_fullscreen:
            if show:
                self._cancel_hide_headerbar()
                self._show_fullscreen_headerbar(True)
            else:
                # 鼠标移出 headerbar 区域：无打开 popover 时才安排隐藏，
                # 否则交给定时器在 popover 关闭后再判定。
                if not self._popover_open:
                    self._schedule_hide_headerbar()
        else:
            # 状态未翻转：鼠标仍在 headerbar 内且无 popover 时取消待隐藏，
            # 防止此前因 popover 关闭等触发的延时隐藏在鼠标回到顶栏后误执行。
            if show and not self._popover_open:
                self._cancel_hide_headerbar()

    def _schedule_hide_headerbar(self):
        '''(重)启动隐藏顶栏的延时计时器。延时回调会再次核对 _popover_open
        与鼠标位置，因此可安全地在 leave / 移出等竞态场景下调用。'''
        if self._headerbar_hide_timeout_id is not None:
            GLib.Source.remove(self._headerbar_hide_timeout_id)
        self._headerbar_hide_timeout_id = GLib.timeout_add(1500, self._do_hide_headerbar)

    def _cancel_hide_headerbar(self):
        if self._headerbar_hide_timeout_id is not None:
            GLib.Source.remove(self._headerbar_hide_timeout_id)
            self._headerbar_hide_timeout_id = None

    def _do_hide_headerbar(self):
        '''延时隐藏：最终判定后才执行隐藏。'''
        self._headerbar_hide_timeout_id = None
        if not (self._is_fullscreen and self._headerbar_visible_in_fullscreen):
            return False
        # 有 popover 打开时绝不隐藏；其 closed 信号会重新评估。
        if self._popover_open:
            return False
        # 鼠标仍在窗口内且位于 headerbar 高度范围内，保持显示。
        headerbar_height = self.headerbar.widget.get_allocated_height()
        if self._pointer_inside and self._last_pointer_y <= headerbar_height:
            return False
        self._show_fullscreen_headerbar(False)
        return False

    def _on_leave(self, controller):
        '''鼠标离开主窗口：全屏时安排隐藏，但不在 leave 时立即隐藏——否则鼠标
        移入 popover（独立 surface）触发的 leave 会立刻收起顶栏。

        是否真正隐藏交由延时计时器在 fire 时核对 _popover_open 与鼠标位置决定，
        从而消解「leave 先于 activate / map 设置标志」的竞态。'''
        if not (self._is_fullscreen and self._headerbar_visible_in_fullscreen):
            return
        self._pointer_inside = False
        if not self._popover_open:
            self._schedule_hide_headerbar()

    def _on_headerbar_clicked(self, button):
        '''无 popover 的 headerbar 按钮（sidebar_toggle / 主操作区 /
        preview_help_toggle / center）：点击后取消待隐藏计时，保持顶栏可见。'''
        if not (self._is_fullscreen and self._headerbar_visible_in_fullscreen):
            return
        self._cancel_hide_headerbar()

    def show_loading_spinner(self):
        '''显示加载中 spinner。'''
        self._loading_spinner.set_visible(True)
        self._loading_spinner.start()

    def hide_loading_spinner(self):
        '''隐藏加载中 spinner。'''
        self._loading_spinner.stop()
        self._loading_spinner.set_visible(False)

    def _on_split_button_activate(self, button):
        '''Adw.SplitButton 的下拉箭头只发 activate、不发 clicked；点击箭头
        展开 popover。立即标记打开并补连 popover 的 map / closed 信号。'''
        self._track_popover(button.get_popover())
        self._popover_open = True
        self._cancel_hide_headerbar()

    def _on_menu_button_activate(self, button):
        '''Gtk.MenuButton（汉堡菜单）没有 clicked 信号，用 activate。
        点击展开 popover，立即标记打开并补连 map / closed 信号。'''
        self._track_popover(button.get_popover())
        self._popover_open = True
        self._cancel_hide_headerbar()

    def _track_popover(self, popover):
        '''为 popover 连接 map / closed，可靠维护 _popover_open。

        GTK4 的 popover 用 map（而非 show）表示显示。用 _hb_tracked 标记
        防止重复连接（popover 可能首次激活时才创建）。'''
        if popover is None:
            return
        if getattr(popover, '_hb_tracked', False):
            return
        popover.connect('map', self._on_popover_opened)
        popover.connect('closed', self._on_popover_closed)
        popover._hb_tracked = True

    def _on_popover_opened(self, popover):
        '''popover 显示：标记打开并取消待隐藏计时。'''
        self._popover_open = True
        self._cancel_hide_headerbar()

    def _on_popover_closed(self, popover):
        '''popover 关闭：解除标记并重新评估是否隐藏顶栏。'''
        self._popover_open = False
        if self._is_fullscreen and self._headerbar_visible_in_fullscreen:
            headerbar_height = self.headerbar.widget.get_allocated_height()
            if self._pointer_inside and self._last_pointer_y <= headerbar_height:
                # 鼠标仍在 headerbar 上，保持显示。
                self._cancel_hide_headerbar()
            else:
                self._schedule_hide_headerbar()
        return False

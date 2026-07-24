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
from gi.repository import Adw, Gtk, GLib, GObject

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
        self.set_content(self.popoverlay)

    def create_widgets(self):
        self.shortcutsbar = shortcutsbar_view.Shortcutsbar()

        self.document_stack = Gtk.Stack()
        # 用 NONE 而非 CROSSFADE：CROSSFADE 有约 200ms 淡入淡出动画，期间
        # 旧页面与新页面同时绘制，切换文档（尤其是「新建 latex」）时左侧编辑器
        # 会延迟出现，给人「更新不及时/卡顿」的感觉。NONE 立即切换，无视觉延迟。
        self.document_stack.set_transition_type(Gtk.StackTransitionType.NONE)
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
        self.sidebar_split.set_min_sidebar_width(252)
        self.sidebar_split.set_max_sidebar_width(600)
        self.sidebar_split.set_sidebar_width_fraction(0.25)

        self.welcome_screen = welcome_screen_view.WelcomeScreenView()

        # welcome_overlay：把欢迎页包进 Gtk.Overlay，以便 headerbar 在欢迎页
        # 模式下作为浮层叠在欢迎页顶部（无文档时 mode_stack 显示 welcome_screen，
        # 此时 headerbar 必须可见，否则用户无法点 open/create 按钮开始编辑——
        # 而欢迎页文字也写着「Click the open or create buttons in the headerbar
        # above」）。headerbar 是单一控件，通过 reparent_headerbar() 在
        # welcome_overlay 与 document_stack_overlay 之间迁移，详见该方法注释。
        self.welcome_overlay = Gtk.Overlay()
        self.welcome_overlay.set_child(self.welcome_screen)

        self.mode_stack = Gtk.Stack()
        self.mode_stack.add_named(self.welcome_overlay, 'welcome_screen')
        self.mode_stack.add_named(self.sidebar_split, 'documents')

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

        # 记录 headerbar 当前所在 overlay，供 reparent_headerbar 判断迁移方向。
        # 不用 get_parent()：Gtk.Overlay 的 overlay 子部件实际父级是内部 Bin，
        # 而非 Gtk.Overlay 本身，get_parent() 比较会失真。
        self._headerbar_in_welcome = False

        self.content_overlay = Gtk.Overlay()
        self.content_overlay.set_child(self.mode_stack)
        self.popoverlay.set_child(self.content_overlay)

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

        self.css_provider_font_size = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.css_provider_font_size, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        # 加载项目自定义 CSS（仅 shortcutsbar 的 FlowBoxChild padding 归零等少量微调，
        # 见 data/resources/style_gtk.css）。FONT 优先级在 USER 之上，确保我们的
        # 微调规则不被 libadwaita 默认 flowbox 样式覆盖。
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


    def do_size_allocate(self, width, height, baseline):
        Adw.ApplicationWindow.do_size_allocate(self, width, height, baseline)

        # 根据浮层 headerbar 的实际高度动态调整编辑器内容/欢迎页的上边距，
        # 保证内容不会被标题栏遮住，同时避免硬编码固定高度。
        # Pass-12: 预览/帮助侧栏不再被标题栏覆盖——它们有自己的工具栏，
        # 故不再设置 preview_panel.stack / help_panel.stack 的 margin_top。
        if hasattr(self, 'headerbar') and hasattr(self, 'document_stack_wrapper'):
            headerbar_height = self.headerbar.widget.get_allocated_height()
            if headerbar_height > 0:
                if self.document_stack_wrapper.get_margin_top() != headerbar_height:
                    self.document_stack_wrapper.set_margin_top(headerbar_height)
                if hasattr(self, 'welcome_screen') and self.welcome_screen.get_margin_top() != headerbar_height:
                    self.welcome_screen.set_margin_top(headerbar_height)

    def reparent_headerbar(self, to_welcome):
        '''在 welcome_overlay 与 document_stack_overlay 之间迁移 headerbar。

        headerbar 是单一控件实例（按钮/信号唯一绑定），不能同时在两处。无文档时
        mode_stack 显示 welcome_screen，此时 headerbar 必须叠在 welcome_overlay
        上，否则 open/create 按钮不可见、用户无法开始编辑；有文档时 headerbar
        回到 document_stack_overlay（只覆盖编辑器列），保留预览/帮助侧栏的完整
        高度（侧栏顶部有自己的 .sidebar-toolbar 工具栏）。模式切换时由
        presenter 调用本方法迁移，迁移在 mode_stack 切页前后完成，避免可见
        瞬间的空标题栏。'''
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

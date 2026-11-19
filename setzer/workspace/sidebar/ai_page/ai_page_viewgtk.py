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
# along with this program. If not, see <http://www.gnu.org/licenses/>

"""侧栏 AI 页视图：工具栏（视图档位 + 面板切换器）+ 单栈内容。

批注列表用与 document structure 页同款组件栈：
Adw.PreferencesPage → PreferencesGroup(.sidebar-section-group) →
ListBox(.boxed-list)。列表不设 vexpand，只占内容自然高度，空白处
露出页面背景；到侧栏两边的间距由 Adw 样式表提供，与结构页一致。
唯一例外：PreferencesPage 默认的 24px 上外边距在 style_gtk.css 里对
`.ai-page` 清零，让首条批注顶边与工具栏下沿（= 右侧标签条 / 预览卡片顶边）
齐平——本页分组没有标题，不需要那点呼吸空间。

批注列表与 Agent 终端（AgentTerminalView：迷你工具栏在卡片外、圆角卡片
仅 VTE）用 Git 页 Changes/Commits 同款 .round ToggleGroup 切档：内容单栈
不同屏展现，一次只看一个，不再上下分栏。Send All 是批注档列表上方的
整行图标+文字按钮（git 页 Commit 行同款），在档内随列表显隐——空列表
与终端档都不会出现它。终端弹出独立窗口时经 detach/attach_agent_panel
从栈里整体摘装（VTE 与 PTY 随 widget 搬移）。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.workspace.sidebar.sidebar_viewgtk import build_panel_switch_group

# AI 页内部档位：(档位名, 图标)。纯视图层状态，控制器连 notify::active
# 驱动内容栈（ToggleGroup 无 toggled 信号）。图标：comment = 批注
# （icon-dev-kit 自带，与 boolean/commit 同源打包）、utilities-terminal =
# 终端（主题图标，终端卡片空态同款）。
AI_VIEW_OPTIONS = (
    ('annotations', 'comment-symbolic'),
    ('agent', 'utilities-terminal-symbolic'),
)


class AiPageView(Gtk.Box):

    def __init__(self, terminal_view):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        # 供 style_gtk.css 精确作用到本页（去掉批注列表默认的 24px 上外边距，
        # 让首条批注顶边与工具栏下沿 / 标签条顶边对齐）。
        self.add_css_class('ai-page')
        self.terminal_view = terminal_view

        # ---- 顶部工具栏（与其他侧栏页同款 .sidebar-toolbar）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)

        # 左：AI 页内部 批注/终端 图标档位（同 Git 页 Changes/Commits）。
        # 用 libadwaita 内置的 .round 变体：整组为全圆角胶囊底槽、选中档为
        # 圆角矩形高亮，与右侧面板切换器的常规分段控件形成区分。名称由
        # tooltip 展示；tooltip 必须写字面量 _() 调用——经变量传的字符串
        # xgettext 提取不到（git 页踩过：Changes/Commits 因此缺席 pot）。
        self.view_toggle = Adw.ToggleGroup()
        self.view_toggle.add_css_class('round')
        self.view_toggle.set_valign(Gtk.Align.CENTER)
        self.view_toggle.set_homogeneous(True)
        tooltips = {'annotations': _('Annotations'), 'agent': _('Agent CLI')}
        for name, icon_name in AI_VIEW_OPTIONS:
            self.view_toggle.add(Adw.Toggle(
                name=name, icon_name=icon_name, tooltip=tooltips[name]))
        self.view_toggle.set_active_name('annotations')
        self.toolbar.append(self.view_toggle)

        # 弹性空档：把面板切换器推到最右（档位固定在左）。
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.toolbar.append(spacer)

        # 与其他页互为镜像的面板切换器（sidebar_viewgtk 统一同步）。
        self.switch_group = build_panel_switch_group()
        self.toolbar.append(self.switch_group)

        self.append(self.toolbar)

        # ---- 内容单栈：批注列表 / Agent 终端。档位是单一事实源，栈由
        # 控制器的 notify::active 回调驱动；弹出/收回由 detach/attach 摘装。
        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)

        # Send All：整行图标+文字按钮放批注档顶部（git 页 Commit 行同款
        # suggested-action；图标与批注行的「发送单条」一致）。放档内不放
        # 工具栏：终端档不该出现它，空列表时随 'list' 子页一起隐藏。
        # 左右 10px 对齐 PreferencesPage clamp 的列表卡片边缘（全侧栏统一
        # 10px 边缘线）；底距 6px 对齐 git 页内容的行距节奏（顶部呼吸由
        # 工具栏 padding 提供）。
        self.send_all_button = Gtk.Button()
        send_all_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        send_all_box.set_halign(Gtk.Align.CENTER)
        send_all_box.append(Gtk.Image(icon_name='mail-send-symbolic'))
        send_all_box.append(Gtk.Label(label=_('Send All')))
        self.send_all_button.set_child(send_all_box)
        self.send_all_button.add_css_class('suggested-action')
        self.send_all_button.set_margin_start(10)  # --setzer-spacing-edge
        self.send_all_button.set_margin_end(10)
        self.send_all_button.set_margin_bottom(6)

        # 与 document structure 页同款：PreferencesPage 自带滚动与边距，
        # ListBox 不 vexpand → 只占内容高度，下方空白是背景而非列表延伸。
        self.list_page = Adw.PreferencesPage()
        self.list_group = Adw.PreferencesGroup()
        self.list_group.add_css_class('sidebar-section-group')
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')
        self.list_group.add(self.list_box)
        self.list_page.add(self.list_group)
        # 列表页 vexpand 吃掉按钮行以下的剩余高度：长列表在其内部
        # ScrolledWindow 滚动，Send All 固定在顶不随内容滚走。
        self.list_page.set_vexpand(True)
        list_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_wrapper.set_vexpand(True)
        list_wrapper.append(self.send_all_button)
        list_wrapper.append(self.list_page)
        self.list_stack = Gtk.Stack()
        self.list_stack.set_vexpand(True)
        self.list_stack.add_named(list_wrapper, 'list')

        self.empty_status = Adw.StatusPage()
        self.empty_status.set_icon_name('ai-slop-symbolic')
        self.empty_status.set_title(_('No Annotations'))
        self.empty_status.set_description(_(
            'Select text in the editor and use the right-click menu or shortcut '
            'to add an AI annotation.'))
        # 与 document structure 空状态同款外观（48px 图标 / 紧凑字号）。
        self.empty_status.add_css_class('compact')
        self.empty_status.add_css_class('sidebar-empty-state')
        self.empty_status.set_vexpand(True)
        child = self.empty_status.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ScrolledWindow):
                child.set_propagate_natural_height(True)
                break
            child = child.get_next_sibling()
        self.list_stack.add_named(self.empty_status, 'empty')
        self.list_stack.set_visible_child_name('empty')

        self.content_stack.add_named(self.list_stack, 'annotations')
        self.content_stack.add_named(self.terminal_view, 'agent')
        self.append(self.content_stack)

    # ---- 视图档位 / 内容栈 ----

    def set_content_view(self, name):
        '''切内容栈（'annotations' / 'agent'）。档位是事实源，正常路径经
        set_active_view 触发控制器回调走到这里。'''
        self.content_stack.set_visible_child_name(name)

    def set_active_view(self, name):
        '''程序化切档；notify::active 由控制器接管后同步内容栈。'''
        self.view_toggle.set_active_name(name)

    def detach_agent_panel(self):
        '''弹出终端前从内容栈整体摘下（reparent 前必须无父），档位弹回批注。'''
        self.content_stack.remove(self.terminal_view)
        self.set_active_view('annotations')

    def attach_agent_panel(self):
        '''收回终端：装回内容栈并切到终端档。'''
        self.content_stack.add_named(self.terminal_view, 'agent')
        self.set_active_view('agent')

    def set_has_annotations(self, has_annotations):
        # Send All 在 'list' 子页内：空列表切到空态子页时整行按钮随之
        # 隐藏，无需置灰。
        self.list_stack.set_visible_child_name('list' if has_annotations else 'empty')

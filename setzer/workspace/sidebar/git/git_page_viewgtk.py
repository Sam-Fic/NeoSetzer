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

"""侧栏 Git 页视图：工具栏（Git 内档位 + 面板切换器）+ 内容。

Git 从 Document Structure 页的一个分区升级为独立面板页。内容直接复用
GitSectionView（repository / init / no-document / unavailable 四态）。

工具栏左上角是 Git 页内部的 Changes/Commits 文字档位（Adw.ToggleGroup，
驱动 GitSectionView.set_repo_view）——与 VSCode Source Control 的差异点：
两块内容不同屏展现，一次只看一个。右侧仍是与其他页互为镜像的面板切换器。

内容不用 Adw.PreferencesPage + PreferencesGroup 包裹：那条链路上 expand
标记不会传给分组内容，页内剩余高度到不了 GitSectionView，空状态只会顶在
页面顶端无法垂直居中（实测）。改用普通 vexpand Box 直接装，手动补 12px
水平边距对齐其他页（其他页的这 12px 来自 PreferencesPage clamp box 的
margin，见 style_gtk.css 对 .ai-page 的注释）。
"""

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from setzer.workspace.sidebar.sidebar_viewgtk import build_panel_switch_group

# Git 页内部档位：(档位名, 图标, tooltip)。纯视图层状态，控制器只管填充两个内容页。
# 图标为应用自带（用户提供）：boolean = 改动勾选、commit = 提交历史。
GIT_VIEW_OPTIONS = (
    ('changes', 'boolean-symbolic', 'Changes'),
    ('commits', 'commit-symbolic', 'Commits'),
)


class GitPageView(Gtk.Box):

    def __init__(self, content):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.content = content

        # ---- 顶部工具栏（与其他侧栏页同款 .sidebar-toolbar）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)

        # 左：Git 页内部 Changes/Commits 图标档位（VSCode 式分组切换，
        # 但不同屏展现）。用 libadwaita 内置的 .round 变体：整组为全圆角
        # 胶囊底槽、选中档为圆角矩形高亮，与右侧面板切换器的常规分段控件
        # 形成区分。名称由 tooltip 展示。
        self.view_toggle = Adw.ToggleGroup()
        self.view_toggle.add_css_class('round')
        self.view_toggle.set_valign(Gtk.Align.CENTER)
        self.view_toggle.set_homogeneous(True)
        for name, icon_name, label in GIT_VIEW_OPTIONS:
            self.view_toggle.add(Adw.Toggle(name=name, icon_name=icon_name,
                                            tooltip=_(label)))
        self.view_toggle.set_active_name('changes')
        self.view_toggle.connect('notify::active', self.on_view_toggle)
        self.toolbar.append(self.view_toggle)

        # 右：与其他页互为镜像的面板切换器（sidebar_viewgtk 统一同步）。
        self.switch_group = build_panel_switch_group()
        self.switch_group.set_halign(Gtk.Align.END)
        self.switch_group.set_hexpand(True)
        self.toolbar.append(self.switch_group)

        self.append(self.toolbar)

        # ---- 内容：GitSectionView（vexpand 链路见模块 docstring）----
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_vexpand(True)
        # 横向 margin 归零：10px 边缘线改由页面内部承担——列表卡片的边距必须
        # 落在滚动视口内（ListBox margin，见 .git-card-list），其它控件由
        # .git-section-box > * 补同样的边距。加在这里（scroller 外）无效，
        # 视口会把卡片的 box-shadow 裁掉。
        # 底部 12px：列表滚到底时，卡片下沿到窗口底不贴边（先例：统计页）。
        content_box.set_margin_bottom(12)
        content_box.append(content)
        self.append(content_box)

    def on_view_toggle(self, group, pspec=None):
        name = group.get_active_name()
        if name is not None:
            self.content.set_repo_view(name)

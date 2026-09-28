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


# 侧栏两页的互斥切换档位：(面板名, 图标, 无障碍名/提示)。
# 面板名同时用作 Gtk.Stack 的子页名与 ToggleGroup 的档位名。
#
# 提示文字经 _(label) 运行时查表翻译：msgid 与「偏好设置 → 快捷键」页里的
# 同名条目共用（page_shortcuts.py 中有字面量 _() 调用，xgettext 据此提取），
# 因此无需在本文件重复写一遍字面量也能拿到译文。
PANEL_SWITCH_OPTIONS = (
    ('document_structure', 'view-list-symbolic', 'Document Structure'),
    ('symbols', 'emoji-symbols-symbolic', 'Symbols'),
)


def build_panel_switch_group():
    '''构建侧栏面板切换器（libadwaita「Group with Icons」分段控件）。

    图标档位 + 当前档高亮，取代原先「单个按钮、图标显示目标面板」的写法：
    两个面板同时可见、单击直达，也省掉了切换时改写按钮图标的逻辑。
    两页工具栏各放一个实例，它们由 Sidebar.set_visible_child_name() 统一同步。
    '''
    group = Adw.ToggleGroup()
    # 不加 .flat：保留分段控件自带的底槽（.flat 会移除该背景，把它变成一排
    # 独立按钮）。底槽让两档的归属关系一眼可辨，与旁边 .flat 的导航/查找按钮
    # 也形成层次区分。
    group.set_valign(Gtk.Align.CENTER)
    group.set_homogeneous(True)
    for name, icon_name, label in PANEL_SWITCH_OPTIONS:
        group.add(Adw.Toggle(name=name, icon_name=icon_name, tooltip=_(label)))
    return group


class Sidebar(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)
        self.append(self.stack)

        # 面板名 -> 该页工具栏上的切换器。两个切换器互为镜像，权威状态是
        # Gtk.Stack 的可见子页（而非某个本地布尔），避免快捷键 / 会话恢复等
        # 旁路切换后出现图标与实际显示不一致。
        self.switch_groups = dict()
        self._syncing_switch = False

    def register_switch_group(self, name, group):
        '''登记某页工具栏上的面板切换器（见 build_panel_switch_group）。

        登记时立即对齐一次当前可见页，这样即使注册晚于首次
        set_visible_child_name()（例如会话恢复先拨了页），高亮也不会停在
        默认档位上。'''
        self.switch_groups[name] = group
        self._set_group_active(group, self.stack.get_visible_child_name())

    def set_visible_child_name(self, name):
        self.stack.set_visible_child_name(name)
        self._sync_switch_groups(name)

    def _set_group_active(self, group, name):
        '''把单个切换器拨到 name；name 不是本组档位时保持原状。

        Adw.ToggleGroup.set_active_name() 对未知名字会打 CRITICAL 并拒绝赋值，
        故先确认档位存在（侧栏将来若加第三个页面，未同步登记的组不会刷警告）。'''
        if name is not None and group.get_toggle_by_name(name) is not None:
            group.set_active_name(name)

    def _sync_switch_groups(self, name):
        '''把各页工具栏上的切换器拨到当前可见面板。

        重入保护：set_active_name() 会发射 notify::active，控制器据此回调
        set_visible_child_name()；不拦就会来回递归。'''
        self._syncing_switch = True
        try:
            for group in self.switch_groups.values():
                self._set_group_active(group, name)
        finally:
            self._syncing_switch = False

    def add_named(self, child, name):
        self.stack.add_named(child, name)

    def get_visible_child(self):
        return self.stack.get_visible_child()



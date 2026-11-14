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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gio, GLib

from setzer.app.service_locator import ServiceLocator


def _(s):
    '''翻译函数。运行时委托 ``builtins._``（由 setzer.in 注入
    trans.gettext），未注入时回退原字符串（开发/测试）。'''
    import builtins
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


class TabContextMenu(object):
    '''标签条（Adw.TabBar）右键菜单——Adw.TabView 原生方案。

    Adw.TabView 自带右键菜单支持：右键标签时 libadwaita 内部先 emit
    setup-menu(page)，再用 view 的 menu-model 弹出菜单（菜单项的
    action 前缀取 view 所在 widget 树上插入的动作组，此处为 main_window
    的 'win' 组）。page 由 libadwaita 直接给出，无需自行做 per-tab 命中
    测试——旧实现对 TabBar 私有结构（tabboxchild css 名、pinned class）
    的反查已随该迁移整体删除。

    菜单项是 'win.pin-tab' / 'win.unpin-tab' / 'win.close-tab'
    （actions.py 注册）。Pin/Unpin 共用 toggle 语义但拆成两个动作，
    菜单项用 hidden-when="action-disabled" 按 pinned 状态二选一显示
    （Gio.Menu 不支持动态文案，官方 libadwaita demo 同款模式）；
    update_actions() 负责按当前活跃文档同步动作 enable 态，setup-menu
    时再按目标文档覆盖一次。菜单把取消固定路由到 win.unpin-tab；快捷键
    Ctrl+Alt+P 仍为 toggle（pin_tab），不受菜单拆分影响。
    Close Tab 对 pinned 文档禁用——libadwaita 默认协议拒绝关闭 pinned
    页，菜单语义保持一致。

    目标文档由 get_target_document() 提供：菜单打开期间为 setup-menu
    命中的文档，菜单关闭即清空——快捷键等无菜单路径由 actions 回退到
    当前活跃文档。
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self._target_document = None

        tab_view = ServiceLocator.get_main_window().document_stack

        # 命中标签 → 目标文档（菜单打开期间生效），并按目标状态调整
        # pin/unpin/close 动作 enable 态。page 为 None 的 emit 来自
        # libadwaita 的菜单复位（右键空白处等），此时清空目标即可。
        tab_view.connect('setup-menu', self.on_setup_menu)

        # 菜单模型：Pin/Unpin 二选一显示，Close 对 pinned 禁用。
        section = Gio.Menu()
        item = Gio.MenuItem.new(_('Pin Tab'), 'win.pin-tab')
        item.set_attribute_value('hidden-when',
                                 GLib.Variant.new_string('action-disabled'))
        section.append_item(item)
        item = Gio.MenuItem.new(_('Unpin Tab'), 'win.unpin-tab')
        item.set_attribute_value('hidden-when',
                                 GLib.Variant.new_string('action-disabled'))
        section.append_item(item)
        item = Gio.MenuItem.new(_('Close Tab'), 'win.close-tab')
        item.set_attribute_value('hidden-when',
                                 GLib.Variant.new_string('action-disabled'))
        section.append_item(item)
        model = Gio.Menu()
        model.append_section(None, section)
        tab_view.set_menu_model(model)

        # 目标文档的清空无需自行监听菜单关闭：libadwaita 在弹出菜单关闭后
        # 会 idle 发出 setup-menu(NULL) 复位（adw-tab-box.c 的
        # reset_setup_menu_cb），on_setup_menu 的 page=None 分支负责清空。
        # 时序安全：菜单项点击的动作激活发生在 popover 关闭之前，此时
        # _target_document 仍有效。

    def get_target_document(self):
        '''右键菜单当前作用的目标文档；菜单未打开时为 None。'''
        return self._target_document

    def on_setup_menu(self, tab_view, page):
        if page is None:
            # 菜单关闭后的复位 emit（或右键空白处）：清空目标文档，之后
            # 快捷键等无菜单路径由 actions 回退到当前活跃文档。
            self._target_document = None
            return
        document = self.workspace.presenter._page_to_doc.get(page)
        self._target_document = document
        # 按目标文档覆盖动作 enable 态：pin/unpin 二选一，close 对
        # pinned 禁用。菜单关闭后 update_actions() 会按活跃文档恢复。
        actions = self.workspace.actions.actions
        pinned = self.workspace.is_document_pinned(document)
        actions['pin-tab'].set_enabled(not pinned)
        actions['unpin-tab'].set_enabled(pinned)
        actions['close-tab'].set_enabled(not pinned)

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
from gi.repository import Gtk, Gdk, Gio

import setzer.workspace.sidebar.document_structure_page.sidebar_list_viewgtk as sidebar_list_view
from setzer.app.service_locator import ServiceLocator


class LabelsSectionView(sidebar_list_view.SidebarListView):

    def __init__(self, model):
        sidebar_list_view.SidebarListView.__init__(self, model)
        self.set_placeholder_info(
            'tag-symbolic',
            _('No labels'),
            _('Add \\label{...} to create references to figures, sections, and more.')
        )
        # 右键菜单上下文：被右键的 label，win.label-ctx-* action 激活时读取。
        self._ctx_label = None
        # 菜单是静态三项（复制 \ref/\pageref/\autoref），常驻挂到
        # sidebar.menu_model；被右键的 label 由 setup-menu 信号更新。
        self.sidebar.set_menu_model(self._build_menu_model())
        # 把上下文 action 注册到主窗口而非 PopoverMenu（见 _register_context_actions）。
        self._register_context_actions()

    def populate(self):
        # 签名 = id(document) + 全部 (label 名, 问题标记) 元组。仅动正文时
        # 命中跳过；新增 \ref 使徽章状态变化时签名改变，触发重建。
        doc = self.model.data_provider.document
        signature = (id(doc), tuple((label[0], label[3]) for label in self.model.labels))
        if not self.populate_if_changed(signature):
            return
        self.clear_items()
        # 按来源文档分组：分组=来源维度，Unused/Duplicate 徽章=状态维度，
        # 两者解耦；组内保持 collate 排序不变。分组的 key 用 id(document)，
        # 组标题用 displayname。
        seen_documents = list()
        for label in self.model.labels:
            document = label[2]
            key = id(document)
            if key not in self._sections:
                self.add_section(key, document.get_displayname())
                seen_documents.append(key)
            item = self.make_item(label[0], icon_name='tag-symbolic')
            self._apply_problem_badge(item, label)
            item.item_data = label
            self.append_item(key, item)
        self.select_first()

    def _apply_problem_badge(self, item, label):
        # 问题标记 → (徽章文案, tooltip, css class)。字面量在使用处以 _()
        # 包裹（放类级常量里 xgettext 提取不到）；unused 用 dim 弱化呈现，
        # duplicate 用 error 色强调。徽章作为 item suffix 展示。
        flag = label[3] if len(label) > 3 else None
        if flag == 'unused':
            text, tooltip, css_class = (
                _('Unused'), _('This label is never referenced.'), 'dimmed')
        elif flag == 'duplicate':
            text, tooltip, css_class = (
                _('Duplicate'), _('This label is defined more than once.'), 'error')
        else:
            return
        badge = Gtk.Label(label=text)
        badge.add_css_class(css_class)
        badge.set_tooltip_text(tooltip)
        badge.set_valign(Gtk.Align.CENTER)
        item.set_suffix(badge)
        # 有问题时行 tooltip 换成问题说明（比 label 名更有用）。
        item.set_tooltip(tooltip)

    def _build_menu_model(self):
        menu = Gio.Menu()

        # Actions section
        actions_section = Gio.Menu()
        actions_section.append_item(Gio.MenuItem.new(_('Copy \\ref{...}'), 'win.label-ctx-copy-ref'))
        actions_section.append_item(Gio.MenuItem.new(_('Copy \\pageref{...}'), 'win.label-ctx-copy-pageref'))
        actions_section.append_item(Gio.MenuItem.new(_('Copy \\autoref{...}'), 'win.label-ctx-copy-autoref'))
        menu.append_section(None, actions_section)

        return menu

    def _register_context_actions(self):
        '''在 main_window 上注册带 win. 前缀的上下文 action。

        把 action 注册到窗口而非 PopoverMenu，可避免 Gtk.PopoverMenu 基于
        menu model 渲染的菜单项在点击时无法解析到 action group 的问题
        （与 files_viewgtk 的修复相同，见 commit 9c8e0c23）。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        if main_window.lookup_action('label-ctx-copy-ref') is not None:
            return  # 已经注册过

        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            main_window.add_action(action)

        add('label-ctx-copy-ref', self._copy_ref)
        add('label-ctx-copy-pageref', self._copy_pageref)
        add('label-ctx-copy-autoref', self._copy_autoref)

    def _on_setup_menu(self, sidebar, item):
        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()
        # 记录被右键的 label（点空白处时 item 为 None）。
        self._ctx_label = getattr(item, 'item_data', None) if item is not None else None

    def _copy_ref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\ref{' + label_name + '}')

    def _copy_pageref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\pageref{' + label_name + '}')

    def _copy_autoref(self, action, parameter):
        label_name = self._ctx_label[0]
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\autoref{' + label_name + '}')

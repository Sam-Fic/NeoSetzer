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
from gi.repository import GLib

# WebKit 是可选依赖（见 help_panel_viewgtk.py 的 HAS_WEBKIT 说明）。
# controller 中 on_policy_decision / on_context_menu / 导航按钮依赖 WebKit
# API，无 WebKit 时需跳过这些信号连接和方法。
try:
    gi.require_version('WebKit', '6.0')
    from gi.repository import WebKit
    HAS_WEBKIT = True
except (ValueError, ImportError):
    HAS_WEBKIT = False

import webbrowser
import threading
import os.path


class HelpPanelController(object):
    '''Pass-12: 按钮回到 help_panel 内嵌工具栏（与左侧栏一致），
    控制器直接通过 self.view.* 访问，无需 headerbar 引用。

    无 WebKit 时：导航按钮（back/next/up/home）禁用（无法控制 WebView），
    但搜索功能完整可用。点击搜索结果时用 webbrowser.open 打开本地 HTML
    文件替代 WebView 内导航。
    '''

    def __init__(self, help_panel, view):
        self.help_panel = help_panel
        self.view = view

        # 搜索 idle 去抖 id：search_entry.changed 每次按键都触发，原实现直接调
        # set_search_query 全量扫描索引（数千项）。连续输入一个词的每个字符都
        # 扫一遍，大索引时明显卡顿。改为 150ms 停顿后才搜索，合并连续按键。
        self._search_idle_id = None

        if HAS_WEBKIT:
            self.view.content.connect('decide-policy', self.on_policy_decision)
            self.view.content.connect('context-menu', self.on_context_menu)
            self.view.content.get_back_forward_list().connect('changed', self.on_back_forward_list_changed)
        else:
            # 无 WebKit：导航按钮无 WebView 可控制，禁用以明确告知用户。
            self.view.back_button.set_sensitive(False)
            self.view.next_button.set_sensitive(False)
            self.view.up_button.set_sensitive(False)
            self.view.home_button.set_sensitive(False)

        self.view.back_button.connect('clicked', self.on_back_button_clicked)
        self.view.next_button.connect('clicked', self.on_next_button_clicked)
        self.view.up_button.connect('clicked', self.on_up_button_clicked)
        self.view.home_button.connect('clicked', self.on_home_button_clicked)
        self.view.search_button.connect('toggled', self.on_search_button_toggled)
        self.view.search_entry.connect('changed', self.on_search_entry_changed)
        self.view.search_entry.connect('stop-search', self.on_search_stopped)

        # 搜索结果行激活：Adw.PreferencesGroup 无 row-activated 信号，改由
        # presenter 在创建每行时连 Adw.ActionRow 的 'activated' 信号到本方法。

    def on_back_button_clicked(self, button):
        if not HAS_WEBKIT: return
        self.view.search_button.set_active(False)
        self.view.content.go_back()

    def on_next_button_clicked(self, button):
        if not HAS_WEBKIT: return
        self.view.search_button.set_active(False)
        self.view.content.go_forward()

    def on_up_button_clicked(self, button):
        if not HAS_WEBKIT: return
        self.view.search_button.set_active(False)
        if self.view.content.get_uri() != self.help_panel.current_uri.split('#')[0] + '#':
            self.view.content.load_uri(self.help_panel.current_uri.split('#')[0] + '#')
        else:
            self.view.content.load_uri(self.help_panel.current_uri.split('#')[0] + '#top')

    def on_home_button_clicked(self, button):
        if not HAS_WEBKIT: return
        self.view.search_button.set_active(False)
        self.view.content.load_uri(self.help_panel.home_uri)

    def on_search_button_toggled(self, button):
        if button.get_active():
            self._open_search()
        else:
            if HAS_WEBKIT:
                self.view.search_content_box.set_visible(False)
                self.view.content.set_visible(True)
            else:
                # 无 WebKit 时 'content' 页只是占位 Label，退出搜索后留在搜索页
                # 不如保持搜索结果可见（用户可能还想看结果）。
                pass
            self.help_panel.workspace.presenter.focus_active_document()

    def open_search(self):
        '''打开帮助搜索页并聚焦搜索输入框。

        供搜索切换按钮以及全局 Ctrl+F 快捷键（帮助面板获得键盘焦点时）
        调用。即使搜索页已经打开也可安全重复调用：仅重新聚焦输入框，
        不会破坏其它状态。'''
        if not self.view.search_button.get_active():
            # 触发 'toggled' 信号 → _open_search()
            self.view.search_button.set_active(True)
        else:
            self._open_search()

    def _open_search(self):
        # content/search 两页互斥可见（不再用 Gtk.Stack 叠放，避免 content
        # 页覆盖并裁切 search 页边缘）。
        self.view.content.set_visible(False)
        self.view.search_content_box.set_visible(True)
        self.view.search_entry.set_text('')
        self.view.search_entry.grab_focus()
        self.help_panel.set_search_query(self.view.search_entry.get_text())
        # 预加载搜索索引：set_search_query('') 走空查询分支不会触发
        # _ensure_search_index，导致用户输入第一个字符后的首次搜索要
        # 同步承担 pickle.load + trigram 构建（~25ms / 2080 项）。此处
        # 面板刚展开、用户尚未敲键，把这次一次性开销移到无感知时刻，
        # 之后首次真实查询即即时响应（trigram 搜索本身 ~5ms）。
        self.help_panel._ensure_search_index()

    def on_search_entry_changed(self, entry):
        # 去抖：取消上一次待执行的搜索，重新计时。连续按键只会在停顿后触发一次
        # 全量索引扫描 + 结果重建。
        if self._search_idle_id is not None:
            GLib.source_remove(self._search_idle_id)
        # 注意：这里【不要】此刻读取并缓存 entry 文本。'changed' 可能在文本
        # 真正更新前/后于清空动作触发，若在此捕获文本再延迟执行，可能出现
        # “搜索框已空、却用旧文本（如残留的 'k'）执行了一次搜索”的竞态，
        # 表现为空搜索却像搜了一个字母。改为在 _do_search 触发时再读当前文本。
        self._search_idle_id = GLib.timeout_add(150, self._do_search)

    def _do_search(self):
        self._search_idle_id = None
        # 触发时才读取搜索框当前文本，避免捕获到清空前的残留字符。
        self.help_panel.set_search_query(self.view.search_entry.get_text())
        return False

    def on_search_stopped(self, entry):
        if self._search_idle_id is not None:
            GLib.source_remove(self._search_idle_id)
            self._search_idle_id = None
        self.view.search_button.set_active(False)

    def on_search_result_activated(self, row):
        if HAS_WEBKIT:
            # Adw.ActionRow：标题/副标题文本存于 title/subtitle（含高亮标记，
            # 但 set_uri_by_search_item 只取纯文本 URI，get_title 返回的也是
            # 含 <b> 的 markup 字符串 — 用于历史记录显示，无副作用）。
            self.help_panel.set_uri_by_search_item(row.uri_ending, row.get_title(), row.get_subtitle())
        else:
            # 无 WebKit：用系统浏览器打开本地 HTML 文件。
            # help_panel.path 是 'file:///.../resources/help'，uri_ending 是相对路径。
            html_path = os.path.join(self.help_panel.path.replace('file://', ''), row.uri_ending)
            if os.path.isfile(html_path):
                threading.Thread(target=webbrowser.open_new_tab,
                                 args=('file://' + html_path,), daemon=True).start()

    def on_back_forward_list_changed(self, back_forward_list, item_added=None, items_removed=None):
        if not HAS_WEBKIT: return
        self.view.back_button.set_sensitive(self.view.content.can_go_back())
        self.view.next_button.set_sensitive(self.view.content.can_go_forward())

    def on_policy_decision(self, view, decision, decision_type, user_data=None):
        if not HAS_WEBKIT: return False
        na = WebKit.PolicyDecisionType.NAVIGATION_ACTION
        nwa = WebKit.PolicyDecisionType.NEW_WINDOW_ACTION
        ra = WebKit.PolicyDecisionType.RESPONSE
        if decision_type == na or decision_type == nwa:
            uri = decision.get_navigation_action().get_request().get_uri()
            if uri.startswith(self.help_panel.path):
                self.help_panel.set_uri(uri)
                return True
            else:
                threading.Thread(target=webbrowser.open_new_tab, args=(uri,), daemon=True).start()
                decision.ignore()
                return True
        elif decision_type == ra:
            return False

    def on_context_menu(self, view, menu, event, hit_test_result, user_data=None):
        return True

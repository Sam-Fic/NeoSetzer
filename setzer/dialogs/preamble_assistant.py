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

'''Review-first UI for package recommendations in a document preamble.'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.app.latex_db import LaTeXDB
from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.project.preamble_assistant import PreambleAssistant


class PreambleAssistantDialog(DialogView):
    '''导言区宏包推荐助手。

    标准 DialogView 形态（与 spellchecking_words 等对话框一致）：
      - Adw.Dialog + DialogView 基类自带的 Adw.HeaderBar / Adw.ToolbarView，
        浮动呈现时有真正的标题栏（可拖拽、自动关闭按钮）；
      - Close / Add 按钮放标题栏两侧；
      - 建议以 Gtk.ListBox（boxed-list）+ Adw.ActionRow 行呈现，卡片自带
        背景与阴影，与弹窗背景分层；外层 Adw.Clamp 限宽 400px 居中，
        不铺满弹窗宽度（对齐 libadwaita demo 的 Boxed Lists 页）；
      - 弹窗尺寸恒定（620x410），不随建议条数变化；卡片只占真实行高，
        短内容时下方留白是弹窗背景，多条时列表内部滚动；
      - 无建议时用 Adw.StatusPage 作占位（ui-guidelines「空状态」）。
    '''

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        # 基类不保存 main_window，子类自存（bibliography_manager 同款）。
        self.main_window = main_window
        self.document = None
        self.suggestions = ()

        self.set_title(_('Preamble Assistant'))
        # 高度恒定：不随建议条数跳动；宽度让 400px 列表居中留白。
        # 卡片本身不膨胀（见下方 ScrolledWindow 注释），多余空间是弹窗背景。
        self.set_content_width(620)
        self.set_content_height(410)

        # 标题栏标题。
        self.headerbar.set_title_widget(
            Adw.WindowTitle(title=_('Preamble Assistant')))
        # 隐藏自动窗口控制按钮（右上角 ✕）：已有显式 Close 按钮与 Esc，
        # 双关闭入口冗余（spellchecking_words 同款做法）。标题栏仍是拖拽热区。
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        # 动作按钮入标题栏：Close 左、Add 右（suggested-action 主操作）。
        self.close_button = Gtk.Button.new_with_mnemonic(_('_Close'))
        self.close_button.connect('clicked', lambda *_: self.close())
        self.headerbar.pack_start(self.close_button)

        self.add_button = Gtk.Button.new_with_mnemonic(_('_Add suggested packages'))
        self.add_button.add_css_class('suggested-action')
        self.add_button.connect('clicked', self._on_add)
        self.headerbar.pack_end(self.add_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # 建议以 List 组件呈现：Gtk.ListBox + Adw.ActionRow。boxed-list 自带
        # @card_bg_color 卡片背景 + box-shadow，浮在弹窗背景之上形成层级
        # （libadwaita 的 AdwPreferencesGroup 用的是同一套样式）。
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.add_css_class('boxed-list')
        # 滚到底时最后一行不贴容器底边（wrap_content 同款处理）。
        self.listbox.set_margin_bottom(12)

        # 无建议时的空状态占位（ui-guidelines「空状态」用 Adw.StatusPage）。
        status = Adw.StatusPage()
        status.set_icon_name('dialog-information-symbolic')
        status.set_title(
            _('No missing package suggestions were found in this document.'))
        self.listbox.set_placeholder(status)

        # 限宽居中：与 libadwaita demo 的 Boxed Lists 页一致
        # (demo/pages/lists/adw-demo-page-lists.ui)，列表最大 400px，
        # 不会铺满 620px 弹窗；窄于 tightening_threshold 时随弹窗收缩。
        clamp = Adw.Clamp()
        clamp.set_maximum_size(400)
        clamp.set_tightening_threshold(300)
        clamp.set_child(self.listbox)

        scroll = Gtk.ScrolledWindow()
        scroll.hscrollbar_policy = Gtk.PolicyType.NEVER
        scroll.vscrollbar_policy = Gtk.PolicyType.AUTOMATIC
        # 卡片高度跟随内容、不撑满弹窗：propagate_natural_height 让卡片只有
        # 真实行高，短内容时下方留白是弹窗背景而非空壳卡片；超过
        # max_content_height 才内部滚动（search_history_popup 同款范式）。
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(340)
        scroll.set_child(clamp)
        content.append(scroll)

        # 内容替换基类的 topbox（spellchecking_words 同款做法），保留统一外边距。
        self.toolbar_view.set_content(content)

        # 记录已加入的行，便于每次 present() 重建列表时清除。
        self.suggestion_rows = []

    def present(self, document):
        self.document = document
        packages_detailed = getattr(document.parser, 'symbols', {}).get(
            'packages_detailed', {})
        self.suggestions = PreambleAssistant.suggest(
            document.get_all_text(), packages_detailed,
            LaTeXDB.get_packages_dict())
        self._render_suggestions()
        # 本方法遮蔽了 Adw.Dialog.present(widget)；显式以基类实现呈现自身，
        # 避免递归（bibliography_manager 同款写法）。
        Adw.Dialog.present(self, self.main_window)

    def _render_suggestions(self):
        # 清掉上一次 present() 留下的行，再按本次建议重建列表。
        for row in self.suggestion_rows:
            self.listbox.remove(row)
        self.suggestion_rows.clear()
        if not self.suggestions:
            # 列表为空时 set_placeholder 的 Adw.StatusPage 自动显示。
            self.add_button.set_sensitive(False)
            return
        for suggestion in self.suggestions:
            availability = _('available in NeoSetzer’s package database') \
                if suggestion.available_in_database else \
                _('not listed in NeoSetzer’s package database')
            row = Adw.ActionRow()
            row.set_title(suggestion.insertion)
            row.set_subtitle('{reason} ({availability})'.format(
                reason=suggestion.reason, availability=availability))
            # 默认副标题单行省略；此处允许完整换行显示原因与可用性说明。
            row.set_subtitle_lines(0)
            row.set_activatable(False)
            self.listbox.append(row)
            self.suggestion_rows.append(row)
        self.add_button.set_sensitive(True)

    def _on_add(self, *_args):
        if not self.suggestions:
            return
        confirmation = Adw.AlertDialog.new(
            _('Add suggested packages?'),
            _('This inserts {count} package declaration(s) into the document preamble.').format(
                count=len(self.suggestions)))
        confirmation.add_response('cancel', _('_Cancel'))
        confirmation.add_response('add', _('_Add packages'))
        confirmation.set_response_appearance('add',
                                              Adw.ResponseAppearance.SUGGESTED)
        confirmation.set_default_response('cancel')
        # self 即 Adw.Dialog，直接作为确认弹窗的父级。
        confirmation.choose(self, None, self._on_confirm_add)

    def _on_confirm_add(self, dialog, result):
        try:
            response = dialog.choose_finish(result)
        except Exception:
            return
        if response != 'add':
            return
        self.document.add_packages([suggestion.package
                                    for suggestion in self.suggestions])
        self._show_toast(_('Added {count} suggested package(s).').format(
            count=len(self.suggestions)))
        self.close()

    def _show_toast(self, message):
        toast = Adw.Toast.new(message)
        toast.set_timeout(5)
        self.main_window.toast_overlay.add_toast(toast)

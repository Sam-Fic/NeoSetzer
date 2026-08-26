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
from gi.repository import Adw, Gdk, Gtk

from setzer.app.latex_db import LaTeXDB
from setzer.dialogs.helpers.dialog_viewgtk import DialogView
from setzer.project.preamble_assistant import PreambleAssistant


class PreambleAssistantDialog(DialogView):
    '''导言区宏包推荐助手。

    标准 DialogView 形态（与 spellchecking_words 等对话框一致）：
      - Adw.Dialog + DialogView 基类自带的 Adw.HeaderBar / Adw.ToolbarView，
        浮动呈现时有真正的标题栏（可拖拽、自动关闭按钮）；
      - Close / Add 按钮放标题栏两侧（原为底部手拼按钮条）；
      - 解释性长句放内容区顶部 dim-label（WindowTitle 的 subtitle 在
        620px 宽度下会被省略号截断，故不作为 subtitle 使用）。
    '''

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        # 基类不保存 main_window，子类自存（bibliography_manager 同款）。
        self.main_window = main_window
        self.document = None
        self.suggestions = ()

        self.set_title(_('Preamble Assistant'))
        self.set_content_width(620)
        self.set_content_height(420)

        # 标题栏标题；解释性长句见内容区 hint。
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

        # 说明文字：dim-label + 自动换行，替代原 Adw.WindowTitle 的 subtitle。
        hint = Gtk.Label(label=_('Suggestions are based on commands in the document; '
                                 'nothing changes until you confirm.'))
        hint.add_css_class('dim-label')
        hint.set_halign(Gtk.Align.START)
        hint.set_xalign(0)
        hint.set_wrap(True)
        content.append(hint)

        self.buffer = Gtk.TextBuffer()
        view = Gtk.TextView(buffer=self.buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # 圆角卡片设计（ai_fix/preview_dialog_viewgtk 同款）：
        # 文字与外框双层内缩——TextView margin 16px + CSS padding 12px，
        # 让建议文本"陷"在圆角外框里。
        view.set_left_margin(16)
        view.set_right_margin(16)
        view.set_top_margin(16)
        view.set_bottom_margin(16)
        view.add_css_class('preamble-suggestions-view')
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        # 外框有圆角时必须裁剪滚动 overshoot/undershoot 提示条，
        # 否则滚动时上下浅灰条会溢出圆角矩形。
        scroll.set_overflow(Gtk.Overflow.HIDDEN)
        scroll.add_css_class('preamble-suggestions-scrolled')
        scroll.set_child(view)
        content.append(scroll)

        # 内容替换基类的 topbox（spellchecking_words 同款做法），保留统一外边距。
        self.toolbar_view.set_content(content)

        # 加载本弹窗专用 CSS（建议区圆角卡片外框与内边距）。
        self._load_css()

    def _load_css(self):
        '''加载本弹窗专用的 CSS（建议区圆角卡片）。

        与 ai_fix/preview_dialog_viewgtk._load_css 同款范式：应用级
        provider 仅追加一次，重复 add_provider 会被 GTK 幂等处理。
        命名色 @card_bg_color / @borders 自动跟随明暗主题。
        '''
        provider = Gtk.CssProvider()
        css = '''
        textview.preamble-suggestions-view {
            background-color: @card_bg_color;
            border: 1px solid @borders;
            border-radius: 8px;
        }
        textview.preamble-suggestions-view text {
            padding: 12px;
        }
        /* hide overshoot/undershoot indicators entirely: they paint on the
           ScrolledWindow edge and are NOT clipped by widget overflow, so they
           would bleed past the rounded frame. The rounded card already signals
           a scrollable area. */
        scrolledwindow.preamble-suggestions-scrolled overshoot,
        scrolledwindow.preamble-suggestions-scrolled undershoot {
            background-image: none;
            background-color: transparent;
            border: none;
            box-shadow: none;
        }
        '''
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

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
        if not self.suggestions:
            self.buffer.set_text(
                _('No missing package suggestions were found in this document.'))
            self.add_button.set_sensitive(False)
            return
        lines = []
        for suggestion in self.suggestions:
            availability = _('available in NeoSetzer’s package database') \
                if suggestion.available_in_database else \
                _('not listed in NeoSetzer’s package database')
            lines.append('{insertion}\n{reason} ({availability})'.format(
                insertion=suggestion.insertion, reason=suggestion.reason,
                availability=availability))
        self.buffer.set_text('\n\n'.join(lines))
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

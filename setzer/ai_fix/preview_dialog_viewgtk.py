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

"""AI 修复「发送前预览/确认」单弹窗视图（Adw.Dialog 范式，镜像 build_log_dialog_viewgtk）。

设计要点（见 .trae/documents/ai-fix-agent-integration.md §设计概览）：
  * 这一个弹窗就是「是否启动」的确认：用户点「发送」即确认；点「取消」即放弃。
    不再叠第二个确认弹窗。
  * 内容：可编辑 prompt（Gtk.TextView + monospace）+ 「此项目不再提示」复选框。
  * 已信任目录由 controller 侧判断后直接跳过本弹窗（不调 present）。

控制器通过 `set_on_send_callback` 注入回调；视图不直接持有 service。
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Pango

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


class PreviewDialogView(DialogView):
    '''AI 修复发送前预览/确认弹窗视图。'''

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_title(_('Send to AI Agent'))
        self.set_content_width(640)
        self.set_content_height(520)

        # HeaderBar 标题（移除默认关闭按钮，左侧已有 Cancel）
        self.headerbar.set_show_start_title_buttons(False)
        self.headerbar.set_show_end_title_buttons(False)

        self.title_widget = Adw.WindowTitle()
        self.title_widget.set_title(_('Send to AI Agent'))
        self.title_widget.set_subtitle('')
        self.headerbar.set_title_widget(self.title_widget)

        # 取消按钮（左侧，非 flat）
        self.cancel_button = Gtk.Button(label=_('Cancel'))
        self.cancel_button.set_can_focus(False)
        self.headerbar.pack_start(self.cancel_button)

        # 发送按钮（右侧，suggested-action 强调色）
        self.send_button = Gtk.Button(label=_('Send'))
        self.send_button.add_css_class('suggested-action')
        self.send_button.set_can_focus(False)
        self.headerbar.pack_end(self.send_button)

        # 内容：垂直堆叠 prompt TextView + 信任复选框
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)

        # 提示标签
        self.hint_label = Gtk.Label()
        self.hint_label.set_wrap(True)
        self.hint_label.set_xalign(0)
        self.hint_label.set_markup(_('Edit the prompt below if needed, then click <b>Send</b> to launch the agent.'))
        self.hint_label.add_css_class('dim-label')
        content_box.append(self.hint_label)

        # prompt 编辑区（可滚动，monospace）
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(280)
        # 关键：裁剪 overshoot/undershoot 提示条到圆角矩形内，
        # 避免滚动后上下浅灰条溢出外框。
        scrolled.set_overflow(Gtk.Overflow.HIDDEN)
        scrolled.add_css_class('ai-fix-scrolled')

        self.prompt_buffer = Gtk.TextBuffer()
        self.prompt_view = Gtk.TextView(buffer=self.prompt_buffer)
        self.prompt_view.set_monospace(True)
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        # 让文字与 TextView 边框保持更大的间距,使文本输入区"内缩"到外框里
        self.prompt_view.set_left_margin(16)
        self.prompt_view.set_right_margin(16)
        self.prompt_view.set_top_margin(16)
        self.prompt_view.set_bottom_margin(16)
        # 用 CSS 给 prompt 区加可见的灰色圆角矩形外框与背景
        self.prompt_view.add_css_class('ai-fix-prompt-view')
        scrolled.set_child(self.prompt_view)
        content_box.append(scrolled)

        # 「此项目不再提示」复选框
        self.dont_ask_check = Gtk.CheckButton(label=_('Don\'t ask again for this project'))
        self.dont_ask_check.set_tooltip_text(_('Skip this preview dialog for the current document\'s directory in the future. '
                                               'You can revoke this in Preferences → AI Fix → Trusted directories.'))
        self.dont_ask_check.set_can_focus(False)
        content_box.append(self.dont_ask_check)

        self.topbox.append(content_box)

        # 加载 CSS：让 prompt TextView 有合适内边距
        self._load_css()

    def _load_css(self):
        '''加载本弹窗专用的 CSS（prompt 区外框与内边距等）。

        GTK4 应用级 CSS provider 范式：每个 provider 仅追加一次，重复 add_provider
        会被 GTK 检测 idempotent（相同 provider+priority 不重复添加）。
        '''
        provider = Gtk.CssProvider()
        css = '''
        textview.ai-fix-prompt-view {
            background-color: @card_bg_color;
            border: 1px solid @borders;
            border-radius: 8px;
        }
        textview.ai-fix-prompt-view text {
            padding: 12px;
        }
        /* hide overshoot/undershoot indicators entirely: they paint on the
           ScrolledWindow edge and are NOT clipped by widget overflow, so they
           would bleed past the rounded frame. The rounded card already signals
           a scrollable area. */
        scrolledwindow.ai-fix-scrolled overshoot,
        scrolledwindow.ai-fix-scrolled undershoot {
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

    def set_header(self, title, subtitle):
        '''更新 HeaderBar 标题与副标题。

        Args:
            title: 形如 'Send to opencode (headed)'
            subtitle: 形如 '/home/user/Documents/project'
        '''
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)

    def set_prompt(self, prompt_text):
        '''填入预编辑的 prompt。会替换 buffer 全部内容。'''
        self.prompt_buffer.set_text(prompt_text or '')

    def get_prompt(self):
        '''读取用户编辑后的 prompt 全文。'''
        start, end = self.prompt_buffer.get_start_iter(), self.prompt_buffer.get_end_iter()
        return self.prompt_buffer.get_text(start, end, True)

    def set_dont_ask_visible(self, visible):
        '''是否显示「不再提示」复选框。

        在已信任目录跳过弹窗的情况下根本不会 present，所以本方法仅用于：
        在某些场景（如临时禁用信任）下隐藏复选框。
        '''
        self.dont_ask_check.set_visible(visible)

    def is_dont_ask_checked(self):
        return self.dont_ask_check.get_active()

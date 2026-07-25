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
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Adw

from setzer.widgets.scrolling_widget.scrolling_widget import ScrollingWidget


class PreviewView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class('preview')

        self.content = ScrollingWidget()
        self.drawing_area = self.content.content

        self.blank_slate = BlankSlateView()

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self.blank_slate, 'blank_slate')
        self.stack.add_named(self.content.view, 'pdf')

        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.overlay.set_child(self.stack)

        # 预览卡片：圆角矩形包裹 PDF 内容
        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card_box.set_hexpand(True)
        self.card_box.set_vexpand(True)
        self.card_box.add_css_class('preview-card')
        self.card_box.set_overflow(Gtk.Overflow.HIDDEN)
        self.card_box.append(self.overlay)

        # 构建失败回退到旧 PDF 时，预览角落显示错误图标（右上角）。
        self.error_badge = Gtk.Image(icon_name='dialog-warning-symbolic')
        self.error_badge.set_halign(Gtk.Align.END)
        self.error_badge.set_valign(Gtk.Align.START)
        self.error_badge.set_margin_top(8)
        self.error_badge.set_margin_end(8)
        self.error_badge.set_can_target(False)
        self.error_badge.set_tooltip_text(_('PDF build failed, showing previous version'))
        self.error_badge.add_css_class('error-badge')
        self.error_badge.set_visible(False)
        self.overlay.add_overlay(self.error_badge)

        # ToastOverlay 包裹内容区，用于构建失败回退时弹出提示。
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)

        # 内容容器：卡片 + 提示语上下排列。
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.set_vexpand(True)
        self.content_box.append(self.card_box)

        # 链接目标提示：位于卡片下方（与编辑器状态栏同款设计）。
        # 注意：revealer 不加入 self 的 widget tree，而是由 PreviewPanelPresenter
        # 在文档切换时挂到 PreviewPanelView 层级（stack 之外），避免被 stack 的
        # overflow:HIDDEN 裁剪到圆角内。
        self.target_label_revealer = Gtk.Revealer()
        self.target_label_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.target_label_revealer.set_transition_duration(150)
        self.target_label_revealer.add_css_class('preview-target-bar')

        self.target_label = Gtk.Label()
        self.target_label.add_css_class('caption')
        self.target_label.add_css_class('dim-label')
        self.target_label.set_halign(Gtk.Align.START)
        self.target_label.set_can_target(False)
        self.target_label.add_css_class('preview-target-label')
        self.target_label_revealer.set_child(self.target_label)
        self.target_label_revealer.set_reveal_child(False)

        self.toast_overlay.set_child(self.content_box)
        self.append(self.toast_overlay)

        self._current_link_target = None
        self._link_target_at_top = False
        self.set_link_target_string('')

    def show_pdf_load_failed(self):
        self.error_badge.set_visible(True)
        toast = Adw.Toast.new(_('PDF build failed, showing previous version'))
        toast.set_timeout(4)
        self.toast_overlay.add_toast(toast)

    def hide_pdf_load_failed(self):
        self.error_badge.set_visible(False)

    def set_layout_data(self, layout_data):
        self.layout_data = layout_data

    def set_link_target_string(self, target_string):
        if target_string != self._current_link_target:
            self._current_link_target = target_string
            has_target = target_string != ''
            self.target_label.set_text(target_string)
            self.target_label_revealer.set_reveal_child(has_target)
            if has_target:
                self.card_box.add_css_class('target-visible')
            else:
                self.card_box.remove_css_class('target-visible')

    def set_link_target_at_top(self, at_top):
        '''链接目标提示已在卡片下方，不再需要上下翻转。保留接口兼容。'''
        pass


class BlankSlateView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)

        self.building_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.building_box.set_halign(Gtk.Align.CENTER)
        self.building_box.set_valign(Gtk.Align.CENTER)
        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(32, 32)
        self.building_label = Gtk.Label(label=_('Building\u2026'))
        self.building_label.add_css_class('heading')
        self.building_box.append(self.spinner)
        self.building_box.append(self.building_label)

        self.status_page = Adw.StatusPage()
        self.status_page.add_css_class('compact')
        self.status_page.set_icon_name('document-properties-symbolic')

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.add_named(self.building_box, 'building')
        self.stack.add_named(self.status_page, 'status')
        self.append(self.stack)

        # 初始化为 None 而非 'never_built'：Gtk.Stack 默认显示第一个添加的
        # 子项（'building'），但初始状态应为 'never_built'。若 _current_state
        # 初始化为 'never_built'，则 show_blank_slate → set_state('never_built')
        # 因状态"未变"而提前 return，stack 永远停在 'building' 页面，导致
        # 新建文档（未编译）的预览区显示 "Building…" 而非 "No preview available"。
        # 初始化为 None 保证首次 set_state('never_built') 真正切换到 'status'。
        self._current_state = None

    def set_state(self, state):
        if state == self._current_state:
            return
        self._current_state = state

        if state == 'building':
            self.stack.set_visible_child_name('building')
            # Adw.Spinner 没有 start()/stop()（那是 Gtk.Spinner 的 API）。
            # Adw.Spinner 是常驻动画 widget，可见时自动旋转。stack 切到
            # 'building' 时 building_box（含 spinner）可见，切到 'status'
            # 时自动隐藏，无需显式 start/stop。
        else:
            self.stack.set_visible_child_name('status')
            if state == 'never_built':
                self.status_page.set_title(_('No preview available'))
                self.status_page.set_description(_('To show a .pdf preview of your document, click the build button in the headerbar.'))
                self.status_page.set_icon_name('document-properties-symbolic')
            elif state == 'build_failed':
                self.status_page.set_title(_('Build failed'))
                self.status_page.set_description(_('Check the build log for errors.'))
                self.status_page.set_icon_name('dialog-error-symbolic')



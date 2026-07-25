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
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk


def WelcomeScreenView():
    '''Welcome screen shown when no document is open.

    Built as a scrollable column so it works on small windows:
      - an Adw.StatusPage (icon + title + friendly hint)
      - a width-limited (Adw.Clamp) region with:
          * quick-action buttons (New LaTeX / New BibTeX / Template Wizard)
          * a recent-documents list (Adw.ActionRow per file)

    Adw.StatusPage is final and cannot be subclassed, so the function
    returns a Gtk.ScrolledWindow. The dynamic widgets the presenter needs
    to drive (recent list, buttons, empty-state label) are attached as
    Python attributes on the returned widget for easy access.

    All _() calls happen inside this function body, never at import time,
    because gettext is installed only after application activation.
    '''
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_hexpand(True)
    scrolled.set_propagate_natural_height(True)

    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    # 四向页面级外边距由 .welcome-content CSS class 统一提供
    # （引用 --setzer-spacing-xl 变量），替代原 4 次 set_margin_*(24) 硬编码。
    # spacing=18 对应 --setzer-spacing-lg（Gtk.Box.spacing 无法用 CSS 设置）。
    column.add_css_class('welcome-content')
    # 在高屏上 ScrolledWindow 的视口比内容高，valign=CENTER 让内容垂直
    # 居中而非贴顶，避免大屏上内容挤在顶部、下方大片空白的失衡感。
    column.set_valign(Gtk.Align.CENTER)
    scrolled.set_child(column)

    # --- top: status page (icon + title + hint) ---
    status = Adw.StatusPage()
    status.set_icon_name('document-latex-symbolic')
    status.set_title(_('Write beautiful LaTeX documents with ease!'))
    status.set_description(_('Start a new document below, pick a template, '
                            'or jump back into one of your recent files.'))
    status.set_vexpand(False)
    column.append(status)

    # --- width-limited content ---
    clamp = Adw.Clamp()
    clamp.set_maximum_size(520)
    clamp.set_tightening_threshold(400)
    column.append(clamp)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

    # quick actions heading
    actions_heading = Gtk.Label(label=_('Create a new document'))
    actions_heading.set_halign(Gtk.Align.START)
    actions_heading.add_css_class('title-4')
    content.append(actions_heading)

    # quick-action buttons
    actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    actions_box.set_homogeneous(True)

    new_latex_button = Gtk.Button()
    new_latex_button.set_icon_name('document-new-symbolic')
    new_latex_button.set_label(_('New LaTeX Document'))
    new_latex_button.set_hexpand(True)

    new_bibtex_button = Gtk.Button()
    new_bibtex_button.set_icon_name('document-new-symbolic')
    new_bibtex_button.set_label(_('New BibTeX File'))
    new_bibtex_button.set_hexpand(True)

    wizard_button = Gtk.Button()
    wizard_button.set_icon_name('preferences-other-symbolic')
    wizard_button.set_label(_('Use a Template…'))
    wizard_button.set_hexpand(True)

    actions_box.append(new_latex_button)
    actions_box.append(new_bibtex_button)
    actions_box.append(wizard_button)
    content.append(actions_box)

    # recent documents heading + clear-all button
    recent_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    recent_header.set_spacing(12)

    recent_heading = Gtk.Label(label=_('Recent documents'))
    recent_heading.set_halign(Gtk.Align.START)
    recent_heading.add_css_class('title-4')
    recent_heading.set_hexpand(True)
    recent_header.append(recent_heading)

    recent_clear_button = Gtk.Button(label=_('Clear All'))
    recent_clear_button.set_valign(Gtk.Align.CENTER)
    recent_clear_button.add_css_class('flat')
    recent_clear_button.set_tooltip_text(_('Remove all documents from the recent list'))
    recent_header.append(recent_clear_button)

    content.append(recent_header)

    recent_listbox = Gtk.ListBox()
    recent_listbox.add_css_class('boxed-list')
    recent_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    content.append(recent_listbox)

    # shown only when there are no recent documents
    empty_state = Adw.StatusPage()
    empty_state.set_icon_name('document-open-recent-symbolic')
    empty_state.set_title(_('No recent documents'))
    empty_state.set_description(_('Documents you open will appear here for quick access.'))
    empty_state.set_vexpand(False)
    empty_state.set_visible(False)
    content.append(empty_state)

    # --- recently closed documents ---
    # 仅在栈非空时显示。用户关闭所有文档后看到 welcome screen，可从这里
    # 一键重开刚关掉的文档，比 Ctrl+Shift+T（只能重开最后一个）更灵活。
    closed_heading = Gtk.Label(label=_('Recently closed'))
    closed_heading.set_halign(Gtk.Align.START)
    closed_heading.add_css_class('title-4')
    closed_heading.set_visible(False)
    content.append(closed_heading)

    closed_listbox = Gtk.ListBox()
    closed_listbox.add_css_class('boxed-list')
    closed_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    closed_listbox.set_visible(False)
    content.append(closed_listbox)

    clamp.set_child(content)

    # expose dynamic widgets to the presenter
    scrolled.recent_listbox = recent_listbox
    scrolled.recent_clear_button = recent_clear_button
    scrolled.empty_state = empty_state
    scrolled.new_latex_button = new_latex_button
    scrolled.new_bibtex_button = new_bibtex_button
    scrolled.wizard_button = wizard_button
    scrolled.closed_heading = closed_heading
    scrolled.closed_listbox = closed_listbox
    # 暴露 actions_box 供 MainWindow 在窄窗口 breakpoint 下切 orientation
    # （HORIZONTAL → VERTICAL），避免三个按钮在 ~360px 窗口下挤压 ellipsize。
    scrolled.actions_box = actions_box

    return scrolled

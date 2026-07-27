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
from gi.repository import Gtk, Adw


class Page(object):

    def load_presets(self, presets):
        pass

    def on_activation(self):
        pass


class PageView(Gtk.Box):

    page_format_names = ['US Letter', 'US Legal', 'A4', 'A5', 'B5']

    # 纸张说明：group 用于按美标/ISO 顺序分组，dims 显示实际尺寸。
    # ComboRow 无法渲染真正的分组分隔符，故按 US -> ISO 顺序平铺，
    # 并在每行显示尺寸，缓解报告 #13 的"混排且无说明"问题。
    page_format_info = {
        'US Letter': ('US', '8.5 x 11 in (215.9 x 279.4 mm)'),
        'US Legal': ('US', '8.5 x 14 in (215.9 x 355.6 mm)'),
        'A4': ('ISO', '210 x 297 mm'),
        'A5': ('ISO', '148 x 210 mm'),
        'B5': ('ISO', '176 x 250 mm'),
    }

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        self.set_margin_start(18)
        self.set_margin_end(18)
        self.set_margin_top(18)
        self.set_margin_bottom(18)
        self.set_spacing(18)

        # Fill the dialog's content area so a ScrolledWindow child can
        # actually scroll instead of just growing past the dialog.
        self.set_vexpand(True)

        self.headerbar_subtitle = ''

    def wrap_content(self, content, maximum_size=500, tightening_threshold=400):
        '''Wrap a child widget in an Adw.Clamp inside a Gtk.ScrolledWindow.

        The clamp keeps rows at a comfortable form width instead of
        stretching to fill very wide windows (below
        ``tightening_threshold`` the clamp starts shrinking so narrow
        windows are not over-padded). The ScrolledWindow lets the page
        scroll vertically when its content (e.g. the long Packages list
        on the General settings page) is taller than the dialog.
        '''
        # 给滚动内容加底部边距：防止列表滚到底时最后一行紧贴容器底边
        # 被裁切（即"没有合适的空白"）。这是 libadwaita PreferencesPage
        # 等标准组件会自带的底部内边距效果，手动拼 Box + PreferencesGroup
        # 时需要补上。
        content.set_margin_bottom(18)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(maximum_size)
        clamp.set_tightening_threshold(tightening_threshold)
        clamp.set_child(content)

        scrolled = Gtk.ScrolledWindow()
        # GTK4: scrollbar policies live on Gtk.PolicyType (the GTK3
        # Gtk.ScrollbarPolicy enum was removed), and they are GObject
        # properties, not setter methods.
        scrolled.hscrollbar_policy = Gtk.PolicyType.NEVER
        scrolled.vscrollbar_policy = Gtk.PolicyType.AUTOMATIC
        scrolled.set_vexpand(True)
        scrolled.set_propagate_natural_height(False)
        scrolled.set_child(clamp)
        return scrolled

    def set_document_settings_page(self):
        '''Create the shared document-settings rows (page format, options,
        font size, margins) as libadwaita rows. Subclasses arrange them into
        PreferencesGroup sections and connect signals via their presenter.'''
        self.headerbar_subtitle = _('Step') + ' 2'

        # Page format -------------------------------------------------------
        self.page_format_combo = Adw.ComboRow()
        self.page_format_combo.set_title(_('Page format'))
        page_format_model = Gtk.StringList()
        for name in self.page_format_names:
            group, dims = self.page_format_info[name]
            # 显示 "US - US Letter (215.9 x 279.4 mm)" 形式：group 提供
            # 美标/ISO 的分组感，dims 提供具体尺寸（报告 #13）。
            page_format_model.append('{} - {} ({})'.format(group, name, dims))
        self.page_format_combo.set_model(page_format_model)

        # Options -----------------------------------------------------------
        self.option_landscape = Adw.SwitchRow()
        self.option_landscape.set_title(_('Landscape format'))

        self.option_twocolumn = Adw.SwitchRow()
        self.option_twocolumn.set_title(_('Two-column layout'))

        # Font size ---------------------------------------------------------
        # A standard Adw.SpinRow, consistent with the margin rows below.
        # 默认 10pt 即 LaTeX 标准默认字号（报告 #14）；book/letter 常用 12pt，
        # 通过 tooltip 给新手提示，避免意外。
        self.font_size_entry = Adw.SpinRow()
        self.font_size_entry.set_title(_('Font size'))
        self.font_size_entry.set_adjustment(Gtk.Adjustment(value=10, lower=6, upper=18, step_increment=1))
        self.font_size_entry.set_digits(0)
        self.font_size_entry.set_tooltip_text(_('LaTeX standard default is 10pt; 12pt is common for book and letter.'))

        # Page margins ------------------------------------------------------
        self.option_default_margins = Adw.SwitchRow()
        self.option_default_margins.set_title(_('Use default margins'))
        self.option_default_margins.set_subtitle(_('On: use LaTeX default margins. Off: set custom margins below.'))

        self.margins_button_left = self._create_margin_row(_('Left'))
        self.margins_button_right = self._create_margin_row(_('Right'))
        self.margins_button_top = self._create_margin_row(_('Top'))
        self.margins_button_bottom = self._create_margin_row(_('Bottom'))

    def _create_margin_row(self, title):
        row = Adw.SpinRow()
        row.set_title(title)
        row.set_adjustment(Gtk.Adjustment(value=3.5, lower=0.0, upper=5.0, step_increment=0.1))
        row.set_digits(1)
        return row

#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, GtkSource, GLib

from setzer.app.service_locator import ServiceLocator
from setzer.settings.document_settings import DocumentSettings


class DocumentPresenter(object):
    ''' Mediator between document and view. '''
    
    def __init__(self, document, document_view):
        self.document = document
        self.view = document_view
        self.settings = ServiceLocator.get_settings()

        self.view.source_view.props.show_line_numbers = False
        def _on_map(widget):
            widget.props.show_line_numbers = False
            gutter = widget.get_gutter(Gtk.TextWindowType.LEFT)
            if gutter is not None:
                gutter.set_visible(False)
        self.view.source_view.connect('map', _on_map)
        self.view.source_view.set_insert_spaces_instead_of_tabs(
            DocumentSettings.get_effective_value(self.document, self.settings, 'spaces_instead_of_tabs'))
        self.view.source_view.set_tab_width(
            DocumentSettings.get_effective_value(self.document, self.settings, 'tab_width'))
        self.view.source_view.set_highlight_current_line(self.settings.get_value('preferences', 'highlight_current_line'))
        self.view.source_view.set_show_right_margin(self.settings.get_value('preferences', 'show_right_margin'))
        self.view.source_view.set_right_margin_position(self.settings.get_value('preferences', 'right_margin_position'))
        self._apply_space_drawer_settings()
        self.document.source_buffer.set_highlight_matching_brackets(self.settings.get_value('preferences', 'highlight_matching_brackets'))
        self.document.source_buffer.set_max_undo_levels(self.settings.get_value('preferences', 'max_undo_levels'))
        if self.settings.get_value('preferences', 'enable_line_wrapping'):
            self.view.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        else:
            self.view.source_view.set_wrap_mode(Gtk.WrapMode.NONE)
        # 行距均分到行上方和下方，使文本在行 slot 中竖直居中。
        # 原先全部放在 below（pixels_below_lines），文本贴着 slot 顶部、
        # 下方留大片空白，视觉上行号和文本都"偏上"未居中。均分后
        # 文本居中，gutter 行号（对齐文本顶部）也随之居中。
        # 副作用：pixels_above_lines > 0 后，get_iter_location().y（文本区
        # 顶部）与 get_line_yrange().y（slot 顶部）不再相同，gutter 的
        # 当前行高亮必须用 get_line_yrange().y 作顶——见 gutter.draw_line_number。
        line_spacing = self.settings.get_value('preferences', 'line_spacing')
        # 行距均分到行上方/下方使文本在 slot 中竖直居中；pixels_inside_wrap
        # 设为完整 line_spacing，使自动换行的续行间距 = 段落间间距（below+above
        # = line_spacing），视觉上行高一致。不设则续行紧贴（默认 0）。
        self.view.source_view.set_pixels_above_lines(line_spacing // 2)
        self.view.source_view.set_pixels_below_lines(line_spacing - line_spacing // 2)
        self.view.source_view.set_pixels_inside_wrap(line_spacing)

        self.settings.connect('settings_changed', self.on_settings_changed)
        self.document.connect('document_settings_changed', self.on_document_settings_changed)

    def _apply_space_drawer_settings(self):
        '''根据 show_line_endings / show_whitespace 设置 SpaceDrawer。

        GtkSourceView 5 通过 SpaceDrawer 控制可见字符：
        - SpaceTypeFlags.SPACE (1): 空格显示为 ·
        - SpaceTypeFlags.TAB (2): Tab 显示为 →
        - SpaceTypeFlags.NEWLINE (4): 行尾显示为 ¶
        SpaceLocationFlags.ALL (7) = LEADING | INSIDE_TEXT | TRAILING。

        注意：必须调用 set_enable_matrix(True) 才会实际绘制。仅设置
        types_for_locations 而 enable_matrix 保持默认 False 时不生效
        （参考 gnome-builder 的 on_draw_spaces_changed 实现）。
        '''
        sd = self.view.source_view.get_space_drawer()
        show_line_endings = self.settings.get_value('preferences', 'show_line_endings')
        show_whitespace = self.settings.get_value('preferences', 'show_whitespace')
        types = 0
        if show_line_endings:
            types |= GtkSource.SpaceTypeFlags.NEWLINE
        if show_whitespace:
            types |= GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
        sd.set_enable_matrix(types != 0)
        sd.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, types)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if (section, item) == ('preferences', 'spaces_instead_of_tabs'):
            self.view.source_view.set_insert_spaces_instead_of_tabs(value)
        if (section, item) == ('preferences', 'tab_width'):
            self.view.source_view.set_tab_width(value)
        if (section, item) == ('preferences', 'highlight_current_line'):
            self.view.source_view.set_highlight_current_line(value)
        if (section, item) == ('preferences', 'show_right_margin'):
            self.view.source_view.set_show_right_margin(value)
        if (section, item) == ('preferences', 'right_margin_position'):
            self.view.source_view.set_right_margin_position(value)
        if (section, item) in (('preferences', 'show_line_endings'), ('preferences', 'show_whitespace')):
            self._apply_space_drawer_settings()
        if (section, item) == ('preferences', 'highlight_matching_brackets'):
            self.document.source_buffer.set_highlight_matching_brackets(value)
        if (section, item) == ('preferences', 'max_undo_levels'):
            self.document.source_buffer.set_max_undo_levels(value)
        if (section, item) == ('preferences', 'enable_line_wrapping'):
            if value == True:
                self.view.source_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            else:
                self.view.source_view.set_wrap_mode(Gtk.WrapMode.NONE)
        if (section, item) == ('preferences', 'line_spacing'):
            self.view.source_view.set_pixels_above_lines(value // 2)
            self.view.source_view.set_pixels_below_lines(value - value // 2)
            self.view.source_view.set_pixels_inside_wrap(value)

    def on_document_settings_changed(self, document, parameter):
        preference_key, value = parameter
        if preference_key == 'spaces_instead_of_tabs':
            effective = DocumentSettings.get_effective_value(
                self.document, self.settings, 'spaces_instead_of_tabs')
            self.view.source_view.set_insert_spaces_instead_of_tabs(effective)
        if preference_key == 'tab_width':
            effective = DocumentSettings.get_effective_value(
                self.document, self.settings, 'tab_width')
            self.view.source_view.set_tab_width(effective)

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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw
from gi.repository import GtkSource
from gi.repository import Pango

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


# 编辑器配色方案预览样例：用 LaTeX 代码（而非 Markdown），因为 Setzer 是
# LaTeX 编辑器。样例刻意覆盖 latex.lang 的多种高亮样式——注释、文档/宏包
# 命令、章节、文本格式命令、verbatim、环境、行内/独立公式、数学命令、
# 上下标、特殊字符——以便配色方案预览能充分展示各语法元素的配色。
LATEX_PREVIEW_TEXT = (
    '% Sample LaTeX document\n'
    '\\documentclass{article}\n'
    '\\usepackage{amsmath}\n'
    '\\section{Introduction}\n'
    'Text \\textbf{bold}, \\emph{italic} and\n'
    '\\verb|code| inline.\n'
    '\\begin{equation}\n'
    '  E = mc^2 + \\alpha_i\n'
    '\\end{equation}\n'
)

# 预览样例表（按语言 ID 索引）；当前仅 LaTeX 一项，预留扩展。
LANG_PREVIEWS = {
    'latex': LATEX_PREVIEW_TEXT,
}


class PageEditor(object):

    def __init__(self, preferences, settings, main_window=None):
        self.view = PageEditorView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = main_window
        self._flowbox_previews = []  # (GtkSource.StyleSchemePreview, scheme_id_or_'')
        # 用户点击网格 tile 时（on_scheme_activated）会写回设置并同步触发
        # settings_changed；为避免该回调重建整个网格造成闪烁，临时屏蔽重建。
        self._suppress_scheme_rebuild = False

    def init(self):
        self.view.reset_button.connect('clicked', self.on_reset_clicked)

        # 编辑器配色方案（复刻 gnome-text-editor Appearance 分组）：
        # 顶部 Markdown 预览 + 方案平铺网格。仅列与当前 Adw 主题同深浅的方案。
        self.view.scheme_flowbox.connect('child-activated', self.on_scheme_activated)
        self.populate_scheme_flowbox()
        self.setup_preview_buffer()
        # 系统主题（或「System」模式下 OS 深浅）变化时重建网格候选与预览配色。
        try:
            Adw.StyleManager.get_default().connect('notify::dark',
                lambda *a: (self.populate_scheme_flowbox(), self.apply_preview_scheme()))
        except Exception:
            pass
        # 外部写回 editor_style_scheme（如 Appearance 页「Reset to Defaults」）
        # 时重建网格并刷新预览配色，保持 Editor 页网格与实际设置同步。
        self.settings.connect('settings_changed', self.on_settings_changed)

        self.view.option_spaces_instead_of_tabs.set_active(self.settings.get_value('preferences', 'spaces_instead_of_tabs'))
        self.view.option_spaces_instead_of_tabs.connect('notify::active', self.on_switch_toggled, 'spaces_instead_of_tabs')

        self.view.tab_width_spinbutton.set_property('value', self.settings.get_value('preferences', 'tab_width'))
        self.view.tab_width_spinbutton.connect('notify::value', self.preferences.spin_button_changed, 'tab_width')

        self.view.max_undo_levels_row.set_property('value', self.settings.get_value('preferences', 'max_undo_levels'))
        self.view.max_undo_levels_row.connect('notify::value', self.preferences.spin_button_changed, 'max_undo_levels')

        self.view.option_show_line_numbers.set_active(self.settings.get_value('preferences', 'show_line_numbers'))
        self.view.option_show_line_numbers.connect('notify::active', self.on_switch_toggled, 'show_line_numbers')

        self.view.option_show_right_margin.set_active(self.settings.get_value('preferences', 'show_right_margin'))
        self.view.option_show_right_margin.connect('notify::active', self.on_switch_toggled, 'show_right_margin')

        self.view.right_margin_position_row.set_property('value', self.settings.get_value('preferences', 'right_margin_position'))
        self.view.right_margin_position_row.connect('notify::value', self.preferences.spin_button_changed, 'right_margin_position')

        self.view.option_show_shortcuts_bar.set_active(self.settings.get_value('preferences', 'show_shortcuts_bar'))
        self.view.option_show_shortcuts_bar.connect('notify::active', self.on_switch_toggled, 'show_shortcuts_bar')

        self.view.option_line_wrapping.set_active(self.settings.get_value('preferences', 'enable_line_wrapping'))
        self.view.option_line_wrapping.connect('notify::active', self.on_switch_toggled, 'enable_line_wrapping')

        self.view.option_code_folding.set_active(self.settings.get_value('preferences', 'enable_code_folding'))
        self.view.option_code_folding.connect('notify::active', self.on_switch_toggled, 'enable_code_folding')

        self.view.option_sticky_scroll.set_active(self.settings.get_value('preferences', 'enable_sticky_scroll'))
        self.view.option_sticky_scroll.connect('notify::active', self.on_switch_toggled, 'enable_sticky_scroll')

        self.view.option_highlight_current_line.set_active(self.settings.get_value('preferences', 'highlight_current_line'))
        self.view.option_highlight_current_line.connect('notify::active', self.on_switch_toggled, 'highlight_current_line')

        self.view.option_highlight_matching_brackets.set_active(self.settings.get_value('preferences', 'highlight_matching_brackets'))
        self.view.option_highlight_matching_brackets.connect('notify::active', self.on_switch_toggled, 'highlight_matching_brackets')

        self.view.option_highlight_matching_begin_end.set_active(self.settings.get_value('preferences', 'highlight_matching_begin_end'))
        self.view.option_highlight_matching_begin_end.connect('notify::active', self.on_switch_toggled, 'highlight_matching_begin_end')

        self.view.option_show_line_endings.set_active(self.settings.get_value('preferences', 'show_line_endings'))
        self.view.option_show_line_endings.connect('notify::active', self.on_switch_toggled, 'show_line_endings')

        self.view.option_show_whitespace.set_active(self.settings.get_value('preferences', 'show_whitespace'))
        self.view.option_show_whitespace.connect('notify::active', self.on_switch_toggled, 'show_whitespace')

        # 同步到预览 SourceView（初始状态）。
        self._apply_preview_space_drawer()

        self.view.option_auto_save_enabled.set_active(self.settings.get_value('preferences', 'auto_save_enabled'))
        self.view.option_auto_save_enabled.connect('notify::active', self.on_switch_toggled, 'auto_save_enabled')

        self.view.option_auto_reload_on_external_change.set_active(
            self.settings.get_value('preferences', 'auto_reload_on_external_change'))
        self.view.option_auto_reload_on_external_change.connect(
            'notify::active', self.on_switch_toggled, 'auto_reload_on_external_change')

        self.view.auto_save_delay_row.set_property('value', self.settings.get_value('preferences', 'auto_save_delay'))
        self.view.auto_save_delay_row.connect('notify::value', self.preferences.spin_button_changed, 'auto_save_delay')

        # Default encoding / line ending
        self.view.encoding_values = ['utf-8', 'iso-8859-1', 'windows-1252', 'utf-16']
        self.view.line_ending_values = ['\n', '\r\n', '\r']
        current_encoding = self.settings.get_value('preferences', 'default_encoding')
        try:
            encoding_index = self.view.encoding_values.index(current_encoding)
        except ValueError:
            encoding_index = 0
        self.view.option_default_encoding.set_selected(encoding_index)
        self.view.option_default_encoding.connect('notify::selected',
            lambda combo, _ps: self.settings.set_value('preferences', 'default_encoding',
                self.view.encoding_values[combo.get_selected()]))
        current_line_ending = self.settings.get_value('preferences', 'default_line_ending')
        try:
            line_ending_index = self.view.line_ending_values.index(current_line_ending)
        except ValueError:
            line_ending_index = 0
        self.view.option_default_line_ending.set_selected(line_ending_index)
        self.view.option_default_line_ending.connect('notify::selected',
            lambda combo, _ps: self.settings.set_value('preferences', 'default_line_ending',
                self.view.line_ending_values[combo.get_selected()]))

        # ---- 字体（从 Appearance 页移入，配合上方预览 SourceView 实时预览）----
        self.view.option_use_system_font.set_active(
            self.settings.get_value('preferences', 'use_system_font'))
        self.view.font_chooser_button.set_font_desc(
            Pango.FontDescription.from_string(self.settings.get_value('preferences', 'font_string')))
        self.view.font_chooser_row.set_sensitive(not self.view.option_use_system_font.get_active())
        self.view.option_use_system_font.connect('notify::active', self.on_use_system_font_toggled)
        self.view.font_chooser_button.connect('notify::font-desc', self.on_font_set)
        self.view.line_spacing_spin.set_value(
            self.settings.get_value('preferences', 'line_spacing'))
        self.view.line_spacing_spin.connect('notify::value', self.on_line_spacing_changed)
        # 行距需手动作用于预览 SourceView；字体本身随全局 CSS 实时生效。
        # 与 document_presenter 保持一致：均分到上下 + pixels_inside_wrap。
        ls = self.settings.get_value('preferences', 'line_spacing')
        self.view.preview_source_view.set_pixels_above_lines(ls // 2)
        self.view.preview_source_view.set_pixels_below_lines(ls - ls // 2)
        self.view.preview_source_view.set_pixels_inside_wrap(ls)

    def _setup_combo_row(self, combo_row, pref_key, values):
        idx = values.index(self.settings.get_value('preferences', pref_key))
        combo_row.set_selected(idx)
        combo_row.connect('notify::selected', self.on_combo_row_changed, pref_key, values)

    def on_combo_row_changed(self, combo_row, pspec, pref_key, values):
        value = values[combo_row.get_selected()]
        self.settings.set_value('preferences', pref_key, value)


    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())
        # 实时同步到预览 SourceView，让用户在偏好设置界面就能看到效果。
        if preference_name in ('show_line_endings', 'show_whitespace'):
            self._apply_preview_space_drawer()

    def _apply_preview_space_drawer(self):
        '''将 show_line_endings / show_whitespace 设置同步到预览 SourceView。'''
        sd = self.view.preview_source_view.get_space_drawer()
        show_le = self.settings.get_value('preferences', 'show_line_endings')
        show_ws = self.settings.get_value('preferences', 'show_whitespace')
        types = 0
        if show_le:
            types |= GtkSource.SpaceTypeFlags.NEWLINE
        if show_ws:
            types |= GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
        sd.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, types)

    # ---- 字体（实时预览于 Appearance 组的预览 SourceView）----
    def on_use_system_font_toggled(self, switch, pspec):
        self.view.font_chooser_row.set_sensitive(not switch.get_active())
        self.settings.set_value('preferences', 'use_system_font', switch.get_active())

    def on_font_set(self, button, pspec=None):
        font_desc = button.get_font_desc()
        size = font_desc.get_size()
        clamped = False
        if size < 6 * Pango.SCALE:
            font_desc.set_size(6 * Pango.SCALE)
            button.set_font_desc(font_desc)
            clamped = 'min'
        elif size > 24 * Pango.SCALE:
            font_desc.set_size(24 * Pango.SCALE)
            button.set_font_desc(font_desc)
            clamped = 'max'
        self.settings.set_value('preferences', 'font_string', font_desc.to_string())
        if clamped:
            if clamped == 'min':
                msg = _('Font size is too small; clamped to 6pt minimum.')
            else:
                msg = _('Font size is too large; clamped to 24pt maximum.')
            self._show_toast(msg)

    def on_line_spacing_changed(self, spin, pspec=None):
        value = int(spin.get_value())
        self.settings.set_value('preferences', 'line_spacing', value)
        # 实时刷新预览 SourceView 行距（字体随全局 CSS 自动生效，行距需手动应用）。
        self.view.preview_source_view.set_pixels_above_lines(value // 2)
        self.view.preview_source_view.set_pixels_below_lines(value - value // 2)
        self.view.preview_source_view.set_pixels_inside_wrap(value)

    def _show_toast(self, message):
        main_window = self.main_window or ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(4)
            main_window.toast_overlay.add_toast(toast)

    # ---- editor color scheme grid (复刻 gnome-text-editor) ----
    def _current_dark(self):
        '''当前 Adw 是否为深色主题，用于过滤方案网格。'''
        try:
            return Adw.StyleManager.get_default().get_dark()
        except AttributeError:
            return False

    def populate_scheme_flowbox(self):
        '''重建编辑器配色方案网格（列出全部方案，不过滤深浅）。

        首项为「Follow system theme」合成 tile（scheme_id=''），其余为
        StyleSchemeManager 中的所有真实方案——**不**按当前 Adw 深浅主题过滤，
        以便用户可在深色应用下选用浅色编辑器配色（或反之），恢复旧版 Setzer
        「任意组合 app 主题与编辑器配色」的能力。

        说明：gnome-text-editor 参考项目会按当前主题只列同深浅方案；但 Setzer
        用户习惯能独立选择，故这里列出全部。重建前清空旧 children。
        '''
        while True:
            child = self.view.scheme_flowbox.get_first_child()
            if child is None:
                break
            self.view.scheme_flowbox.remove(child)
        self._flowbox_previews = []

        current_scheme = self.settings.get_value('preferences', 'editor_style_scheme')
        dark = self._current_dark()

        # 1) Follow system theme —— 用当前系统默认方案渲染预览 tile。
        system_scheme_id = 'default-dark' if dark else 'default'
        system_scheme = ServiceLocator.get_source_style_scheme_manager().get_scheme(system_scheme_id)
        if system_scheme is not None:
            preview = GtkSource.StyleSchemePreview.new(system_scheme)
            self._add_flowbox_child(preview, '')

        # 2) 全部真实方案（不过滤深浅，允许用户任意组合 app 与编辑器配色）。
        scheme_manager = ServiceLocator.get_source_style_scheme_manager()
        for scheme_id in scheme_manager.get_scheme_ids():
            if scheme_id in ('default', 'default-dark'):
                continue
            scheme = scheme_manager.get_scheme(scheme_id)
            if scheme is None:
                continue
            preview = GtkSource.StyleSchemePreview.new(scheme)
            self._add_flowbox_child(preview, scheme_id)

        self.update_scheme_selection(current_scheme)

    def _add_flowbox_child(self, preview, scheme_id):
        preview.set_selected(False)
        self.view.scheme_flowbox.insert(preview, -1)
        self._flowbox_previews.append((preview, scheme_id))

    def update_scheme_selection(self, current_scheme):
        '''高亮与 current_scheme 匹配的 tile（'' 匹配 Follow system 项）。
        仅对状态实际变化的 tile 调用 set_selected，避免不必要的重绘闪烁。'''
        for preview, scheme_id in self._flowbox_previews:
            selected = (scheme_id == current_scheme)
            if preview.get_selected() != selected:
                preview.set_selected(selected)

    def on_scheme_activated(self, flowbox, child):
        # child 是 Gtk.FlowBoxChild，其单子为 StyleSchemePreview。
        preview = child.get_child()
        scheme_id = ''
        for p, sid in self._flowbox_previews:
            if p is preview:
                scheme_id = sid
                break
        # set_style_scheme_name 会同步触发 settings_changed -> on_settings_changed，
        # 该回调默认会重建整个网格（remove+insert 所有 tile），造成切换闪烁。
        # 激活场景下无需重建，仅更新选中态与预览配色即可，故临时屏蔽重建。
        self._suppress_scheme_rebuild = True
        try:
            # 写回设置（清空缓存 + set_value），驱动已打开文档重应用配色。
            ServiceLocator.set_style_scheme_name(scheme_id)
        finally:
            self._suppress_scheme_rebuild = False
        self.update_scheme_selection(scheme_id)
        self.apply_preview_scheme()

    def setup_preview_buffer(self):
        '''初始化 LaTeX 预览缓冲区：语法高亮 + 文本 + 选中第 3 行（同参考）。'''
        buffer_ = GtkSource.Buffer()
        lang_manager = GtkSource.LanguageManager()
        lang = lang_manager.get_language('latex')
        buffer_.set_language(lang)
        buffer_.set_highlight_syntax(True)
        text = LANG_PREVIEWS.get('latex', LATEX_PREVIEW_TEXT)
        buffer_.set_text(text)
        self.view.preview_source_view.set_buffer(buffer_)
        # get_iter_at_line 的 PyGObject 绑定返回 (found, iter) 元组，取 [1]。
        if buffer_.get_line_count() >= 3:
            start = buffer_.get_iter_at_line(2)[1]
            end = buffer_.get_iter_at_line(3)[1]
            buffer_.select_range(start, end)
        self.apply_preview_scheme()

    def apply_preview_scheme(self):
        '''把当前生效的编辑器配色应用到预览缓冲区。'''
        scheme = ServiceLocator.get_style_scheme()
        if scheme is not None:
            self.view.preview_source_view.get_buffer().set_style_scheme(scheme)

    def on_settings_changed(self, settings, parameter):
        # settings_changed 参数为 (section, item, value) 元组。
        # 仅当编辑器配色方案被外部写回时重建网格（如 Appearance 页重置）。
        # 忽略其它设置变更，避免无谓重建。
        # 用户点击网格 tile 时已在 on_scheme_activated 中临时屏蔽重建，
        # 此处仅在外部写回（如 Appearance 页「Reset to Defaults」）时重建。
        if self._suppress_scheme_rebuild:
            return
        section, item, value = parameter
        if section == 'preferences' and item == 'editor_style_scheme':
            self.populate_scheme_flowbox()
            self.apply_preview_scheme()

    def on_reset_clicked(self, button):
        defaults = self.settings.defaults['preferences']
        # editor_style_scheme 默认 '' = 跟随系统主题；写回后重建网格并刷新预览。
        ServiceLocator.set_style_scheme_name('')
        self.populate_scheme_flowbox()
        self.apply_preview_scheme()
        self.view.option_spaces_instead_of_tabs.set_active(defaults['spaces_instead_of_tabs'])
        self.view.tab_width_spinbutton.set_property('value', defaults['tab_width'])
        self.view.max_undo_levels_row.set_property('value', defaults['max_undo_levels'])
        self.view.option_show_line_numbers.set_active(defaults['show_line_numbers'])
        self.view.option_show_right_margin.set_active(defaults['show_right_margin'])
        self.view.right_margin_position_row.set_value(defaults['right_margin_position'])
        self.view.option_show_shortcuts_bar.set_active(defaults['show_shortcuts_bar'])
        self.view.option_line_wrapping.set_active(defaults['enable_line_wrapping'])
        self.view.option_code_folding.set_active(defaults['enable_code_folding'])
        self.view.option_sticky_scroll.set_active(defaults['enable_sticky_scroll'])
        self.view.option_highlight_current_line.set_active(defaults['highlight_current_line'])
        self.view.option_highlight_matching_brackets.set_active(defaults['highlight_matching_brackets'])
        self.view.option_highlight_matching_begin_end.set_active(defaults['highlight_matching_begin_end'])
        self.view.option_show_line_endings.set_active(defaults['show_line_endings'])
        self.view.option_show_whitespace.set_active(defaults['show_whitespace'])
        self._apply_preview_space_drawer()
        self.view.option_auto_save_enabled.set_active(defaults['auto_save_enabled'])
        self.view.auto_save_delay_row.set_property('value', defaults['auto_save_delay'])
        self.view.option_auto_reload_on_external_change.set_active(
            defaults['auto_reload_on_external_change'])
        self.view.option_default_encoding.set_selected(
            self.view.encoding_values.index(defaults['default_encoding']))
        self.view.option_default_line_ending.set_selected(
            self.view.line_ending_values.index(defaults['default_line_ending']))
        # 字体（从 Appearance 页移入，随此处一并重置）。
        self.view.option_use_system_font.set_active(defaults['use_system_font'])
        self.view.font_chooser_button.set_font_desc(
            Pango.FontDescription.from_string(defaults['font_string']))
        self.view.font_chooser_row.set_sensitive(not defaults['use_system_font'])
        self.view.line_spacing_spin.set_value(defaults['line_spacing'])
        ls = defaults['line_spacing']
        self.view.preview_source_view.set_pixels_above_lines(ls // 2)
        self.view.preview_source_view.set_pixels_below_lines(ls - ls // 2)
        self.view.preview_source_view.set_pixels_inside_wrap(ls)
        # 重置编辑器字号缩放倍率为默认值 1.0
        self.settings.set_value('preferences', 'editor_font_zoom_level', 1.0)


class PageEditorView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Editor'))
        self.set_icon_name('accessories-text-editor-symbolic')

        # Appearance 分组（复刻 gnome-text-editor）：顶部 Markdown 预览 +
        # 下方 Gtk.FlowBox 的编辑器配色方案平铺网格。置于 Editor 页最前，
        # 与参考项目一致——配色选择属于编辑器外观设置。
        group_appearance = Adw.PreferencesGroup()
        group_appearance.set_title(_('Appearance'))
        self.add(group_appearance)

        # Markdown 预览：只读、等宽、显示行号、card 类（参考项目 preview+card），
        # 并显式设置与参考一致的边距（上下 8、左右 12，底部额外 24 与网格分隔）。
        self.preview_source_view = GtkSource.View()
        self.preview_source_view.set_editable(False)
        self.preview_source_view.set_cursor_visible(False)
        self.preview_source_view.set_monospace(True)
        self.preview_source_view.set_show_line_numbers(True)
        self.preview_source_view.set_top_margin(8)
        self.preview_source_view.set_bottom_margin(8)
        self.preview_source_view.set_left_margin(12)
        self.preview_source_view.set_right_margin(12)
        self.preview_source_view.set_margin_bottom(24)
        self.preview_source_view.add_css_class('card')
        self.preview_source_view.add_css_class('scheme-preview')
        self.preview_source_view.add_css_class('preview')
        self.preview_source_view.set_size_request(-1, 140)
        group_appearance.add(self.preview_source_view)

        # 方案平铺网格：每行最多 4 个，不可框选（点击直接应用）。
        self.scheme_flowbox = Gtk.FlowBox()
        self.scheme_flowbox.add_css_class('scheme-flowbox')
        self.scheme_flowbox.set_hexpand(True)
        self.scheme_flowbox.set_column_spacing(12)
        self.scheme_flowbox.set_row_spacing(12)
        self.scheme_flowbox.set_max_children_per_line(4)
        self.scheme_flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        group_appearance.add(self.scheme_flowbox)

        # 字体：从 Appearance 页移入 Editor 页，配合上方预览 SourceView 提供
        # 实时预览（字体随全局 CSS 生效，行距在 on_line_spacing_changed 手动作用）。
        group_font = Adw.PreferencesGroup()
        group_font.set_title(_('Font'))
        self.add(group_font)

        font_string = FontManager.get_system_font() or 'Monospace'
        self.option_use_system_font = Adw.SwitchRow()
        self.option_use_system_font.set_title(_('Use the system fixed width font'))
        self.option_use_system_font.set_subtitle(font_string)
        self.option_use_system_font.set_tooltip_text(_(
            'Use the system fixed-width font for editing. '
            'When enabled, the font set below is overridden.'))
        group_font.add(self.option_use_system_font)

        self.font_chooser_button = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
        self.font_chooser_button.set_valign(Gtk.Align.CENTER)
        self.font_chooser_row = Adw.ActionRow()
        self.font_chooser_row.set_title(_('Set Editor Font'))
        self.font_chooser_row.add_suffix(self.font_chooser_button)
        self.font_chooser_row.set_tooltip_text(_(
            'Choose a custom font for the LaTeX editor. '
            'Only active when "Use the system fixed width font" is off.'))
        group_font.add(self.font_chooser_row)

        self.line_spacing_spin = Adw.SpinRow()
        self.line_spacing_spin.set_title(_('Line Spacing'))
        self.line_spacing_spin.set_subtitle(_('Extra vertical space between lines in pixels.'))
        self.line_spacing_spin.set_tooltip_text(_(
            'Extra vertical space between lines, in pixels. '
            'Affects readability and line density in the editor.'))
        adjustment_line_spacing = Gtk.Adjustment(value=0, lower=0, upper=12, step_increment=1)
        self.line_spacing_spin.set_adjustment(adjustment_line_spacing)
        group_font.add(self.line_spacing_spin)

        group_tab_stops = Adw.PreferencesGroup()
        group_tab_stops.set_title(_('Tab Stops'))
        self.add(group_tab_stops)

        self.option_spaces_instead_of_tabs = Adw.SwitchRow()
        self.option_spaces_instead_of_tabs.set_title(_('Insert spaces instead of tabs'))
        self.option_spaces_instead_of_tabs.set_subtitle(_('Use spaces when pressing Tab, instead of a tab character.'))
        self.option_spaces_instead_of_tabs.set_tooltip_text(_(
            'When enabled, pressing Tab inserts spaces instead of a tab character.'))
        group_tab_stops.add(self.option_spaces_instead_of_tabs)

        self.tab_width_row = Adw.SpinRow()
        self.tab_width_row.set_title(_('Tab Width'))
        adjustment = Gtk.Adjustment(value=1, lower=1, upper=8, step_increment=1)
        self.tab_width_row.set_adjustment(adjustment)
        self.tab_width_row.set_tooltip_text(_(
            'Number of spaces a tab character occupies '
            'when "Insert spaces instead of tabs" is enabled.'))
        group_tab_stops.add(self.tab_width_row)
        self.tab_width_spinbutton = self.tab_width_row

        # 撤销栈深度：限制 GtkSource.Buffer 的 max-undo-levels，避免超大文档
        # 撤销栈无界增长占用过多内存。0 = 不限制（沿用旧版行为）。
        group_undo = Adw.PreferencesGroup()
        group_undo.set_title(_('Undo'))
        self.add(group_undo)

        self.max_undo_levels_row = Adw.SpinRow()
        self.max_undo_levels_row.set_title(_('Maximum undo depth'))
        self.max_undo_levels_row.set_subtitle(_('Maximum number of undo steps. 0 = unlimited.'))
        self.max_undo_levels_row.set_tooltip_text(_(
            'Limit the number of undoable actions kept in memory. '
            'Lower values save memory for very large documents. '
            '0 keeps an unlimited history.'))
        adjustment_undo = Gtk.Adjustment(value=200, lower=0, upper=10000, step_increment=10)
        self.max_undo_levels_row.set_adjustment(adjustment_undo)
        group_undo.add(self.max_undo_levels_row)

        group_line_numbers = Adw.PreferencesGroup()
        group_line_numbers.set_title(_('Line Numbers'))
        self.add(group_line_numbers)

        self.option_show_line_numbers = Adw.SwitchRow()
        self.option_show_line_numbers.set_title(_('Show line numbers'))
        self.option_show_line_numbers.set_subtitle(_('Display line numbers in the editor gutter.'))
        self.option_show_line_numbers.set_tooltip_text(_(
            'Display line numbers in the left gutter of the editor.'))
        group_line_numbers.add(self.option_show_line_numbers)

        group_right_margin = Adw.PreferencesGroup()
        group_right_margin.set_title(_('Right Margin'))
        self.add(group_right_margin)

        self.option_show_right_margin = Adw.SwitchRow()
        self.option_show_right_margin.set_title(_('Show right margin guide'))
        self.option_show_right_margin.set_subtitle(_('Display a vertical line at a fixed column.'))
        self.option_show_right_margin.set_tooltip_text(_(
            'Display a vertical line at a fixed column position '
            'to help keep lines within a recommended width.'))
        group_right_margin.add(self.option_show_right_margin)

        self.right_margin_position_row = Adw.SpinRow()
        self.right_margin_position_row.set_title(_('Right margin column'))
        self.right_margin_position_row.set_subtitle(_('Column position for the right margin guide.'))
        self.right_margin_position_row.set_tooltip_text(_(
            'Column position for the right margin guide vertical line. '
            'LaTeX documents are typically kept within 80 columns.'))
        adjustment_right_margin = Gtk.Adjustment(value=80, lower=40, upper=200, step_increment=1)
        self.right_margin_position_row.set_adjustment(adjustment_right_margin)
        group_right_margin.add(self.right_margin_position_row)

        group_shortcuts_bar = Adw.PreferencesGroup()
        group_shortcuts_bar.set_title(_('Shortcuts Bar'))
        self.add(group_shortcuts_bar)

        self.option_show_shortcuts_bar = Adw.SwitchRow()
        self.option_show_shortcuts_bar.set_title(_('Show shortcuts bar'))
        self.option_show_shortcuts_bar.set_subtitle(_('Display the shortcuts bar above the editor.'))
        self.option_show_shortcuts_bar.set_tooltip_text(_(
            'Display a bar of keyboard shortcuts above the editor.'))
        group_shortcuts_bar.add(self.option_show_shortcuts_bar)

        group_line_wrapping = Adw.PreferencesGroup()
        group_line_wrapping.set_title(_('Line Wrapping'))
        self.add(group_line_wrapping)

        self.option_line_wrapping = Adw.SwitchRow()
        self.option_line_wrapping.set_title(_('Enable line wrapping'))
        self.option_line_wrapping.set_subtitle(_('Wrap long lines instead of scrolling horizontally.'))
        self.option_line_wrapping.set_tooltip_text(_(
            'Wrap long lines to fit within the editor window '
            'instead of scrolling horizontally.'))
        group_line_wrapping.add(self.option_line_wrapping)

        group_code_folding = Adw.PreferencesGroup()
        group_code_folding.set_title(_('Code Folding'))
        self.add(group_code_folding)

        self.option_code_folding = Adw.SwitchRow()
        self.option_code_folding.set_title(_('Enable code folding'))
        self.option_code_folding.set_subtitle(_('Allow collapsing blocks of code such as environments.'))
        self.option_code_folding.set_tooltip_text(_(
            'Allow collapsing and expanding code blocks '
            'such as LaTeX environments, sections, and functions.'))
        group_code_folding.add(self.option_code_folding)

        group_sticky_scroll = Adw.PreferencesGroup()
        group_sticky_scroll.set_title(_('Sticky Scroll'))
        self.add(group_sticky_scroll)

        self.option_sticky_scroll = Adw.SwitchRow()
        self.option_sticky_scroll.set_title(_('Enable sticky scroll for sections'))
        self.option_sticky_scroll.set_subtitle(_('Show the current section header at the top of the editor while scrolling.'))
        self.option_sticky_scroll.set_tooltip_text(_(
            'Displays a sticky header at the top of the editor showing the '
            'current section hierarchy (chapter, section, subsection, etc.) '
            'as you scroll through long documents.'))
        group_sticky_scroll.add(self.option_sticky_scroll)

        group_highlighting = Adw.PreferencesGroup()
        group_highlighting.set_title(_('Highlighting'))
        self.add(group_highlighting)

        self.option_highlight_current_line = Adw.SwitchRow()
        self.option_highlight_current_line.set_title(_('Highlight current line'))
        self.option_highlight_current_line.set_subtitle(_('Visually emphasize the line containing the cursor.'))
        self.option_highlight_current_line.set_tooltip_text(_(
            'Visually highlight the line where the cursor is located.'))
        group_highlighting.add(self.option_highlight_current_line)

        self.option_highlight_matching_brackets = Adw.SwitchRow()
        self.option_highlight_matching_brackets.set_title(_('Highlight matching brackets'))
        self.option_highlight_matching_brackets.set_subtitle(_('Highlight the bracket matching the one next to the cursor.'))
        self.option_highlight_matching_brackets.set_tooltip_text(_(
            'Highlight the bracket that matches the one next to the cursor.'))
        group_highlighting.add(self.option_highlight_matching_brackets)

        self.option_highlight_matching_begin_end = Adw.SwitchRow()
        self.option_highlight_matching_begin_end.set_title(_('Highlight matching \\begin/\\end'))
        self.option_highlight_matching_begin_end.set_subtitle(_('Highlight the environment matching the \\begin or \\end command next to the cursor.'))
        self.option_highlight_matching_begin_end.set_tooltip_text(_(
            'Highlight the \\begin{...}/\\end{...} pair that matches the one next to the cursor.'))
        group_highlighting.add(self.option_highlight_matching_begin_end)

        # 可见字符：显示行尾 ¶ 和空白（空格 · Tab →），调试缩进问题时有用。
        group_visible_chars = Adw.PreferencesGroup()
        group_visible_chars.set_title(_('Visible Characters'))
        self.add(group_visible_chars)

        self.option_show_line_endings = Adw.SwitchRow()
        self.option_show_line_endings.set_title(_('Show line endings'))
        self.option_show_line_endings.set_subtitle(_('Display line ending characters (¶) at the end of each line.'))
        self.option_show_line_endings.set_tooltip_text(_(
            'Display a paragraph symbol (¶) at the end of each line '
            'to help identify trailing whitespace and line ending issues.'))
        group_visible_chars.add(self.option_show_line_endings)

        self.option_show_whitespace = Adw.SwitchRow()
        self.option_show_whitespace.set_title(_('Show whitespace'))
        self.option_show_whitespace.set_subtitle(_('Display whitespace characters (· for spaces, → for tabs).'))
        self.option_show_whitespace.set_tooltip_text(_(
            'Display symbols for spaces (·) and tabs (→) '
            'to help debug indentation and trailing whitespace issues.'))
        group_visible_chars.add(self.option_show_whitespace)

        # 自动保存（崩溃恢复）：把缓冲区内容定时写入临时文件，
        # 应用崩溃后下次启动可恢复未保存的编辑。
        group_auto_save = Adw.PreferencesGroup()
        group_auto_save.set_title(_('Auto Save'))
        self.add(group_auto_save)

        self.option_auto_save_enabled = Adw.SwitchRow()
        self.option_auto_save_enabled.set_title(_('Enable auto save (crash recovery)'))
        self.option_auto_save_enabled.set_subtitle(_('Periodically save buffer to a temp file for crash recovery'))
        self.option_auto_save_enabled.set_tooltip_text(_(
            'Periodically save the buffer to a temporary file '
            'for crash recovery. Setzer can restore unsaved edits '
            'after an unexpected application exit.'))
        group_auto_save.add(self.option_auto_save_enabled)

        self.auto_save_delay_row = Adw.SpinRow()
        self.auto_save_delay_row.set_title(_('Auto save interval (seconds)'))
        adjustment_auto_save = Gtk.Adjustment(value=60, lower=10, upper=600, step_increment=5)
        self.auto_save_delay_row.set_adjustment(adjustment_auto_save)
        self.auto_save_delay_row.set_tooltip_text(_(
            'Time interval in seconds between automatic saves '
            'when auto save is enabled.'))
        group_auto_save.add(self.auto_save_delay_row)

        # 外部变更：自动静默重载磁盘上被其他程序修改的文件。
        group_external_changes = Adw.PreferencesGroup()
        group_external_changes.set_title(_('External Changes'))
        self.add(group_external_changes)

        self.option_auto_reload_on_external_change = Adw.SwitchRow()
        self.option_auto_reload_on_external_change.set_title(_('Automatically reload changed files'))
        self.option_auto_reload_on_external_change.set_subtitle(
            _('Reload files modified by external applications without prompting'))
        self.option_auto_reload_on_external_change.set_tooltip_text(_(
            'Automatically reload files modified by external applications '
            'without showing a prompt. Unsaved changes in the editor will be lost.'))
        group_external_changes.add(self.option_auto_reload_on_external_change)

        # 新建文档默认设置：编码与行尾格式
        group_new_doc = Adw.PreferencesGroup()
        group_new_doc.set_title(_('New Document'))
        self.add(group_new_doc)

        self.option_default_encoding = Adw.ComboRow()
        self.option_default_encoding.set_title(_('Default Encoding'))
        self.option_default_encoding.set_subtitle(
            _('Encoding used when creating new documents'))
        self.option_default_encoding.set_tooltip_text(_(
            'Default character encoding for new documents. '
            'UTF-8 is the recommended choice for cross-platform collaboration.'))
        encoding_list = Gtk.StringList.new([
            _('UTF-8'),
            _('ISO-8859-1'),
            _('Windows-1252'),
            _('UTF-16'),
        ])
        self.option_default_encoding.set_model(encoding_list)
        group_new_doc.add(self.option_default_encoding)

        self.option_default_line_ending = Adw.ComboRow()
        self.option_default_line_ending.set_title(_('Default Line Ending'))
        self.option_default_line_ending.set_subtitle(
            _('Line ending format for new documents'))
        self.option_default_line_ending.set_tooltip_text(_(
            'Default line ending format for new documents. '
            'LF is Unix/macOS style; CRLF is Windows style; CR is old macOS style.'))
        line_ending_list = Gtk.StringList.new([
            _('LF (Unix)'),
            _('CRLF (Windows)'),
            _('CR (Old macOS)'),
        ])
        self.option_default_line_ending.set_model(line_ending_list)
        group_new_doc.add(self.option_default_line_ending)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

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
from setzer.document.spellchecking.spellchecking import SpellChecker


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

        # Experimental Features（多光标编辑）开关
        self.view.master_switch.set_active(
            self.settings.get_value('preferences', 'experimental_features'))
        self.view.master_switch.connect('notify::active', self._on_exp_master_toggled)

        self.view.switch_multicursor.set_active(
            self.settings.get_value('preferences', 'experimental_multicursor'))
        self.view.switch_multicursor.connect('notify::active', self._on_exp_toggled, 'experimental_multicursor')

        self.view.switch_alt_click.set_active(
            self.settings.get_value('preferences', 'experimental_alt_click'))
        self.view.switch_alt_click.connect('notify::active', self._on_exp_toggled, 'experimental_alt_click')

        self.view.switch_alt_drag.set_active(
            self.settings.get_value('preferences', 'experimental_alt_drag'))
        self.view.switch_alt_drag.connect('notify::active', self._on_exp_toggled, 'experimental_alt_drag')

        self.view.switch_select_next.set_active(
            self.settings.get_value('preferences', 'experimental_select_next'))
        self.view.switch_select_next.connect('notify::active', self._on_exp_toggled, 'experimental_select_next')

        self.view.switch_select_all.set_active(
            self.settings.get_value('preferences', 'experimental_select_all'))
        self.view.switch_select_all.connect('notify::active', self._on_exp_toggled, 'experimental_select_all')

        self.view.switch_add_above.set_active(
            self.settings.get_value('preferences', 'experimental_add_above'))
        self.view.switch_add_above.connect('notify::active', self._on_exp_toggled, 'experimental_add_above')

        self.view.switch_add_below.set_active(
            self.settings.get_value('preferences', 'experimental_add_below'))
        self.view.switch_add_below.connect('notify::active', self._on_exp_toggled, 'experimental_add_below')

        self.view.switch_escape_clear.set_active(
            self.settings.get_value('preferences', 'experimental_escape_clear'))
        self.view.switch_escape_clear.connect('notify::active', self._on_exp_toggled, 'experimental_escape_clear')

        self.view.switch_multiedit.set_active(
            self.settings.get_value('preferences', 'experimental_multiedit'))
        self.view.switch_multiedit.connect('notify::active', self._on_exp_toggled, 'experimental_multiedit')

        self._sync_exp_sub_sensitivity()

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

        # ---- 拼写检查（pyenchant 后端，缺库/无词典时整组置灰）----
        self.spellchecking_languages = SpellChecker.available_languages()
        if SpellChecker.is_available() and self.spellchecking_languages:
            self.view.option_spellchecking.set_active(
                self.settings.get_value('preferences', 'spellchecking_enabled'))
            self.view.option_spellchecking.connect(
                'notify::active', self.on_switch_toggled, 'spellchecking_enabled')

            language_model = Gtk.StringList.new([
                SpellChecker.language_display_name(tag)
                for tag in self.spellchecking_languages])
            self.view.spellchecking_language_row.set_model(language_model)
            self.view.spellchecking_language_row.set_selected(
                self._spellchecking_language_index(
                    self.settings.get_value('preferences', 'spellchecking_language')))
            self.view.spellchecking_language_row.connect(
                'notify::selected', self.on_spellchecking_language_changed)
            self.view.spellchecking_words_button.connect(
                'clicked', self.on_manage_words_clicked)
        else:
            self.view.option_spellchecking.set_active(False)
            self.view.option_spellchecking.set_sensitive(False)
            self.view.spellchecking_language_row.set_sensitive(False)
            self.view.spellchecking_words_row.set_sensitive(False)
            self.view.option_spellchecking.set_subtitle(_(
                'Unavailable: install pyenchant and hunspell dictionaries to enable.'))

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

        # 自动补全设置初始化
        self.view.option_autocomplete.set_active(self.settings.get_value('preferences', 'enable_autocomplete'))
        self.view.option_autocomplete.connect('notify::active', self.on_switch_toggled, 'enable_autocomplete')

        self.view.option_bracket_completion.set_active(self.settings.get_value('preferences', 'enable_bracket_completion'))
        self.view.option_bracket_completion.connect('notify::active', self.on_switch_toggled, 'enable_bracket_completion')

        self.view.option_selection_brackets.set_active(self.settings.get_value('preferences', 'bracket_selection'))
        self.view.option_selection_brackets.connect('notify::active', self.on_switch_toggled, 'bracket_selection')

        self.view.option_tab_jump_brackets.set_active(self.settings.get_value('preferences', 'tab_jump_brackets'))
        self.view.option_tab_jump_brackets.connect('notify::active', self.on_switch_toggled, 'tab_jump_brackets')

        self.view.option_update_matching_blocks.set_active(self.settings.get_value('preferences', 'update_matching_blocks'))
        self.view.option_update_matching_blocks.connect('notify::active', self.on_switch_toggled, 'update_matching_blocks')

        self.view.option_environment_autocomplete.set_active(self.settings.get_value('preferences', 'enable_environment_autocomplete'))
        self.view.option_environment_autocomplete.connect('notify::active', self.on_switch_toggled, 'enable_environment_autocomplete')

        accel = self.settings.get_value('preferences', 'autocomplete_manual_trigger')
        self.view.trigger_button.set_label(self._accel_label(accel))
        self.view.trigger_button.connect('clicked', self.on_trigger_capture_start)

        # 补全弹窗导航键
        for setting_name, title, subtitle in self._nav_key_rows():
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            button = Gtk.Button()
            button.set_valign(Gtk.Align.CENTER)
            button.set_label(self._accel_label(self.settings.get_value('preferences', setting_name)))
            button.connect('clicked', self.on_nav_capture_start, setting_name)
            row.add_suffix(button)
            row.set_activatable_widget(button)
            self.view.nav_group.add(row)
            self.view.nav_buttons[setting_name] = button

    # ---- 自动补全设置（从 page_autocomplete 移入）----
    def _nav_key_rows(self):
        return [
            ('autocomplete_previous', _('Select previous suggestion'),
             _('Move the selection up one item in the completion popup.')),
            ('autocomplete_next', _('Select next suggestion'),
             _('Move the selection down one item in the completion popup.')),
            ('autocomplete_previous_page', _('Previous page'),
             _('Scroll the completion popup up one page.')),
            ('autocomplete_next_page', _('Next page'),
             _('Scroll the completion popup down one page.')),
            ('autocomplete_accept', _('Accept suggestion'),
             _('Insert the currently selected completion.')),
            ('autocomplete_cancel', _('Dismiss popup'),
             _('Close the completion popup without inserting anything.')),
        ]

    def _accel_label(self, accel):
        _success, keyval, mods = Gtk.accelerator_parse(accel)
        if keyval == 0:
            return _('Disabled')
        return Gtk.accelerator_get_label(keyval, mods)

    def _setup_combo_row(self, combo_row, pref_key, values):
        idx = values.index(self.settings.get_value('preferences', pref_key))
        combo_row.set_selected(idx)
        combo_row.connect('notify::selected', self.on_combo_row_changed, pref_key, values)

    def on_combo_row_changed(self, combo_row, pspec, pref_key, values):
        value = values[combo_row.get_selected()]
        self.settings.set_value('preferences', pref_key, value)

    def _spellchecking_language_index(self, tag):
        '''词典 tag → 下拉索引；保存值不可用时回退 en_US → 首项。'''
        if tag in self.spellchecking_languages:
            return self.spellchecking_languages.index(tag)
        if 'en_US' in self.spellchecking_languages:
            return self.spellchecking_languages.index('en_US')
        return 0

    def on_spellchecking_language_changed(self, combo_row, pspec):
        index = combo_row.get_selected()
        if 0 <= index < len(self.spellchecking_languages):
            self.settings.set_value('preferences', 'spellchecking_language',
                                    self.spellchecking_languages[index])

    def on_manage_words_clicked(self, button):
        '''打开「管理词表」对话框：查看/增删用户词典与会话忽略词。

        首次点击时惰性创建（构造 Adw.Dialog 控件树成本不小），之后复用。
        '''
        if getattr(self, 'spellchecking_words_dialog', None) is None:
            from setzer.dialogs.spellchecking_words.spellchecking_words \
                import SpellCheckingWordsDialog
            self.spellchecking_words_dialog = SpellCheckingWordsDialog(
                self.main_window or ServiceLocator.get_main_window())
        self.spellchecking_words_dialog.run()

    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())
        # 实时同步到预览 SourceView，让用户在偏好设置界面就能看到效果。
        if preference_name in ('show_line_endings', 'show_whitespace'):
            self._apply_preview_space_drawer()

    def on_trigger_capture_start(self, button):
        self._start_capture(button, 'autocomplete_manual_trigger', 'trigger')

    def on_nav_capture_start(self, button, setting_name):
        self._start_capture(button, setting_name, 'nav')

    def _start_capture(self, button, setting_name, mode):
        if self._capture_controller is not None:
            self._cancel_capture()
            return
        self._capture_setting = setting_name
        self._capture_mode = mode
        self._capture_button = button
        button.set_label(_('Press a shortcut… (Esc to cancel)'))
        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect('key-pressed', self.on_capture_keypress)
        self.view.add_controller(controller)
        self._capture_controller = controller

    def on_capture_keypress(self, controller, keyval, keycode, state):
        if keyval == Gdk.keyval_from_name('Escape'):
            self._cancel_capture()
            return True
        mask = Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.keyval_from_name('space') and (state & mask) == 0:
            return True
        if Gtk.keyval_is_modifier(keyval):
            return True
        if self._capture_mode == 'trigger':
            ignore = {'Tab', 'ISO_Left_Tab', 'Return', 'KP_Enter', 'Up', 'Down',
                      'Left', 'Right', 'Page_Up', 'Page_Down', 'Home', 'End',
                      'BackSpace', 'Delete'}
            if keyval in {Gdk.keyval_from_name(n) for n in ignore}:
                return True
        accel = Gtk.accelerator_name(keyval, state & mask)
        _success, kv, m = Gtk.accelerator_parse(accel)
        if kv == 0:
            self._cancel_capture()
            return True
        self.settings.set_value('preferences', self._capture_setting, accel)
        self._capture_button.set_label(self._accel_label(accel))
        self._end_capture()
        return True

    def _end_capture(self):
        if self._capture_controller is not None:
            self.view.remove_controller(self._capture_controller)
            self._capture_controller = None
        self._capture_setting = None
        self._capture_mode = None
        self._capture_button = None

    def _cancel_capture(self):
        accel = self.settings.get_value('preferences', self._capture_setting)
        self._capture_button.set_label(self._accel_label(accel))
        self._end_capture()

    def _apply_preview_space_drawer(self):
        '''将 show_line_endings / show_whitespace 设置同步到预览 SourceView。

        与 document_presenter._apply_space_drawer_settings 保持一致：
        必须调用 set_enable_matrix(True) 才会实际绘制。
        '''
        sd = self.view.preview_source_view.get_space_drawer()
        show_le = self.settings.get_value('preferences', 'show_line_endings')
        show_ws = self.settings.get_value('preferences', 'show_whitespace')
        types = 0
        if show_le:
            types |= GtkSource.SpaceTypeFlags.NEWLINE
        if show_ws:
            types |= GtkSource.SpaceTypeFlags.SPACE | GtkSource.SpaceTypeFlags.TAB
        sd.set_enable_matrix(types != 0)
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
        # 拼写检查：开关直接写回；语言下拉仅在可用时复位（缺库时整组置灰）。
        self.view.option_spellchecking.set_active(defaults['spellchecking_enabled'])
        if getattr(self, 'spellchecking_languages', None):
            self.view.spellchecking_language_row.set_selected(
                self._spellchecking_language_index(defaults['spellchecking_language']))
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
        # 重置自动补全设置
        self.view.option_autocomplete.set_active(defaults['enable_autocomplete'])
        self.view.option_bracket_completion.set_active(defaults['enable_bracket_completion'])
        self.view.option_selection_brackets.set_active(defaults['bracket_selection'])
        self.view.option_tab_jump_brackets.set_active(defaults['tab_jump_brackets'])
        self.view.option_update_matching_blocks.set_active(defaults['update_matching_blocks'])
        self.view.option_environment_autocomplete.set_active(defaults['enable_environment_autocomplete'])
        self.view.trigger_button.set_label(self._accel_label(defaults['autocomplete_manual_trigger']))
        self.settings.set_value('preferences', 'autocomplete_manual_trigger', defaults['autocomplete_manual_trigger'])
        for name in ('autocomplete_previous', 'autocomplete_next',
                     'autocomplete_previous_page', 'autocomplete_next_page',
                     'autocomplete_accept', 'autocomplete_cancel'):
            self.settings.set_value('preferences', name, defaults[name])
            self.view.nav_buttons[name].set_label(self._accel_label(defaults[name]))
        # 重置实验性多光标开关
        self.view.master_switch.set_active(defaults['experimental_features'])
        self.view.switch_multicursor.set_active(defaults['experimental_multicursor'])
        self.view.switch_alt_click.set_active(defaults['experimental_alt_click'])
        self.view.switch_alt_drag.set_active(defaults['experimental_alt_drag'])
        self.view.switch_select_next.set_active(defaults['experimental_select_next'])
        self.view.switch_select_all.set_active(defaults['experimental_select_all'])
        self.view.switch_add_above.set_active(defaults['experimental_add_above'])
        self.view.switch_add_below.set_active(defaults['experimental_add_below'])
        self.view.switch_escape_clear.set_active(defaults['experimental_escape_clear'])
        self.view.switch_multiedit.set_active(defaults['experimental_multiedit'])
        self._sync_exp_sub_sensitivity()

    # ---- Experimental Features handlers ----
    def _on_exp_master_toggled(self, switch, pspec):
        enabled = switch.get_active()
        self.settings.set_value('preferences', 'experimental_features', enabled)
        self._sync_exp_sub_sensitivity()

    def _sync_exp_sub_sensitivity(self):
        enabled = self.view.master_switch.get_active()
        self.view.expander_row.set_sensitive(enabled)
        children = [
            self.view.switch_multicursor,
            self.view.switch_alt_click,
            self.view.switch_alt_drag,
            self.view.switch_select_next,
            self.view.switch_select_all,
            self.view.switch_add_above,
            self.view.switch_add_below,
            self.view.switch_escape_clear,
            self.view.switch_multiedit,
        ]
        for child in children:
            child.set_sensitive(enabled)

    def _on_exp_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())


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

        # 拼写检查：pyenchant 后端；缺库或系统无词典时整组置灰。
        group_spellchecking = Adw.PreferencesGroup()
        group_spellchecking.set_title(_('Spell Checking'))
        self.add(group_spellchecking)

        self.option_spellchecking = Adw.SwitchRow()
        self.option_spellchecking.set_title(_('Check spelling as you type'))
        self.option_spellchecking.set_subtitle(_(
            'Underline misspelled words in the text. '
            'Commands, math and verbatim regions are skipped.'))
        self.option_spellchecking.set_tooltip_text(_(
            'Check spelling as you type using enchant/hunspell dictionaries. '
            'LaTeX commands, math mode and verbatim environments are not checked. '
            'Right-click a marked word for suggestions.'))
        group_spellchecking.add(self.option_spellchecking)

        self.spellchecking_language_row = Adw.ComboRow()
        self.spellchecking_language_row.set_title(_('Language'))
        self.spellchecking_language_row.set_subtitle(_('Dictionary used for spell checking'))
        self.spellchecking_language_row.set_tooltip_text(_(
            'The dictionary language for spell checking. '
            'Additional languages can be installed via hunspell/aspell packages.'))
        group_spellchecking.add(self.spellchecking_language_row)

        self.spellchecking_words_row = Adw.ActionRow()
        self.spellchecking_words_row.set_title(_('Manage Words'))
        self.spellchecking_words_row.set_subtitle(_(
            'View and edit ignored words and your user dictionary'))
        self.spellchecking_words_row.set_tooltip_text(_(
            'Manage the words you ignored for the current session and the '
            'words saved permanently to your user dictionary.'))
        self.spellchecking_words_button = Gtk.Button(label=_('Open'))
        self.spellchecking_words_button.set_valign(Gtk.Align.CENTER)
        self.spellchecking_words_row.add_suffix(self.spellchecking_words_button)
        self.spellchecking_words_row.set_activatable_widget(self.spellchecking_words_button)
        group_spellchecking.add(self.spellchecking_words_row)

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

        # 自动补全设置（从 page_autocomplete 移入）
        group_autocomplete = Adw.PreferencesGroup()
        group_autocomplete.set_title(_('Autocomplete'))
        self.add(group_autocomplete)

        self.option_autocomplete = Adw.SwitchRow()
        self.option_autocomplete.set_title(_('Suggest matching LaTeX Commands'))
        self.option_autocomplete.set_subtitle(_('Show a completion popup as you type LaTeX commands.'))
        group_autocomplete.add(self.option_autocomplete)

        row_trigger = Adw.ActionRow()
        row_trigger.set_title(_('Manual trigger key'))
        row_trigger.set_subtitle(_('Press to capture a shortcut that opens the completion popup.'))
        self.trigger_button = Gtk.Button()
        self.trigger_button.set_valign(Gtk.Align.CENTER)
        row_trigger.add_suffix(self.trigger_button)
        group_autocomplete.add(row_trigger)

        self.nav_group = Adw.PreferencesGroup()
        self.nav_group.set_title(_('Completion Navigation'))
        self.nav_group.set_description(_('Keys used to move and confirm within the completion popup.'))
        self.add(self.nav_group)
        self.nav_buttons = dict()

        group_brackets = Adw.PreferencesGroup()
        group_brackets.set_title(_('Brackets and Blocks'))
        self.add(group_brackets)

        self.option_bracket_completion = Adw.SwitchRow()
        self.option_bracket_completion.set_title(_('Automatically add closing brackets'))
        self.option_bracket_completion.set_subtitle(_('Insert a matching closing bracket when you type an opening one.'))
        group_brackets.add(self.option_bracket_completion)

        self.option_selection_brackets = Adw.SwitchRow()
        self.option_selection_brackets.set_title(_('Add brackets to selected text, instead of replacing it with them'))
        self.option_selection_brackets.set_subtitle(_('Wrap the selected text in brackets, keeping it selected.'))
        group_brackets.add(self.option_selection_brackets)

        self.option_tab_jump_brackets = Adw.SwitchRow()
        self.option_tab_jump_brackets.set_title(_('Jump over closing brackets with Tab'))
        self.option_tab_jump_brackets.set_subtitle(_('Press Tab to move past an automatically inserted closing bracket.'))
        group_brackets.add(self.option_tab_jump_brackets)

        self.option_update_matching_blocks = Adw.SwitchRow()
        self.option_update_matching_blocks.set_title(_('Update matching begin / end blocks'))
        self.option_update_matching_blocks.set_subtitle(_('Keep matching \\begin{} / \\end{} blocks in sync when you edit one.'))
        group_brackets.add(self.option_update_matching_blocks)

        self.option_environment_autocomplete = Adw.SwitchRow()
        self.option_environment_autocomplete.set_title(_('Automatically add matching \\end{}'))
        self.option_environment_autocomplete.set_subtitle(_('When you type \\begin{, automatically insert the matching \\end{} (use Tab to jump to the content placeholder).'))
        group_brackets.add(self.option_environment_autocomplete)

        # Experimental Features（实验性功能：多光标编辑，归属编辑器行为）
        group_experimental = Adw.PreferencesGroup()
        group_experimental.set_title(_('Experimental Features'))
        group_experimental.set_description(_(
            'Unstable multi-cursor editing features.'))
        self.add(group_experimental)

        self.master_switch = Adw.SwitchRow()
        self.master_switch.set_title(_('Enable experimental features'))
        self.master_switch.set_subtitle(_(
            'Master switch for all multi-cursor features below.'))
        group_experimental.add(self.master_switch)

        self.expander_row = Adw.ExpanderRow()
        self.expander_row.set_title(_('Multi-Cursor Settings'))
        self.expander_row.set_subtitle(_(
            'Individually enable or disable each multi-cursor feature.'))
        group_experimental.add(self.expander_row)

        self.switch_multicursor = Adw.SwitchRow()
        self.switch_multicursor.set_title(_('Multi-cursor mode'))
        self.switch_multicursor.set_subtitle(_(
            'Allow creating additional cursors.'))
        self.expander_row.add_row(self.switch_multicursor)

        self.switch_alt_click = Adw.SwitchRow()
        self.switch_alt_click.set_title(_('Alt+Click to add/remove cursor'))
        self.switch_alt_click.set_subtitle(_(
            'Add a cursor at the clicked position, or remove an existing one.'))
        self.expander_row.add_row(self.switch_alt_click)

        self.switch_alt_drag = Adw.SwitchRow()
        self.switch_alt_drag.set_title(_('Alt+Drag column selection'))
        self.switch_alt_drag.set_subtitle(_(
            'Drag to create a column (rectangular) selection.'))
        self.expander_row.add_row(self.switch_alt_drag)

        self.switch_select_next = Adw.SwitchRow()
        self.switch_select_next.set_title(_('Select next occurrence (Ctrl+D)'))
        self.switch_select_next.set_subtitle(_(
            'Select the next match of the selected text or word.'))
        self.expander_row.add_row(self.switch_select_next)

        self.switch_select_all = Adw.SwitchRow()
        self.switch_select_all.set_title(_('Select all occurrences (Ctrl+Shift+L)'))
        self.switch_select_all.set_subtitle(_(
            'Select all matches of the selected text or word.'))
        self.expander_row.add_row(self.switch_select_all)

        self.switch_add_above = Adw.SwitchRow()
        self.switch_add_above.set_title(_('Add cursor above (Ctrl+Alt+↑)'))
        self.switch_add_above.set_subtitle(_(
            'Add a cursor on the line above each existing cursor.'))
        self.expander_row.add_row(self.switch_add_above)

        self.switch_add_below = Adw.SwitchRow()
        self.switch_add_below.set_title(_('Add cursor below (Ctrl+Alt+↓)'))
        self.switch_add_below.set_subtitle(_(
            'Add a cursor on the line below each existing cursor.'))
        self.expander_row.add_row(self.switch_add_below)

        self.switch_escape_clear = Adw.SwitchRow()
        self.switch_escape_clear.set_title(_('Escape to clear multi-cursor'))
        self.switch_escape_clear.set_subtitle(_(
            'Press Escape to clear all additional cursors.'))
        self.expander_row.add_row(self.switch_escape_clear)

        self.switch_multiedit = Adw.SwitchRow()
        self.switch_multiedit.set_title(_('Multi-cursor text editing'))
        self.switch_multiedit.set_subtitle(_(
            'Insert, delete, and indent at all cursor positions simultaneously.'))
        self.expander_row.add_row(self.switch_multiedit)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

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
# along with this program, see <http://www.gnu.org/licenses/

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw
from gi.repository import Pango
import os
import sys

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


# theme mode: display name -> stored value -> Adw.ColorScheme
# 注意：模块顶层不允许调用 _()，因为 gettext.install 尚未执行；
# 翻译在 init()/view.__init__ 构建模型时进行（运行时已注入 _）。
#
# 维护契约：此处的英文字符串（'System'/'Light'/'Dark'）是 .po 文件中的
# 翻译 msgid。若修改显示名称，必须同步更新 .po 文件，否则运行时翻译
# 会回退到未翻译的英文。Python 无编译期检查，依赖人工保证一致性。
THEME_MODES = [
    ('System', 'system', Adw.ColorScheme.DEFAULT),
    ('Light', 'light', Adw.ColorScheme.FORCE_LIGHT),
    ('Dark', 'dark', Adw.ColorScheme.FORCE_DARK),
]

# language: display name -> stored value (locale code); 通过 gettext 的 languages 参数在重启后生效
# 同 THEME_MODES：英文名是翻译 msgid，修改需同步 .po 文件。
LANGUAGES = [
    ('English', 'en'),
    ('Chinese (Simplified)', 'zh_CN'),
    ('Chinese (Traditional)', 'zh_TW'),
    ('German', 'de'),
    ('Spanish', 'es'),
    ('French', 'fr'),
    ('Italian', 'it'),
    ('Portuguese (Brazil)', 'pt_BR'),
]


class PageAppearanceColors(object):

    def __init__(self, preferences, settings, main_window=None):
        self.view = PageAppearanceColorsView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = main_window

    def init(self):
        # theme mode
        current_theme = self.settings.get_value('preferences', 'app_theme_mode')
        theme_index = next((i for i, m in enumerate(THEME_MODES) if m[1] == current_theme), 0)
        self.view.theme_combo.set_selected(theme_index)
        self.view.theme_combo.connect('notify::selected', self.on_theme_changed)

        # editor color scheme (GtkSourceView)
        # _editor_scheme_ids[selected_index] 给出对应的 scheme ID（'' 表示跟随系统）。
        # 方案列表在运行时从 StyleSchemeManager 读取，因此用 index→id 映射而非
        # 直接依赖 StringList 顺序（方案文件增删后顺序可能变化）。
        # GtkSource 5 的 StyleSchemeManager 用 get_scheme_ids() 取 ID 列表，
        # 再 get_scheme(id) 取方案对象（无 get_schemes() 方法）。
        scheme_manager = ServiceLocator.get_source_style_scheme_manager()
        self._editor_scheme_ids = ['']
        editor_scheme_model = Gtk.StringList()
        editor_scheme_model.append(_('Follow system theme'))
        current_scheme = self.settings.get_value('preferences', 'editor_style_scheme')
        selected_index = 0
        for scheme_id in scheme_manager.get_scheme_ids():
            scheme = scheme_manager.get_scheme(scheme_id)
            if scheme is None:
                continue
            self._editor_scheme_ids.append(scheme_id)
            editor_scheme_model.append(scheme.get_name())
            if scheme_id == current_scheme:
                selected_index = len(self._editor_scheme_ids) - 1
        self.view.editor_scheme_combo.set_model(editor_scheme_model)
        self.view.editor_scheme_combo.set_selected(selected_index)
        self.view.editor_scheme_combo.connect('notify::selected', self.on_editor_scheme_changed)

        # language
        current_lang = self.settings.get_value('preferences', 'language')
        lang_index = next((i for i, l in enumerate(LANGUAGES) if l[1] == current_lang), 0)
        self.view.language_combo.set_selected(lang_index)
        self.view.language_combo.connect('notify::selected', self.on_language_changed)

        # font
        self.view.font_chooser_button.set_font_desc(
            Pango.FontDescription.from_string(self.settings.get_value('preferences', 'font_string')))
        self.view.font_chooser_button.connect('notify::font-desc', self.on_font_set)
        self.view.option_use_system_font.set_active(
            self.settings.get_value('preferences', 'use_system_font'))
        self.view.font_chooser_row.set_sensitive(not self.view.option_use_system_font.get_active())
        self.view.option_use_system_font.connect('notify::active', self.on_use_system_font_toggled)

        # line numbers vertical offset
        self.view.line_numbers_offset_spin.set_value(
            self.settings.get_value('preferences', 'line_numbers_vertical_offset'))
        self.view.line_numbers_offset_spin.connect('notify::value', self.on_line_numbers_offset_changed)

        # line spacing
        self.view.line_spacing_spin.set_value(
            self.settings.get_value('preferences', 'line_spacing'))
        self.view.line_spacing_spin.connect('notify::value', self.on_line_spacing_changed)

        # preview width fraction
        fraction = self.settings.get_value('window_state', 'preview_width_fraction')
        self.view.preview_width_scale.set_value(int(fraction * 100))
        self.view.preview_width_scale.connect('value-changed', self.on_preview_width_changed)

        # recolor_pdf
        self.view.option_recolor_pdf.set_active(
            self.settings.get_value('preferences', 'recolor_pdf'))
        self.view.option_recolor_pdf.connect(
            'notify::active', self.on_recolor_pdf_toggled)

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    # ---- theme ----
    def on_theme_changed(self, combo, pspec=None):
        value = THEME_MODES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'app_theme_mode', value)
        self.apply_theme(value)

    def on_editor_scheme_changed(self, combo, pspec=None):
        # Adw.ComboRow 在 set_selected 时触发 notify::selected，包括程序化
        # 重置（on_reset_confirmed）。INVALID_LIST_POSITION 守卫防御模型未就绪。
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        scheme_id = self._editor_scheme_ids[selected]
        # set_style_scheme_name 先清空 ServiceLocator 缓存再 set_value，
        # settings_changed 驱动已打开文档 on_settings_changed 重新应用配色。
        ServiceLocator.set_style_scheme_name(scheme_id)

    def on_language_changed(self, combo, pspec=None):
        value = LANGUAGES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'language', value)
        self.settings.pickle()
        self.show_restart_dialog()

    def show_restart_dialog(self):
        dialog = Adw.AlertDialog(
            heading=_('Restart Required'),
            body=_('The new language will take effect after the application is restarted.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('restart', _('Restart'))
        dialog.set_response_appearance('restart', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('restart')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_restart_response)

    def on_restart_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'restart':
            self.restart_application()

    def restart_application(self):
        self.settings.pickle()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @staticmethod
    def apply_theme(value):
        scheme = next((m[2] for m in THEME_MODES if m[1] == value), Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    # ---- font ----
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
            # 钳制时通知用户：FontDialogButton 不会自行提示，用户可能困惑
            # 为何选了 4pt 却显示 6pt。
            if clamped == 'min':
                msg = _('Font size is too small; clamped to 6pt minimum.')
            else:
                msg = _('Font size is too large; clamped to 24pt maximum.')
            self._show_toast(msg)

    def _show_toast(self, message):
        '''显示 toast 通知（字体钳制等操作反馈）。'''
        main_window = self.main_window or ServiceLocator.get_main_window()
        if main_window and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(4)
            main_window.toast_overlay.add_toast(toast)

    def on_line_numbers_offset_changed(self, spin, pspec=None):
        self.settings.set_value('preferences', 'line_numbers_vertical_offset', spin.get_value())

    def on_line_spacing_changed(self, spin, pspec=None):
        self.settings.set_value('preferences', 'line_spacing', int(spin.get_value()))

    # ---- preview width ----
    def on_preview_width_changed(self, scale):
        fraction = scale.get_value() / 100.0
        self.settings.set_value('window_state', 'preview_width_fraction', fraction)
        if self.main_window is not None:
            self.main_window.preview_split.set_sidebar_width_fraction(fraction)

    def on_recolor_pdf_toggled(self, switch, pspec=None):
        self.settings.set_value('preferences', 'recolor_pdf', switch.get_active())

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to Defaults?'),
            body=_('All appearance settings will be restored to their default values.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_reset_confirmed)

    def on_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reset':
            defaults = self.settings.defaults['preferences']
            current_theme = defaults['app_theme_mode']
            theme_index = next((i for i, m in enumerate(THEME_MODES) if m[1] == current_theme), 0)
            self.view.theme_combo.set_selected(theme_index)
            # editor_style_scheme 默认 '' = 跟随系统主题 = 索引 0。
            # set_selected 触发 on_editor_scheme_changed → set_style_scheme_name('')
            # 完成设置回写与文档重应用。
            self.view.editor_scheme_combo.set_selected(0)
            current_lang = defaults['language']
            lang_index = next((i for i, l in enumerate(LANGUAGES) if l[1] == current_lang), 0)
            self.view.language_combo.set_selected(lang_index)
            self.view.option_use_system_font.set_active(defaults['use_system_font'])
            self.view.font_chooser_button.set_font_desc(
                Pango.FontDescription.from_string(defaults['font_string']))
            self.view.line_numbers_offset_spin.set_value(defaults['line_numbers_vertical_offset'])
            self.view.line_spacing_spin.set_value(defaults['line_spacing'])
            fraction = self.settings.defaults['window_state']['preview_width_fraction']
            self.view.preview_width_scale.set_value(int(fraction * 100))
            self.view.option_recolor_pdf.set_active(defaults['recolor_pdf'])
            self.preferences.page_editor.on_reset_clicked(None)


class PageAppearanceColorsView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Appearance'))
        self.set_icon_name('preferences-desktop-appearance-symbolic')

        # theme mode
        group_theme = Adw.PreferencesGroup()
        group_theme.set_title(_('Theme'))
        self.add(group_theme)

        self.theme_combo = Adw.ComboRow()
        self.theme_combo.set_title(_('Color Scheme'))
        theme_model = Gtk.StringList()
        for name, _value, _scheme in THEME_MODES:
            theme_model.append(_(name))
        self.theme_combo.set_model(theme_model)
        group_theme.add(self.theme_combo)

        # 编辑器（GtkSourceView）配色方案。第一项「Follow system theme」
        # 对应空字符串 ID，由 ServiceLocator.get_style_scheme 根据 Adw 深浅色
        # 选择 default / default-dark；其余项来自 GtkSource.StyleSchemeManager
        # 已加载的方案（resources/themes + 用户配置目录 themes/）。
        # 与上面 app 主题不同：app 主题控制整个 Adw 应用的深浅色，编辑器配色
        # 仅影响代码编辑区，可独立选择（例如 dark app + 自定义编辑器主题）。
        self.editor_scheme_combo = Adw.ComboRow()
        self.editor_scheme_combo.set_title(_('Editor Color Scheme'))
        self.editor_scheme_combo.set_subtitle(
            _('Color scheme for the LaTeX editor. "Follow system theme" matches the application light/dark mode.'))
        group_theme.add(self.editor_scheme_combo)

        # language
        group_language = Adw.PreferencesGroup()
        group_language.set_title(_('Language'))
        self.add(group_language)

        self.language_combo = Adw.ComboRow()
        self.language_combo.set_title(_('Interface Language'))
        self.language_combo.set_subtitle(_('Changes take effect after the application is restarted.'))
        language_model = Gtk.StringList()
        for name, _value in LANGUAGES:
            language_model.append(_(name))
        self.language_combo.set_model(language_model)
        group_language.add(self.language_combo)

        # font
        group_font = Adw.PreferencesGroup()
        group_font.set_title(_('Font'))
        self.add(group_font)

        font_string = FontManager.get_system_font() or 'Monospace'
        self.option_use_system_font = Adw.SwitchRow()
        self.option_use_system_font.set_title(_('Use the system fixed width font'))
        self.option_use_system_font.set_subtitle(font_string)
        group_font.add(self.option_use_system_font)

        self.font_chooser_button = Gtk.FontDialogButton(dialog=Gtk.FontDialog())
        self.font_chooser_button.set_valign(Gtk.Align.CENTER)
        self.font_chooser_row = Adw.ActionRow()
        self.font_chooser_row.set_title(_('Set Editor Font'))
        self.font_chooser_row.add_suffix(self.font_chooser_button)
        group_font.add(self.font_chooser_row)

        # 行号垂直微调：不同字体的 ascent/descent 比例不同，行号相对文本可能
        # 有轻微上下偏移。提供 -10..+10 像素、0.5 步进的 SpinRow 供用户补偿。
        # 正值下移、负值上移，默认 0.0。
        self.line_numbers_offset_spin = Adw.SpinRow.new_with_range(-10.0, 10.0, 0.5)
        self.line_numbers_offset_spin.set_digits(1)
        self.line_numbers_offset_spin.set_title(_('Line Number Vertical Offset'))
        self.line_numbers_offset_spin.set_subtitle(
            _('Fine-tune line numbers vertical position in pixels. '
              'Adjust if line numbers appear slightly misaligned with text.'))
        group_font.add(self.line_numbers_offset_spin)

        # 行距：每行下方额外添加的像素间距。0 = 紧凑（默认），增大后行间更宽松。
        self.line_spacing_spin = Adw.SpinRow.new_with_range(0.0, 12.0, 1.0)
        self.line_spacing_spin.set_digits(0)
        self.line_spacing_spin.set_title(_('Line Spacing'))
        self.line_spacing_spin.set_subtitle(
            _('Extra vertical space between lines in pixels.'))
        group_font.add(self.line_spacing_spin)

        # preview width
        group_preview = Adw.PreferencesGroup()
        group_preview.set_title(_('Preview'))
        self.add(group_preview)

        self.preview_width_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 15, 85, 1)
        self.preview_width_scale.set_valign(Gtk.Align.CENTER)
        self.preview_width_scale.set_hexpand(True)
        self.preview_width_scale.set_draw_value(True)
        self.preview_width_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.preview_width_scale.add_mark(50, Gtk.PositionType.BOTTOM, None)
        self.preview_width_row = Adw.ActionRow()
        self.preview_width_row.set_title(_('Preview Sidebar Width'))
        self.preview_width_row.set_subtitle(_('Percentage of the window width allocated to the PDF preview.'))
        self.preview_width_row.add_suffix(self.preview_width_scale)
        group_preview.add(self.preview_width_row)

        # 预览 PDF 配色随主题：深色模式下把 PDF 前景/背景重着色以匹配编辑器
        # 深浅色（recolor_pdf）。该值在 preview 工具栏有快速切换按钮，此处暴露
        # 为偏好以便持久化与重置。
        self.option_recolor_pdf = Adw.SwitchRow()
        self.option_recolor_pdf.set_title(_('Match PDF colors to theme'))
        self.option_recolor_pdf.set_subtitle(
            _('Recolor the PDF preview to match the light/dark theme.'))
        group_preview.add(self.option_recolor_pdf)

        group_reset = Adw.PreferencesGroup()
        # 标记以便 PreferencesDialog.setup() 在合并 Editor 组后将其重置按钮
        # 重新移到末尾（保持“重置”在所有设置项之后）。
        group_reset._is_appearance_reset = True
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

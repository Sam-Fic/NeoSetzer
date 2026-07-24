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

from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


# theme mode: display name -> stored value -> Adw.ColorScheme
# 注意：模块顶层不允许调用 _()，因为 gettext.install 尚未执行；
# 翻译在 init()/view.__init__ 构建模型时进行（运行时已注入 _）。
THEME_MODES = [
    ('System', 'system', Adw.ColorScheme.DEFAULT),
    ('Light', 'light', Adw.ColorScheme.FORCE_LIGHT),
    ('Dark', 'dark', Adw.ColorScheme.FORCE_DARK),
]

# language: display name -> stored value (locale code); 通过 gettext 的 languages 参数在重启后生效
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

        # preview width fraction
        fraction = self.settings.get_value('window_state', 'preview_width_fraction')
        self.view.preview_width_scale.set_value(int(fraction * 100))
        self.view.preview_width_scale.connect('value-changed', self.on_preview_width_changed)

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    # ---- theme ----
    def on_theme_changed(self, combo, pspec=None):
        value = THEME_MODES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'app_theme_mode', value)
        self.apply_theme(value)

    def on_language_changed(self, combo, pspec=None):
        value = LANGUAGES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'language', value)
        # 语言选择已接入 gettext，重启应用后按此偏好加载界面语言。

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
        if size < 6 * Pango.SCALE:
            font_desc.set_size(6 * Pango.SCALE)
            button.set_font_desc(font_desc)
        elif size > 24 * Pango.SCALE:
            font_desc.set_size(24 * Pango.SCALE)
            button.set_font_desc(font_desc)
        self.settings.set_value('preferences', 'font_string', font_desc.to_string())

    # ---- preview width ----
    def on_preview_width_changed(self, scale):
        fraction = scale.get_value() / 100.0
        self.settings.set_value('window_state', 'preview_width_fraction', fraction)
        if self.main_window is not None:
            self.main_window.preview_split.set_sidebar_width_fraction(fraction)

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
            current_lang = defaults['language']
            lang_index = next((i for i, l in enumerate(LANGUAGES) if l[1] == current_lang), 0)
            self.view.language_combo.set_selected(lang_index)
            self.view.option_use_system_font.set_active(defaults['use_system_font'])
            self.view.font_chooser_button.set_font_desc(
                Pango.FontDescription.from_string(defaults['font_string']))
            fraction = self.settings.defaults['window_state']['preview_width_fraction']
            self.view.preview_width_scale.set_value(int(fraction * 100))
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

        group_reset = Adw.PreferencesGroup()
        # 标记以便 PreferencesDialog.setup() 在合并 Editor 组后将其重置按钮
        # 重新移到末尾（保持“重置”在所有设置项之后）。
        group_reset._is_appearance_reset = True
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

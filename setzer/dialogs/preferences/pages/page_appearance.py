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
from gi.repository import Gtk, Adw
import os
import sys
import json

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_controller_app import ShortcutControllerApp


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
    ('简体中文', 'zh_CN'),
    ('繁體中文', 'zh_TW'),
    ('Deutsch', 'de'),
    ('Español', 'es'),
    ('Français', 'fr'),
    ('Italiano', 'it'),
    ('Português (Brasil)', 'pt_BR'),
]

# startup: display name -> stored value; 同 THEME_MODES/LANGUAGES 维护契约：
# 英文名为翻译 msgid，修改需同步 .po 文件。
STARTUP_MODES = [
    ('Last session', 'last_session'),
    ('Empty workspace', 'empty'),
]


class PageGeneral(object):
    '''通用设置页：合并原 Appearance / First Run / Settings Data 三页。'''

    def __init__(self, preferences, settings, main_window=None):
        self.view = PageGeneralView()
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

        # on startup（应用级/界面设置，归属此通用页）
        current_startup = self.settings.get_value('preferences', 'on_startup')
        startup_index = next((i for i, m in enumerate(STARTUP_MODES) if m[1] == current_startup), 0)
        self.view.startup_combo.set_selected(startup_index)
        self.view.startup_combo.connect('notify::selected', self.on_startup_selected)

        # preview width fraction
        fraction = self.settings.get_value('window_state', 'preview_width_fraction')
        self.view.preview_width_scale.set_value(int(fraction * 100))
        self.view.preview_width_scale.connect('value-changed', self.on_preview_width_changed)

        # sidebar width fraction
        fraction = self.settings.get_value('window_state', 'sidebar_width_fraction')
        self.view.sidebar_width_scale.set_value(int(fraction * 100))
        self.view.sidebar_width_scale.connect('value-changed', self.on_sidebar_width_changed)

        # recolor_pdf
        self.view.option_recolor_pdf.set_active(
            self.settings.get_value('preferences', 'recolor_pdf'))
        self.view.option_recolor_pdf.connect(
            'notify::active', self.on_recolor_pdf_toggled)

        self.view.preview_zoom_values = ['fit_to_width', 'fit_to_text_width', 'fit_to_height', 'manual']
        idx = self.view.preview_zoom_values.index(
            self.settings.get_value('preferences', 'preview_zoom'))
        self.view.option_preview_zoom.set_selected(idx)
        self.view.option_preview_zoom.connect(
            'notify::selected', self.on_preview_zoom_changed)

        # Tutorial（来自 First Run 页）
        self.view.show_again_button.connect('clicked', self.on_show_again_clicked)

        # Backup and Restore（来自 Settings Data 页）
        self.main_window = ServiceLocator.get_main_window()
        self.shortcuts = ServiceLocator.get_shortcuts()
        self.view.toast_overlay = getattr(self.main_window, 'toast_overlay', None)
        self.view.option_export.connect('clicked', self.on_export_clicked)
        self.view.option_import.connect('clicked', self.on_import_clicked)
        self.view.option_reset_all.connect('clicked', self.on_reset_all_clicked)

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    # ---- theme ----
    def on_theme_changed(self, combo, pspec=None):
        value = THEME_MODES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'app_theme_mode', value)
        self.apply_theme(value)
        # 注：编辑器配色方案网格已移至 Editor 页（Appearance 分组），其网格
        # 重建与预览刷新由 preferences.py 在 theme_combo 变化时统一驱动。

    def on_language_changed(self, combo, pspec=None):
        value = LANGUAGES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'language', value)
        self.settings.pickle()
        self.show_restart_dialog()

    def on_startup_selected(self, combo, pspec=None):
        value = STARTUP_MODES[combo.get_selected()][1]
        self.settings.set_value('preferences', 'on_startup', value)

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

    # ---- preview width ----
    def on_preview_width_changed(self, scale):
        fraction = scale.get_value() / 100.0
        self.settings.set_value('window_state', 'preview_width_fraction', fraction)
        if self.main_window is not None:
            self.main_window.preview_split.set_sidebar_width_fraction(fraction)

    # ---- sidebar width ----
    def on_sidebar_width_changed(self, scale):
        fraction = scale.get_value() / 100.0
        self.settings.set_value('window_state', 'sidebar_width_fraction', fraction)
        if self.main_window is not None:
            self.main_window.sidebar_split.set_sidebar_width_fraction(fraction)

    def on_recolor_pdf_toggled(self, switch, pspec=None):
        self.settings.set_value('preferences', 'recolor_pdf', switch.get_active())

    def on_preview_zoom_changed(self, combo_row, pspec):
        value = self.view.preview_zoom_values[combo_row.get_selected()]
        self.settings.set_value('preferences', 'preview_zoom', value)

    # ---- tutorial（来自 First Run 页） ----
    def on_show_again_clicked(self, button):
        # 延迟导入避免与 dialog_locator 循环依赖。
        from setzer.dialogs.dialog_locator import DialogLocator
        DialogLocator.get_dialog('first_run_tutorial').show_again()

    # ---- backup and restore（来自 Settings Data 页） ----
    def rebuild_shortcut_controllers(self):
        '''导入的快捷键需同步到当前应用：替换 app 级快捷键控制器（与
        PageShortcuts 导入时一致）。'''
        old = self.shortcuts.shortcut_controller_app
        new = ShortcutControllerApp()
        self.main_window.remove_controller(old)
        self.shortcuts.shortcut_controller_app = new
        self.main_window.add_controller(new)

    def on_export_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Export Settings'))
        dialog.set_initial_name('setzer-settings.json')
        filter_json = Gtk.FileFilter()
        filter_json.set_name('JSON')
        filter_json.add_mime_type('application/json')
        filter_json.add_pattern('*.json')
        dialog.set_default_filter(filter_json)
        dialog.save(self.main_window, None, self.on_export_response)

    def on_export_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except Exception:
            return
        if file is None:
            return
        data = {
            'format': 'setzer-settings',
            'version': 1,
            'preferences': self.settings.get_value('preferences', None),
            'keyboard_shortcuts': self.settings.get_value('keyboard_shortcuts', None),
        }
        try:
            with open(file.get_path(), 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._show_toast(_('Could not export settings: {}').format(e))
            return
        self._show_toast(_('Settings exported.'))

    def on_import_clicked(self, button):
        dialog = Gtk.FileDialog()
        dialog.set_title(_('Import Settings'))
        filter_json = Gtk.FileFilter()
        filter_json.set_name('JSON')
        filter_json.add_mime_type('application/json')
        filter_json.add_pattern('*.json')
        dialog.set_default_filter(filter_json)
        dialog.open(self.main_window, None, self.on_import_response)

    def on_import_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except Exception:
            return
        if file is None:
            return
        try:
            with open(file.get_path(), 'r', encoding='utf-8') as f:
                incoming = json.load(f)
        except Exception as e:
            self._show_toast(_('Could not read settings file: {}').format(e))
            return
        if not isinstance(incoming, dict):
            self._show_toast(_('Invalid settings file.'))
            return

        imported = {'preferences': 0, 'keyboard_shortcuts': 0}
        for section in ('preferences', 'keyboard_shortcuts'):
            section_data = incoming.get(section)
            if not isinstance(section_data, dict):
                continue
            defaults_section = self.settings.defaults.get(section, {})
            for key, value in section_data.items():
                if key in defaults_section:
                    self.settings.set_value(section, key, value)
                    imported[section] += 1

        self.settings.pickle()
        if imported['keyboard_shortcuts'] > 0:
            self.rebuild_shortcut_controllers()
        self._show_toast(_('Settings imported. Reopen Preferences to see all changes.'))

    def on_reset_all_clicked(self, button):
        confirm = Adw.AlertDialog()
        confirm.set_heading(_('Reset all preferences?'))
        confirm.set_body(_('This resets all preferences to their default values. '
                           'Keyboard shortcuts and window layout are kept.'))
        confirm.add_response('cancel', _('Cancel'))
        confirm.add_response('reset', _('Reset'))
        confirm.set_default_response('cancel')
        confirm.set_close_response('cancel')
        confirm.choose(self.main_window, None, self.on_reset_all_response)

    def on_reset_all_response(self, dialog, result):
        if dialog.choose_finish(result) != 'reset':
            return
        self.settings.reset_preferences()
        self.settings.pickle()
        self._show_toast(_('Preferences reset. Reopen Preferences to see all changes.'))

    def _show_toast(self, message):
        if self.view.toast_overlay is not None:
            self.view.toast_overlay.add_toast(Adw.Toast(title=message))
        else:
            self.preferences.view.add_toast(Adw.Toast(title=message))

    # ---- reset ----
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
            # editor_style_scheme 默认 '' = 跟随系统主题。写回设置即可，
            # Editor 页监听 settings_changed 会重建方案网格并刷新预览配色。
            ServiceLocator.set_style_scheme_name('')
            current_lang = defaults['language']
            lang_index = next((i for i, l in enumerate(LANGUAGES) if l[1] == current_lang), 0)
            self.view.language_combo.set_selected(lang_index)
            current_startup = defaults['on_startup']
            startup_index = next((i for i, m in enumerate(STARTUP_MODES) if m[1] == current_startup), 0)
            self.view.startup_combo.set_selected(startup_index)
            fraction = self.settings.defaults['window_state']['preview_width_fraction']
            self.view.preview_width_scale.set_value(int(fraction * 100))
            fraction = self.settings.defaults['window_state']['sidebar_width_fraction']
            self.view.sidebar_width_scale.set_value(int(fraction * 100))
            self.view.option_recolor_pdf.set_active(defaults['recolor_pdf'])
        self.view.option_preview_zoom.set_selected(
            self.view.preview_zoom_values.index(defaults['preview_zoom']))


class PageGeneralView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('General'))
        self.set_icon_name('preferences-system-symbolic')
        self.toast_overlay = None

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

        # on startup（应用级/界面设置，归属此通用页）
        group_startup = Adw.PreferencesGroup()
        group_startup.set_title(_('On Startup'))
        self.add(group_startup)

        self.startup_combo = Adw.ComboRow()
        self.startup_combo.set_title(_('Open'))
        self.startup_combo.set_subtitle(_('Whether to restore the previous session or start with an empty workspace.'))
        startup_model = Gtk.StringList()
        for name, _value in STARTUP_MODES:
            startup_model.append(_(name))
        self.startup_combo.set_model(startup_model)
        group_startup.add(self.startup_combo)

        # 预览 PDF 配色随主题：深色模式下把 PDF 前景/背景重着色以匹配编辑器
        # 深浅色（recolor_pdf）。该值在 preview 工具栏有快速切换按钮，此处暴露
        # 为偏好以便持久化与重置。
        self.option_recolor_pdf = Adw.SwitchRow()
        self.option_recolor_pdf.set_title(_('Match PDF colors to theme'))
        self.option_recolor_pdf.set_subtitle(
            _('Recolor the PDF preview to match the light/dark theme.'))
        group_preview.add(self.option_recolor_pdf)

        self.option_preview_zoom = Adw.ComboRow()
        self.option_preview_zoom.set_title(_('Default Zoom'))
        self.option_preview_zoom.set_subtitle(
            _('Initial zoom mode for the PDF preview.'))
        self.option_preview_zoom.set_tooltip_text(_(
            'Default zoom mode for the PDF preview. '
            'Fit Width fills the preview width; Fit Text Width fits the text column; '
            'Fit Height fills the preview height; Manual starts at 100%.'))
        zoom_model = Gtk.StringList.new([
            _('Fit Width'),
            _('Fit Text Width'),
            _('Fit Height'),
            _('Manual (100%)'),
        ])
        self.option_preview_zoom.set_model(zoom_model)
        group_preview.add(self.option_preview_zoom)

        # sidebar width
        group_sidebar = Adw.PreferencesGroup()
        group_sidebar.set_title(_('Sidebar'))
        self.add(group_sidebar)

        self.sidebar_width_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 10, 45, 1)
        self.sidebar_width_scale.set_valign(Gtk.Align.CENTER)
        self.sidebar_width_scale.set_hexpand(True)
        self.sidebar_width_scale.set_draw_value(True)
        self.sidebar_width_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.sidebar_width_scale.add_mark(20, Gtk.PositionType.BOTTOM, None)
        self.sidebar_width_row = Adw.ActionRow()
        self.sidebar_width_row.set_title(_('Sidebar Width'))
        self.sidebar_width_row.set_subtitle(_('Percentage of the window width allocated to the sidebar.'))
        self.sidebar_width_row.add_suffix(self.sidebar_width_scale)
        group_sidebar.add(self.sidebar_width_row)

        # tutorial（来自 First Run 页）
        group_tutorial = Adw.PreferencesGroup()
        group_tutorial.set_title(_('First-Run Tutorial'))
        self.add(group_tutorial)

        self.show_again_row = Adw.ActionRow()
        self.show_again_row.set_title(_('Show the tutorial again'))
        self.show_again_row.set_subtitle(_('Open the welcome tips dialog.'))
        self.show_again_button = Gtk.Button(label=_('Show'))
        self.show_again_button.set_valign(Gtk.Align.CENTER)
        self.show_again_row.add_suffix(self.show_again_button)
        self.show_again_row.set_activatable_widget(self.show_again_button)
        group_tutorial.add(self.show_again_row)

        # backup and restore（来自 Settings Data 页）
        group_backup = Adw.PreferencesGroup()
        group_backup.set_title(_('Backup and Restore'))
        group_backup.set_description(_('Export your preferences and keyboard shortcuts to a '
                                        'file, or import them on another machine.'))
        self.add(group_backup)

        self.export_row = Adw.ActionRow()
        self.export_row.set_title(_('Export Settings'))
        self.export_row.set_subtitle(_('Save your preferences and keyboard shortcuts to a file.'))
        self.option_export = Gtk.Button(label=_('Export'))
        self.option_export.add_css_class('suggested-action')
        self.option_export.set_valign(Gtk.Align.CENTER)
        self.export_row.add_suffix(self.option_export)
        self.export_row.set_activatable_widget(self.option_export)
        group_backup.add(self.export_row)

        self.import_row = Adw.ActionRow()
        self.import_row.set_title(_('Import Settings'))
        self.import_row.set_subtitle(_('Load preferences and keyboard shortcuts from a file.'))
        self.option_import = Gtk.Button(label=_('Import'))
        self.option_import.set_valign(Gtk.Align.CENTER)
        self.import_row.add_suffix(self.option_import)
        self.import_row.set_activatable_widget(self.option_import)
        group_backup.add(self.import_row)

        self.reset_all_row = Adw.ActionRow()
        self.reset_all_row.set_title(_('Reset all preferences'))
        self.reset_all_row.set_subtitle(_('Restore all preference values to their defaults.'))
        self.option_reset_all = Gtk.Button(label=_('Reset'))
        self.option_reset_all.add_css_class('destructive-action')
        self.option_reset_all.set_valign(Gtk.Align.CENTER)
        self.reset_all_row.add_suffix(self.option_reset_all)
        self.reset_all_row.set_activatable_widget(self.option_reset_all)
        group_backup.add(self.reset_all_row)

        # reset（通用页偏好）
        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

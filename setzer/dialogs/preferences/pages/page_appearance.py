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
# along with this program, see <http://www.gnu.org/licenses/

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw
import os
import sys
import json

from setzer.app.service_locator import ServiceLocator
from setzer.ai_fix import agent_runner
from setzer.ai_fix.presets import default_tools, builtin_names
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
    '''通用设置页：合并原 Appearance / First Run / Settings Data 三页，
    并承载 AI 代理设置（自 Build System 页迁入，供 AI Fix 与 Agent 终端
    按钮等多个入口共用）。'''

    def __init__(self, preferences, settings, main_window=None):
        self.view = PageGeneralView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = main_window
        # AI 代理设置列表缓存（自 page_build_system 迁入）
        self._tool_names = []
        self._tool_rows = []
        self._trusted_rows = []
        # 重建工具下拉框期间抑制 on_tool_selected 写回：
        # Adw.ComboRow.set_model 会同步自动选中第 0 项并触发 notify::selected，
        # 不加守卫会把用户已保存的 active tool 冲掉为列表第 1 项。
        self._rebuilding_combo = False

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

        # ---- AI 代理设置（自 Build System 页迁入）----
        self.view.option_enabled.set_active(self.settings.get_value('preferences', 'ai_fix_enabled'))
        self.view.option_enabled.connect('notify::active', self.on_switch_toggled, 'ai_fix_enabled')
        self.view.option_enabled.connect('notify::active', self.on_ai_enabled_toggled)
        self.on_ai_enabled_toggled(self.view.option_enabled, None)

        self.view.option_tool.connect('notify::selected', self.on_tool_selected)
        self._rebuild_tool_combo()

        self.view.option_terminal_cmd.set_text(self.settings.get_value('preferences', 'ai_fix_terminal_cmd') or '')
        self.view.option_terminal_cmd.connect('changed', self.on_terminal_cmd_changed)

        self.view.add_tool_button.connect('clicked', self.on_add_tool_clicked)
        self._rebuild_tools_list()

        self._rebuild_trusted_dirs_list()

        self.view.ai_reset_button.connect('clicked', self.on_ai_reset_clicked)

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

    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

    # ---------- AI 代理设置（自 Build System 页迁入） ----------

    def _get_tools(self):
        '''读 ai_fix_tools；确保是 list（防御旧版 settings 缺失字段）。'''
        tools = self.settings.get_value('preferences', 'ai_fix_tools')
        if not isinstance(tools, list):
            tools = default_tools()
        return tools

    def _save_tools(self, tools):
        self.settings.set_value('preferences', 'ai_fix_tools', tools)

    def _rebuild_tool_combo(self):
        '''同步「当前工具」下拉框选项与当前选中项。

        必须在 set_model 之前先读出 active：set_model 会同步自动选中第 0 项
        并触发 on_tool_selected，若先 set_model 再读会把 active_tool 冲成
        第 1 项（重启后用户选择丢失的根因）。重建期间用 _rebuilding_combo
        守卫抑制写回。
        '''
        tools = self._get_tools()
        self._tool_names = [t.get('name', '?') for t in tools]
        active = self.settings.get_value('preferences', 'ai_fix_active_tool')
        try:
            active_index = self._tool_names.index(active)
        except ValueError:
            active_index = 0 if self._tool_names else Gtk.INVALID_LIST_POSITION

        self._rebuilding_combo = True
        try:
            model = Gtk.StringList()
            for name in self._tool_names:
                model.append(name)
            self.view.option_tool.set_model(model)
            if active_index != Gtk.INVALID_LIST_POSITION:
                self.view.option_tool.set_selected(active_index)
            # 当前激活工具不在列表里（如被删除）→ 回退第一个
            if active not in self._tool_names and self._tool_names:
                self.settings.set_value('preferences', 'ai_fix_active_tool', self._tool_names[0])
        finally:
            self._rebuilding_combo = False

    def _rebuild_tools_list(self):
        '''清空并重建工具列表 group。

        用自维护的 _tool_rows 列表 remove 旧 row，再 append 新 row。
        Adw.PreferencesGroup 的 get_first_child 返回内部 Gtk.Box（非用户行），
        不能直接遍历 remove；故维护列表显式管理（与 welcome_screen 范式一致）。
        '''
        group = self.view.group_tools
        for row in self._tool_rows:
            try:
                group.remove(row)
            except Exception:
                pass
        self._tool_rows = []

        tools = self._get_tools()
        active_name = self.settings.get_value('preferences', 'ai_fix_active_tool')
        builtin_set = set(builtin_names())
        for tool in tools:
            row = self._make_tool_row(tool, active_name, builtin_set)
            group.add(row)
            self._tool_rows.append(row)

    def _make_tool_row(self, tool, active_name, builtin_set):
        name = tool.get('name', '?')
        executable = tool.get('executable', '')
        is_builtin = tool.get('builtin', False) or name in builtin_set
        is_active = (name == active_name)

        row = Adw.ActionRow()
        title = name + ('  ' + _('(active)') if is_active else '')
        row.set_title(title)
        subtitle_parts = [executable] if executable else []
        if is_builtin:
            subtitle_parts.append(_('built-in'))
        else:
            subtitle_parts.append(_('custom'))
        # 检测可用性（同步，subprocess --version 太慢；仅 PATH 检测）
        if agent_runner.check_tool_available(tool):
            subtitle_parts.append(_('installed'))
        else:
            subtitle_parts.append(_('not installed'))
        row.set_subtitle(' · '.join(subtitle_parts))

        # 删除按钮（仅自定义可删）
        if not is_builtin:
            del_btn = Gtk.Button(icon_name='edit-delete-symbolic')
            del_btn.set_has_frame(False)
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class('flat')
            del_btn.set_tooltip_text(_('Remove this custom tool'))
            del_btn.connect('clicked', self.on_delete_tool_clicked, name)
            row.add_suffix(del_btn)
        return row

    def _rebuild_trusted_dirs_list(self):
        '''清空并重建信任目录列表。用自维护 _trusted_rows 列表 remove。'''
        group = self.view.group_trusted
        for row in self._trusted_rows:
            try:
                group.remove(row)
            except Exception:
                pass
        self._trusted_rows = []

        dirs = self.settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
        if not dirs:
            empty = Adw.ActionRow()
            empty.set_title(_('No trusted directories yet'))
            empty.set_subtitle(_('Check "Don\'t ask again" in the preview dialog to add one.'))
            empty.set_sensitive(False)
            group.add(empty)
            self._trusted_rows.append(empty)
            return

        for d in dirs:
            row = Adw.ActionRow()
            row.set_title(d)
            del_btn = Gtk.Button(icon_name='edit-delete-symbolic')
            del_btn.set_has_frame(False)
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class('flat')
            del_btn.set_tooltip_text(_('Remove from trusted list'))
            del_btn.connect('clicked', self.on_delete_trusted_clicked, d)
            row.add_suffix(del_btn)
            group.add(row)
            self._trusted_rows.append(row)

    def on_ai_enabled_toggled(self, switch, pspec):
        enabled = switch.get_active()
        # 启用关闭时所有子项置灰
        for w in (self.view.option_tool, self.view.option_terminal_cmd,
                  self.view.group_tools, self.view.group_add, self.view.group_trusted):
            w.set_sensitive(enabled)

    def on_tool_selected(self, combo, pspec):
        if self._rebuilding_combo:
            return  # set_model 自动选中第 0 项等程序性变更，不写回设置
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION or selected >= len(self._tool_names):
            return
        name = self._tool_names[selected]
        self.settings.set_value('preferences', 'ai_fix_active_tool', name)
        self._rebuild_tools_list()

    def on_terminal_cmd_changed(self, entry):
        self.settings.set_value('preferences', 'ai_fix_terminal_cmd', entry.get_text().strip())

    def on_delete_tool_clicked(self, button, name):
        tools = self._get_tools()
        new_tools = [t for t in tools if t.get('name') != name]
        self._save_tools(new_tools)
        # 若删除的是当前激活工具，回退第一个
        if self.settings.get_value('preferences', 'ai_fix_active_tool') == name:
            if new_tools:
                self.settings.set_value('preferences', 'ai_fix_active_tool', new_tools[0].get('name'))
        self._rebuild_tool_combo()
        self._rebuild_tools_list()

    def on_delete_trusted_clicked(self, button, path):
        dirs = self.settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
        new_dirs = [d for d in dirs if d != path]
        self.settings.set_value('preferences', 'ai_fix_trusted_dirs', new_dirs)
        self.settings.pickle()
        self._rebuild_trusted_dirs_list()

    def on_add_tool_clicked(self, button):
        '''打开「添加自定义工具」对话框。'''
        dialog = AddCustomToolDialog(self.main_window)
        dialog.present_for(on_save=self.on_custom_tool_added)

    def on_custom_tool_added(self, tool_dict):
        '''AddCustomToolDialog 回调：把新工具追加到 ai_fix_tools。'''
        if not tool_dict or not tool_dict.get('name'):
            return
        tools = self._get_tools()
        # 防止重名：若已存在同名工具，覆盖
        for i, t in enumerate(tools):
            if t.get('name') == tool_dict['name']:
                tools[i] = tool_dict
                self._save_tools(tools)
                self._rebuild_tool_combo()
                self._rebuild_tools_list()
                return
        tools.append(tool_dict)
        self._save_tools(tools)
        self._rebuild_tool_combo()
        self._rebuild_tools_list()

    def on_ai_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to Defaults?'),
            body=_('All AI agent settings will be restored to their default values. '
                   'Custom tools and trusted directories will be removed.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_ai_reset_confirmed)

    def on_ai_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'reset':
            defaults = self.settings.defaults['preferences']
            for key in ('ai_fix_enabled', 'ai_fix_active_tool',
                        'ai_fix_terminal_cmd', 'ai_fix_trusted_dirs', 'ai_fix_tools'):
                self.settings.set_value('preferences', key, defaults[key])
            self.settings.pickle()
            self.view.option_enabled.set_active(defaults['ai_fix_enabled'])
            self.view.option_terminal_cmd.set_text(defaults['ai_fix_terminal_cmd'] or '')
            self._rebuild_tool_combo()
            self._rebuild_tools_list()
            self._rebuild_trusted_dirs_list()


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

        # ---- AI 代理设置（自 Build System 页迁入：AI Fix / Agent 终端按钮等
        #      多个入口共用的配置）。置于 Tutorial/Backup 之前——功能配置区
        #      在前，备份/重置等档案性操作统一放页面末尾。----
        group_ai_main = Adw.PreferencesGroup()
        group_ai_main.set_title(_('AI Settings'))
        self.add(group_ai_main)

        self.option_enabled = Adw.SwitchRow()
        self.option_enabled.set_title(_('Enable AI agent'))
        self.option_enabled.set_subtitle(_('Enables AI Fix in the Build Log dialog and the agent terminal button in the header bar'))
        group_ai_main.add(self.option_enabled)

        self.option_tool = Adw.ComboRow()
        self.option_tool.set_title(_('Active agent tool'))
        self.option_tool.set_subtitle(_('Which CLI to invoke for AI features'))
        group_ai_main.add(self.option_tool)

        self.option_terminal_cmd = Adw.EntryRow()
        self.option_terminal_cmd.set_title(_('Terminal command (optional)'))
        self.option_terminal_cmd.set_tooltip_text(
            _('Leave empty to auto-detect (gnome-terminal / xterm / konsole / ...). '
              'Under Flatpak, flatpak-spawn --host is added automatically.'))
        group_ai_main.add(self.option_terminal_cmd)

        self.group_tools = Adw.PreferencesGroup()
        self.group_tools.set_title(_('Agent tools'))
        self.group_tools.set_description(_('Built-in presets cannot be removed. Add a custom tool to integrate other Agent CLIs.'))
        self.add(self.group_tools)

        # 添加按钮：独立 group，直接放入（右对齐），避免重建时被误移除。
        self.group_add = Adw.PreferencesGroup()
        self.add(self.group_add)
        self.add_tool_button = Gtk.Button(label=_('+ Add custom tool'))
        self.add_tool_button.set_halign(Gtk.Align.END)
        self.group_add.add(self.add_tool_button)

        self.group_trusted = Adw.PreferencesGroup()
        self.group_trusted.set_title(_('Trusted directories'))
        self.group_trusted.set_description(_('Directories where the preview dialog is skipped. '
                                              'Click delete to revoke trust for a project.'))
        self.add(self.group_trusted)

        # AI 设置独立重置按钮（通用页自身的 reset_button 不含 AI 项）
        group_ai_reset = Adw.PreferencesGroup()
        self.add(group_ai_reset)

        self.ai_reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.ai_reset_button.set_halign(Gtk.Align.END)
        self.ai_reset_button.add_css_class('destructive-action')
        group_ai_reset.add(self.ai_reset_button)

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

        # reset（通用页偏好）：页面最末尾，紧随 Backup and Restore
        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)


class AddCustomToolDialog(object):
    '''添加自定义 Agent 工具的小弹窗。

    简单表单：name / executable / headed_template。
    模板字段以空格分隔（如 `claude {prompt}`），保存时拆成 list。
    '''

    def __init__(self, main_window):
        self.main_window = main_window
        self.view = Adw.Dialog()
        self.view.set_title(_('Add custom tool'))
        self.view.set_content_width(480)
        self.view.set_content_height(320)

        headerbar = Adw.HeaderBar()
        headerbar.set_show_start_title_buttons(False)
        headerbar.set_show_end_title_buttons(False)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(headerbar)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        toolbar.set_content(content)
        self.view.set_child(toolbar)

        # 取消 / 保存
        self.cancel_button = Gtk.Button(label=_('Cancel'))
        headerbar.pack_start(self.cancel_button)

        self.save_button = Gtk.Button(label=_('Save'))
        self.save_button.add_css_class('suggested-action')
        headerbar.pack_end(self.save_button)

        # 表单字段
        group = Adw.PreferencesGroup()
        content.append(group)

        self.name_entry = Adw.EntryRow()
        self.name_entry.set_title(_('Tool name (unique)'))
        group.add(self.name_entry)

        self.exec_entry = Adw.EntryRow()
        self.exec_entry.set_title(_('Executable (e.g. aider)'))
        group.add(self.exec_entry)

        self.headed_entry = Adw.EntryRow()
        self.headed_entry.set_title(_('Headed command template'))
        self.headed_entry.set_tooltip_text(
            _('Space-separated args. Use {prompt} / {file} / {cwd} as placeholders. '
              'Example: aider --message {prompt} --no-auto-commits. '
              'If left blank or without {prompt}, the prompt is copied to clipboard.'))
        group.add(self.headed_entry)

        self._on_save_cb = None
        self.cancel_button.connect('clicked', lambda b: self.view.close())
        self.save_button.connect('clicked', self._on_save_clicked)

    def present_for(self, on_save):
        self._on_save_cb = on_save
        self.view.present(self.main_window)

    def _on_save_clicked(self, button):
        name = self.name_entry.get_text().strip()
        executable = self.exec_entry.get_text().strip()
        headed_text = self.headed_entry.get_text().strip()

        if not name or not executable:
            # 简单校验失败：保留弹窗让用户改
            return

        tool = {
            'name': name,
            'executable': executable,
            'headed_template': headed_text.split() if headed_text else [executable],
            'builtin': False,
        }
        cb = self._on_save_cb
        self._on_save_cb = None
        self.view.close()
        if cb is not None:
            try:
                cb(tool)
            except Exception:
                pass

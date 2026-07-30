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
# along with this program, if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

import subprocess
import threading

from setzer.app.service_locator import ServiceLocator
from setzer.ai_fix import agent_runner
from setzer.ai_fix.presets import default_tools, builtin_names


class PageBuildSystem(object):

    autoshow_values = ['errors', 'errors_warnings', 'all', 'never']
    shell_values = ['disable', 'restricted', 'enable']

    def __init__(self, preferences, settings):
        self.view = PageBuildSystemView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()
        self.latex_interpreters = list()
        self.latexmk_available = False
        # AI Fix 列表缓存（合并自 page_ai_fix）
        self._tool_names = []
        self._tool_rows = []
        self._trusted_rows = []

    def init(self):
        self.view.option_cleanup_build_files.set_active(self.settings.get_value('preferences', 'cleanup_build_files'))
        self.view.option_cleanup_build_files.connect('notify::active', self.on_switch_toggled, 'cleanup_build_files')

        # LaTeX interpreter combo
        self.view.option_latex_interpreter.connect('notify::selected', self.on_interpreter_selected)

        # Automatically show build log combo
        self.view.option_autoshow_build_log.set_selected(
            self.autoshow_values.index(self.settings.get_value('preferences', 'autoshow_build_log')))
        self.view.option_autoshow_build_log.connect('notify::selected', self.on_autoshow_selected)

        # Embedded system commands combo
        self.view.option_system_commands.set_selected(
            self.shell_values.index(self.settings.get_value('preferences', 'build_option_system_commands')))
        self.view.option_system_commands.connect('notify::selected', self.on_shell_selected)

        # Auto build
        self.view.option_auto_build.set_active(self.settings.get_value('preferences', 'auto_build'))
        self.view.option_auto_build.connect('notify::active', self.on_auto_build_toggled)
        self.view.option_auto_build_delay.set_property('value', self.settings.get_value('preferences', 'auto_build_delay'))
        self.view.option_auto_build_delay.connect('notify::value', self.on_delay_changed, 'auto_build_delay')
        self.update_auto_build_delay_sensitivity()

        # Auto-build error popup：仅在 auto_build 开启时有意义，故 sensitivity 跟随。
        self.view.option_auto_build_autoshow_errors.set_active(
            self.settings.get_value('preferences', 'auto_build_autoshow_errors'))
        self.view.option_auto_build_autoshow_errors.connect(
            'notify::active', self.on_switch_toggled, 'auto_build_autoshow_errors')
        self.update_auto_build_autoshow_errors_sensitivity()

        self.setup_latex_interpreters()

        # AI Fix（合并自 page_ai_fix）
        self.view.option_enabled.set_active(self.settings.get_value('preferences', 'ai_fix_enabled'))
        self.view.option_enabled.connect('notify::active', self.on_switch_toggled, 'ai_fix_enabled')
        self.view.option_enabled.connect('notify::active', self.on_enabled_toggled)
        self.on_enabled_toggled(self.view.option_enabled, None)

        self.view.option_tool.connect('notify::selected', self.on_tool_selected)
        self._rebuild_tool_combo()

        self.view.option_terminal_cmd.set_text(self.settings.get_value('preferences', 'ai_fix_terminal_cmd') or '')
        self.view.option_terminal_cmd.connect('changed', self.on_terminal_cmd_changed)

        self.view.add_tool_button.connect('clicked', self.on_add_tool_clicked)
        self._rebuild_tools_list()

        self._rebuild_trusted_dirs_list()

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    def on_auto_build_toggled(self, switch, pspec):
        value = switch.get_active()
        self.settings.set_value('preferences', 'auto_build', value)
        self.update_auto_build_delay_sensitivity()
        self.update_auto_build_autoshow_errors_sensitivity()

    def on_delay_changed(self, spin, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, float(spin.get_property('value')))

    def update_auto_build_delay_sensitivity(self):
        self.view.option_auto_build_delay.set_sensitive(self.view.option_auto_build.get_active())

    def update_auto_build_autoshow_errors_sensitivity(self):
        # auto_build 关闭时此设置无意义（不会触发自动构建），置灰避免误导。
        self.view.option_auto_build_autoshow_errors.set_sensitive(
            self.view.option_auto_build.get_active())

    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

    def on_interpreter_selected(self, combo, pspec):
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        interpreter = self.latex_interpreters[selected]
        self.settings.set_value('preferences', 'latex_interpreter', interpreter)
        self.update_tectonic_element_visibility()

    def on_autoshow_selected(self, combo, pspec):
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        self.settings.set_value('preferences', 'autoshow_build_log', self.autoshow_values[selected])

    def on_shell_selected(self, combo, pspec):
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        self.settings.set_value('preferences', 'build_option_system_commands', self.shell_values[selected])

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset to Defaults?'),
            body=_('All build system and AI Fix settings will be restored to their default values. '
                   'Custom tools and trusted directories will be removed.'))
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
            self.view.option_cleanup_build_files.set_active(defaults['cleanup_build_files'])
            self.view.option_autoshow_build_log.set_selected(
                self.autoshow_values.index(defaults['autoshow_build_log']))
            self.view.option_system_commands.set_selected(
                self.shell_values.index(defaults['build_option_system_commands']))
            self.view.option_auto_build.set_active(defaults['auto_build'])
            self.view.option_auto_build_delay.set_property('value', defaults['auto_build_delay'])
            self.view.option_auto_build_autoshow_errors.set_active(defaults['auto_build_autoshow_errors'])
            if self.latexmk_available:
                self.view.option_use_latexmk.set_active(defaults['use_latexmk'])

            # AI Fix 合并重置
            for key in ('ai_fix_enabled', 'ai_fix_active_tool',
                        'ai_fix_terminal_cmd', 'ai_fix_trusted_dirs', 'ai_fix_tools'):
                self.settings.set_value('preferences', key, defaults[key])
            self.settings.pickle()
            self.view.option_enabled.set_active(defaults['ai_fix_enabled'])
            self.view.option_terminal_cmd.set_text(defaults['ai_fix_terminal_cmd'] or '')
            self._rebuild_tool_combo()
            self._rebuild_tools_list()
            self._rebuild_trusted_dirs_list()

    def setup_latex_interpreters(self):
        # 异步检测：5 个 subprocess（xelatex/pdflatex/lualatex/tectonic/latexmk
        # --version）串行执行约 250–750ms。原实现同步阻塞主线程，打开 Preferences
        # 时窗口冻结。改为后台线程检测，完成后 idle 回主线程更新 UI。
        # 检测期间解释器选择器暂时不可见（保持初始空状态）。
        threading.Thread(target=self._detect_interpreters, daemon=True).start()

    def _detect_interpreters(self):
        '''后台线程：检测可用 LaTeX 解释器和 latexmk。

        只关心 returncode，从不读取 stdout。原用 PIPE 但不 communicate()：
        若 --version 输出填满管道缓冲（约 64KB，tectonic 等会打印冗长版本
        信息），子进程阻塞在写管道上，process.wait() 永久挂起——后台线程
        泄漏且 UI 永远等不到检测结果。改用 DEVNULL 让内核直接丢弃输出，
        既消除死锁风险又省去管道分配。配合 PreferencesDialog 的视图缓存
        （init 只在首次打开执行一次），整个会话仅检测一次。
        '''
        latex_interpreters = []
        for interpreter in ['xelatex', 'pdflatex', 'lualatex', 'tectonic']:
            try:
                process = subprocess.Popen([interpreter, '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                pass
            else:
                process.wait()
                if process.returncode == 0:
                    latex_interpreters.append(interpreter)

        latexmk_available = False
        try:
            process = subprocess.Popen(['latexmk', '--version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        else:
            process.wait()
            latexmk_available = (process.returncode == 0)

        GLib.idle_add(self._apply_interpreter_results, latex_interpreters, latexmk_available)

    def _apply_interpreter_results(self, latex_interpreters, latexmk_available):
        '''主线程 idle 回调：用检测结果更新 UI。'''
        self.latex_interpreters = latex_interpreters
        self.latexmk_available = latexmk_available

        if len(self.latex_interpreters) == 0:
            self.view.no_interpreter_label.set_visible(True)
            self.view.option_latex_interpreter.set_visible(False)
        else:
            self.view.no_interpreter_label.set_visible(False)
            self.view.option_latex_interpreter.set_visible(True)
            if self.settings.get_value('preferences', 'latex_interpreter') not in self.latex_interpreters:
                self.settings.set_value('preferences', 'latex_interpreter', self.latex_interpreters[0])

            if self.latexmk_available:
                self.view.option_use_latexmk.set_visible(True)
            else:
                self.view.option_use_latexmk.set_visible(False)
                self.settings.set_value('preferences', 'use_latexmk', False)
            self.view.option_use_latexmk.set_active(self.settings.get_value('preferences', 'use_latexmk'))
            self.view.option_use_latexmk.connect('notify::active', self.on_switch_toggled, 'use_latexmk')

            # 填充 interpreter 下拉列表
            string_list = Gtk.StringList()
            for interpreter in self.latex_interpreters:
                string_list.append(interpreter)
            self.view.option_latex_interpreter.set_model(string_list)
            current = self.settings.get_value('preferences', 'latex_interpreter')
            self.view.option_latex_interpreter.set_selected(self.latex_interpreters.index(current))

            self.update_tectonic_element_visibility()
        return False

    def update_tectonic_element_visibility(self):
        selected = self.view.option_latex_interpreter.get_selected()
        tectonic_active = (selected != Gtk.INVALID_LIST_POSITION and
                           self.latex_interpreters[selected] == 'tectonic')
        if tectonic_active:
            self.view.tectonic_warning_label.set_visible(True)
            self.view.option_use_latexmk.set_visible(False)
            self.view.option_system_commands.set_visible(False)
        else:
            self.view.tectonic_warning_label.set_visible(False)
            self.view.option_use_latexmk.set_visible(True)
            self.view.option_system_commands.set_visible(True)

    # ---------- AI Fix（合并自 page_ai_fix） ----------

    def _get_tools(self):
        '''读 ai_fix_tools；确保是 list（防御旧版 settings 缺失字段）。'''
        tools = self.settings.get_value('preferences', 'ai_fix_tools')
        if not isinstance(tools, list):
            tools = default_tools()
        return tools

    def _save_tools(self, tools):
        self.settings.set_value('preferences', 'ai_fix_tools', tools)

    def _rebuild_tool_combo(self):
        '''同步「当前工具」下拉框选项与当前选中项。'''
        tools = self._get_tools()
        self._tool_names = [t.get('name', '?') for t in tools]
        model = Gtk.StringList()
        for name in self._tool_names:
            model.append(name)
        self.view.option_tool.set_model(model)
        active = self.settings.get_value('preferences', 'ai_fix_active_tool')
        try:
            self.view.option_tool.set_selected(self._tool_names.index(active))
        except ValueError:
            # 当前激活工具不在列表里（如被删除）→ 回退第一个
            if self._tool_names:
                self.view.option_tool.set_selected(0)
                self.settings.set_value('preferences', 'ai_fix_active_tool', self._tool_names[0])

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

    # ---------- AI Fix 信号回调 ----------

    def on_enabled_toggled(self, switch, pspec):
        enabled = switch.get_active()
        # 启用关闭时所有子项置灰（与 page_build_system 的 auto_build 联动范式一致）
        for w in (self.view.option_tool, self.view.option_terminal_cmd,
                  self.view.group_tools, self.view.group_add, self.view.group_trusted):
            w.set_sensitive(enabled)

    def on_tool_selected(self, combo, pspec):
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


class PageBuildSystemView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('Build System'))
        self.set_icon_name('system-run-symbolic')

        group_interpreter = Adw.PreferencesGroup()
        group_interpreter.set_title(_('LaTeX Interpreter'))
        self.add(group_interpreter)

        self.no_interpreter_label = Gtk.Label()
        self.no_interpreter_label.set_wrap(True)
        running_under_flatpak = False
        try:
            gi.require_version('Xdp', '1.0')
            from gi.repository import Xdp
            running_under_flatpak = Xdp.Portal().running_under_flatpak()
        except (ValueError, ImportError):
            # Xdp (xdg-desktop-portal) GI namespace not available in this runtime.
            pass
        if running_under_flatpak:
            self.no_interpreter_label.set_markup(_('''No LaTeX interpreter found. To install interpreters in Flatpak, open a terminal and run the following command:
flatpak install org.freedesktop.Sdk.Extension.texlive'''))
        else:
            self.no_interpreter_label.set_markup(_('No LaTeX interpreter found. For instructions on installing LaTeX see <a href="https://en.wikibooks.org/wiki/LaTeX/Installation">https://en.wikibooks.org/wiki/LaTeX/Installation</a>'))
        self.no_interpreter_label.set_xalign(0)
        group_interpreter.add(self.no_interpreter_label)

        self.option_latex_interpreter = Adw.ComboRow()
        self.option_latex_interpreter.set_title(_('Interpreter'))
        group_interpreter.add(self.option_latex_interpreter)

        self.tectonic_warning_label = Gtk.Label()
        self.tectonic_warning_label.set_wrap(True)
        self.tectonic_warning_label.set_markup(_('Please note: the Tectonic backend uses only the V1 command-line interface. Tectonic.toml configuration files are ignored. For custom build configuration, switch to another interpreter (see the <a href="https://tectonic-typesetting.github.io/tectonic/">Tectonic documentation</a>).'))
        self.tectonic_warning_label.set_xalign(0)
        self.tectonic_warning_label.add_css_class('caption')
        group_interpreter.add(self.tectonic_warning_label)

        group_options = Adw.PreferencesGroup()
        group_options.set_title(_('Options'))
        self.add(group_options)

        self.option_cleanup_build_files = Adw.SwitchRow()
        self.option_cleanup_build_files.set_title(_('Automatically remove helper files (.log, .dvi, …) after building .pdf.'))
        group_options.add(self.option_cleanup_build_files)

        self.option_use_latexmk = Adw.SwitchRow()
        self.option_use_latexmk.set_title(_('Use latexmk'))
        group_options.add(self.option_use_latexmk)

        group_auto_build = Adw.PreferencesGroup()
        group_auto_build.set_title(_('Auto Build'))
        self.add(group_auto_build)

        self.option_auto_build = Adw.SwitchRow()
        self.option_auto_build.set_title(_('Automatically build and save after changes'))
        self.option_auto_build.set_subtitle(_('When you stop typing, the document is saved and rebuilt after a short delay.'))
        group_auto_build.add(self.option_auto_build)

        self.option_auto_build_delay = Adw.SpinRow()
        self.option_auto_build_delay.set_title(_('Delay (seconds)'))
        self.option_auto_build_delay.set_digits(1)
        self.option_auto_build_delay.set_subtitle(_('Shorter delays update the preview faster but use more CPU while typing.'))
        adjustment_auto_build = Gtk.Adjustment(value=2, lower=0.5, upper=30, step_increment=0.5)
        self.option_auto_build_delay.set_adjustment(adjustment_auto_build)
        group_auto_build.add(self.option_auto_build_delay)

        # 自动构建报错时是否弹出构建日志弹窗。与上方 autoshow_build_log 不同：
        # autoshow_build_log 控制所有构建路径的日志显示阈值（errors/warnings/all）；
        # 此开关仅作用于自动构建路径——用户打字途中触发自动构建，文档可能尚未
        # 输完导致报错，频繁弹窗打扰写作。关闭后自动构建报错不再弹窗，但手动
        # 构建（F5/F6）仍遵循 autoshow_build_log。
        self.option_auto_build_autoshow_errors = Adw.SwitchRow()
        self.option_auto_build_autoshow_errors.set_title(_('Pop up build log on auto-build errors'))
        self.option_auto_build_autoshow_errors.set_subtitle(
            _('When auto-build is on, automatically show the build log when errors occur. '
              'Disable to avoid interruptions while typing.'))
        group_auto_build.add(self.option_auto_build_autoshow_errors)

        group_build_log = Adw.PreferencesGroup()
        group_build_log.set_title(_('Automatically show build log'))
        self.add(group_build_log)

        self.option_autoshow_build_log = Adw.ComboRow()
        self.option_autoshow_build_log.set_title(_('Show build log'))
        autoshow_model = Gtk.StringList()
        for label in [_('.. only when errors occurred.'),
                      _('.. on errors and warnings.'),
                      _('.. on errors, warnings and badboxes.'),
                      _('.. never.')]:
            autoshow_model.append(label)
        self.option_autoshow_build_log.set_model(autoshow_model)
        group_build_log.add(self.option_autoshow_build_log)

        group_shell_escape = Adw.PreferencesGroup()
        group_shell_escape.set_title(_('Embedded system commands'))
        self.add(group_shell_escape)

        shell_warning_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        shell_warning_box.set_margin_start(12)
        shell_warning_box.set_margin_end(12)
        shell_warning_box.set_margin_top(10)
        shell_warning_box.set_margin_bottom(4)
        shell_warning_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')
        shell_warning_icon.set_valign(Gtk.Align.START)
        shell_warning_icon.set_margin_top(2)
        shell_warning_icon.add_css_class('warning')
        shell_warning_label = Gtk.Label()
        shell_warning_label.set_wrap(True)
        shell_warning_label.set_markup(_('Warning: enable this only if you have to. It can cause security problems when building files from untrusted sources.'))
        shell_warning_label.set_xalign(0)
        shell_warning_label.add_css_class('warning')
        shell_warning_box.append(shell_warning_icon)
        shell_warning_box.append(shell_warning_label)
        group_shell_escape.add(shell_warning_box)

        self.option_system_commands = Adw.ComboRow()
        self.option_system_commands.set_title(_('System commands'))
        shell_model = Gtk.StringList()
        for label in [_('Disable') + ' (' + _('recommended') + ')',
                      _('Enable restricted \\write18{SHELL COMMAND}'),
                      _('Fully enable \\write18{SHELL COMMAND}')]:
            shell_model.append(label)
        self.option_system_commands.set_model(shell_model)
        group_shell_escape.add(self.option_system_commands)

        # ---- AI Fix（合并自 page_ai_fix：服务于构建报错修复） ----
        group_ai_main = Adw.PreferencesGroup()
        group_ai_main.set_title(_('AI Fix'))
        self.add(group_ai_main)

        self.option_enabled = Adw.SwitchRow()
        self.option_enabled.set_title(_('Enable AI Fix'))
        self.option_enabled.set_subtitle(_('Show "AI Fix" buttons in the Build Log dialog'))
        group_ai_main.add(self.option_enabled)

        self.option_tool = Adw.ComboRow()
        self.option_tool.set_title(_('Active agent tool'))
        self.option_tool.set_subtitle(_('Which CLI to invoke when clicking AI Fix'))
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

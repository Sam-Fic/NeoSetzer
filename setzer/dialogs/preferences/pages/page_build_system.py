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
from gi.repository import Gtk, Adw, GLib

import subprocess
import threading

from setzer.app.service_locator import ServiceLocator


class PageBuildSystem(object):

    autoshow_values = ['errors', 'errors_warnings', 'all']
    shell_values = ['disable', 'restricted', 'enable']

    def __init__(self, preferences, settings):
        self.view = PageBuildSystemView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()
        self.latex_interpreters = list()
        self.latexmk_available = False

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

        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    def on_auto_build_toggled(self, switch, pspec):
        value = switch.get_active()
        self.settings.set_value('preferences', 'auto_build', value)
        self.update_auto_build_delay_sensitivity()
        self.update_auto_build_autoshow_errors_sensitivity()

    def on_delay_changed(self, spin, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, int(spin.get_property('value')))

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
            body=_('All build system settings will be restored to their default values.'))
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
        self.tectonic_warning_label.set_markup(_('Please note: the Tectonic backend uses only the V1 command-line interface. Tectonic.toml configuration files are ignored.'))
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
        self.option_use_latexmk.set_title(_('Use Latexmk'))
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
        adjustment_auto_build = Gtk.Adjustment(value=2, lower=1, upper=10, step_increment=1)
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
                      _('.. on errors, warnings and badboxes.')]:
            autoshow_model.append(label)
        self.option_autoshow_build_log.set_model(autoshow_model)
        group_build_log.add(self.option_autoshow_build_log)

        group_shell_escape = Adw.PreferencesGroup()
        group_shell_escape.set_title(_('Embedded system commands'))
        self.add(group_shell_escape)

        label_explainer = Gtk.Label()
        label_explainer.set_wrap(True)
        label_explainer.set_markup(_('Warning: enable this only if you have to. It can cause security problems when building files from untrusted sources.'))
        label_explainer.set_xalign(0)
        label_explainer.add_css_class('caption')
        # 与 Adw.PreferencesRow 内边距对齐，避免裸 Label 贴边
        label_explainer.set_margin_start(12)
        label_explainer.set_margin_end(12)
        label_explainer.set_margin_top(10)
        label_explainer.set_margin_bottom(4)
        group_shell_escape.add(label_explainer)

        self.option_system_commands = Adw.ComboRow()
        self.option_system_commands.set_title(_('System commands'))
        shell_model = Gtk.StringList()
        for label in [_('Disable') + ' (' + _('recommended') + ')',
                      _('Enable restricted \\write18{SHELL COMMAND}'),
                      _('Fully enable \\write18{SHELL COMMAND}')]:
            shell_model.append(label)
        self.option_system_commands.set_model(shell_model)
        group_shell_escape.add(self.option_system_commands)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

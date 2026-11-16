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
# along with this program, if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

import subprocess
import sys
import threading

from setzer.app.service_locator import ServiceLocator


class PageBuildSystem(object):

    autoshow_values = ['errors', 'errors_warnings', 'all', 'never']
    shell_values = ['disable', 'restricted', 'enable']
    output_chain_values = ['pdf', 'pdfps']

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

        self.view.option_enable_synctex.set_active(self.settings.get_value('preferences', 'enable_synctex'))
        self.view.option_enable_synctex.connect('notify::active', self.on_switch_toggled, 'enable_synctex')

        self.view.option_phitex_live_build.set_active(
            self.settings.get_value('preferences', 'phitex_live_build'))
        self.view.option_phitex_live_build.connect(
            'notify::active', self.on_switch_toggled, 'phitex_live_build')

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

        # 输出链（issue #223，仅 latexmk 生效）与个人 TeX 树（issue #182）
        output_chain = self.settings.get_value('preferences', 'latexmk_output_chain')
        if output_chain not in self.output_chain_values:
            output_chain = 'pdf'
        self.view.option_output_chain.set_selected(self.output_chain_values.index(output_chain))
        self.view.option_output_chain.connect('notify::selected', self.on_output_chain_selected)
        self.view.option_texmf_home.set_text(self.settings.get_value('preferences', 'texmf_home') or '')
        # set_text 在 connect 之前调用：初始化回填不触发写入
        self.view.option_texmf_home.connect('changed', self.on_texmf_home_changed)

        # Auto-build error popup：仅在 auto_build 开启时有意义，故 sensitivity 跟随。
        self.view.option_auto_build_autoshow_errors.set_active(
            self.settings.get_value('preferences', 'auto_build_autoshow_errors'))
        self.view.option_auto_build_autoshow_errors.connect(
            'notify::active', self.on_switch_toggled, 'auto_build_autoshow_errors')
        self.update_auto_build_autoshow_errors_sensitivity()

        self.setup_latex_interpreters()

        self.view.copy_phitex_prompt_button.connect(
            'clicked', self.on_copy_install_prompt_clicked)

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
        self.update_engine_specific_visibility()

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

    def on_output_chain_selected(self, combo, pspec):
        selected = combo.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return
        self.settings.set_value('preferences', 'latexmk_output_chain', self.output_chain_values[selected])

    def on_texmf_home_changed(self, entry):
        self.settings.set_value('preferences', 'texmf_home', entry.get_text().strip())

    def update_output_chain_sensitivity(self):
        # 输出链只在 latexmk 路线下有意义（直接引擎路线硬编码直出 PDF）
        self.view.option_output_chain.set_sensitive(self.view.option_use_latexmk.get_active())

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
            self.view.option_output_chain.set_selected(
                self.output_chain_values.index(defaults['latexmk_output_chain']))
            self.view.option_phitex_live_build.set_active(defaults['phitex_live_build'])
            # set_text 触发 changed 回调 → 自动写回设置
            self.view.option_texmf_home.set_text(defaults['texmf_home'])

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
        # Windows 上设 CREATE_NO_WINDOW 避免弹出控制台窗口
        popen_kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        for interpreter in ['xelatex', 'pdflatex', 'lualatex', 'tectonic', 'phitex']:
            try:
                process = subprocess.Popen([interpreter, '--version'], **popen_kwargs)
            except FileNotFoundError:
                pass
            else:
                process.wait()
                if process.returncode == 0:
                    latex_interpreters.append(interpreter)

        latexmk_available = False
        try:
            process = subprocess.Popen(['latexmk', '--version'], **popen_kwargs)
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
                self.view.option_output_chain.set_visible(True)
            else:
                self.view.option_use_latexmk.set_visible(False)
                self.view.option_output_chain.set_visible(False)
                self.settings.set_value('preferences', 'use_latexmk', False)
            self.view.option_use_latexmk.set_active(self.settings.get_value('preferences', 'use_latexmk'))
            self.view.option_use_latexmk.connect('notify::active', self.on_switch_toggled, 'use_latexmk')
            # 输出链置灰跟随 latexmk 开关（顺序：先同步一次初值，再挂回调）
            self.update_output_chain_sensitivity()
            self.view.option_use_latexmk.connect('notify::active', lambda *args: self.update_output_chain_sensitivity())

            # 填充 interpreter 下拉列表
            string_list = Gtk.StringList()
            for interpreter in self.latex_interpreters:
                string_list.append(interpreter)
            self.view.option_latex_interpreter.set_model(string_list)
            current = self.settings.get_value('preferences', 'latex_interpreter')
            self.view.option_latex_interpreter.set_selected(self.latex_interpreters.index(current))

            self.update_engine_specific_visibility()
        return False

    def update_engine_specific_visibility(self):
        selected = self.view.option_latex_interpreter.get_selected()
        interpreter = (None if selected == Gtk.INVALID_LIST_POSITION
                       else self.latex_interpreters[selected])
        phitex_available = 'phitex' in self.latex_interpreters
        # 没装才给安装入口；一个解释器都没检测到时（下拉框隐藏、列表为空），
        # 也要能走到这里，否则 install row 永远出不来。
        self.view.install_phitex_row.set_visible(not phitex_available)
        if interpreter is None:
            return

        tectonic_active = (interpreter == 'tectonic')
        phitex_active = (interpreter == 'phitex')
        self.view.tectonic_warning_label.set_visible(tectonic_active)
        self.view.phitex_note_label.set_visible(phitex_active)
        self.view.option_phitex_live_build.set_visible(phitex_active)
        self.view.option_system_commands.set_visible(not tectonic_active)
        # latexmk 路线与其输出链对两个自带编排的引擎都没有意义：tectonic
        # 走自己的构建图，phitex 一次调用即跑到不动点（BibTeX/biber/makeindex
        # 在进程内）。构建时 builder 也按引擎分支跳过 latexmk，与此处的
        # 隐藏一致，不会出现在开关开着但实际没生效的错觉。
        hide_latexmk = tectonic_active or phitex_active
        self.view.option_use_latexmk.set_visible(not hide_latexmk)
        # tectonic 不参与 latexmk 路线；latexmk 不可用时同样隐藏
        self.view.option_output_chain.set_visible(
            self.latexmk_available and not hide_latexmk)

        # PhiTeX 常驻 session 下这几项不再由用户决定，置灰而不是隐藏：
        # 隐藏会让人以为设置丢了，置灰配上原因能读出"这个引擎接管了它"。
        # 实际行为见 AutoBuild._auto_build_values 与 run_via_phitex_session。
        self._set_row_state(
            self.view.option_auto_build, not phitex_active,
            _('PhiTeX rebuilds as you type, which needs each edit written to disk first — auto build is always on for it.'))
        self._set_row_state(
            self.view.option_auto_build_delay, not phitex_active,
            _('PhiTeX uses a short fixed delay: an incremental rebuild takes about 20 ms, so a longer wait only delays the preview.'))
        self._set_row_state(
            self.view.option_cleanup_build_files, not phitex_active,
            _('A running PhiTeX session needs the helper files it writes (.aux, .toc, .bbl) to know what an edit affects, so they are kept.'))
        # PhiTeX live 下这条已被无条件关掉（build_log.update_items），留着可点
        # 就是"能打开但不生效"，与其它接管项同样处理。
        self._set_row_state(
            self.view.option_auto_build_autoshow_errors, not phitex_active,
            _('With a PhiTeX session the build log never pops up on its own: errors show as squiggles in the text and highlighted areas in the preview.'))
        # 这几项对 PhiTeX 有效，但只在 watch 启动那一刻读入；改动会结束当前
        # session，下一轮构建按新设置重起（付一次完整构建），见 config_signature。
        restart_note = _('Changing this ends the running PhiTeX session; the next build starts a new one with the new setting.')
        for row in (self.view.option_enable_synctex, self.view.option_system_commands):
            row.set_tooltip_text(restart_note if phitex_active else None)
        self.view.option_texmf_home.set_tooltip_text(restart_note if phitex_active else None)

    @staticmethod
    def _set_row_state(row, enabled, reason_when_disabled):
        row.set_sensitive(enabled)
        row.set_tooltip_text(None if enabled else reason_when_disabled)

    def on_copy_install_prompt_clicked(self, button):
        '''把安装 PhiTeX 的说明复制进剪贴板，交给用户的 AI Agent 执行。

        提示词只写意图、不写命令：具体下载哪个平台的包、怎么校验完整性、装到
        哪儿，让 Agent 自己查 PhiTeX 仓库——这里写死 URL 和哈希会随版本漂移，
        过时后反而误导。NeoSetzer 自己不下载、不执行远程脚本。
        '''
        clipboard = Gdk.Display.get_default().get_clipboard()
        # 刻意只讲意图和约束、不给命令：命令、URL、校验和写死在这里就会随
        # PhiTeX 版本漂移，过时后是误导而不是帮助。约束留三条，都是这次适配
        # 实测踩到的：装进用户 PATH（免 sudo，否则每次构建弹权限）、装完要能
        # 验证、不要塞进 NeoSetzer 的包（AGPL-3.0-only 与 GPL-3 的打包纠缠）。
        clipboard.set_content(Gdk.ContentProvider.new_for_value(_(
            'Install PhiTeX on this computer for me: https://github.com/accl-kaust/PhiTeX '
            '— a separately built TeX engine I want to use as the LaTeX interpreter in '
            'NeoSetzer. Choose whatever is appropriate for this operating system, verify '
            'what you download, and install it so that `phitex` is on PATH for my user '
            'account without sudo and without writing into system directories. Then check '
            'it works and show me what you did. Do not compile PhiTeX into NeoSetzer or '
            'bundle it in a NeoSetzer package.')))
        # 就地反馈：偏好对话框没有 toast overlay，换图标比弹新窗口打扰小。
        button.set_icon_name('object-select-symbolic')
        GLib.timeout_add_seconds(2, self._reset_copy_button, button)

    def _reset_copy_button(self, button):
        button.set_icon_name('edit-copy-symbolic')
        return False


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

        self.phitex_note_label = Gtk.Label()
        self.phitex_note_label.set_wrap(True)
        self.phitex_note_label.set_markup(_('PhiTeX builds once to a fixpoint (BibTeX, biber and makeindex included), so the latexmk options below do not apply. Its PDF is byte-identical to pdfLaTeX\u2019s, but LuaLaTeX is not supported yet. Editing stays instant only while a PhiTeX session is running; without one, use another interpreter for the fastest full rebuilds.'))
        self.phitex_note_label.set_xalign(0)
        self.phitex_note_label.add_css_class('caption')
        group_interpreter.add(self.phitex_note_label)

        # 未检测到 phitex 时才出现（由 update_engine_specific_visibility 控制）：
        # 提供一段可复制给 AI Agent 的安装提示词，而不是让 NeoSetzer 自己去
        # 下载执行远程脚本。
        self.install_phitex_row = Adw.ActionRow()
        self.install_phitex_row.set_title(_('Install PhiTeX'))
        self.install_phitex_row.set_subtitle(_('Not found on this machine. Copy the instructions and hand them to an AI agent — it will fetch and verify the release for your platform.'))
        self.copy_phitex_prompt_button = Gtk.Button()
        self.copy_phitex_prompt_button.set_icon_name('edit-copy-symbolic')
        self.copy_phitex_prompt_button.set_valign(Gtk.Align.CENTER)
        self.copy_phitex_prompt_button.set_tooltip_text(_('Copy the installation instructions to the clipboard'))
        self.install_phitex_row.add_suffix(self.copy_phitex_prompt_button)
        self.install_phitex_row.set_activatable_widget(self.copy_phitex_prompt_button)
        # 同上：检测结果回来之前先藏起来。
        self.install_phitex_row.set_visible(False)
        group_interpreter.add(self.install_phitex_row)

        group_options = Adw.PreferencesGroup()
        group_options.set_title(_('Options'))
        self.add(group_options)

        self.option_cleanup_build_files = Adw.SwitchRow()
        self.option_cleanup_build_files.set_title(_('Automatically remove helper files (.log, .dvi, …) after building .pdf.'))
        group_options.add(self.option_cleanup_build_files)

        self.option_use_latexmk = Adw.SwitchRow()
        self.option_use_latexmk.set_title(_('Use latexmk'))
        group_options.add(self.option_use_latexmk)

        self.option_phitex_live_build = Adw.SwitchRow()
        self.option_phitex_live_build.set_title(_('Keep a PhiTeX session running'))
        self.option_phitex_live_build.set_subtitle(_('Rebuild only what the edit affects by reusing one running PhiTeX process per document. Turning this off re-starts PhiTeX for every build, which recompiles the whole document each time.'))
        # 仅对 PhiTeX 有意义，初值隐藏，由 update_engine_specific_visibility
        # 在检测结果回来后按需显示（与 option_use_latexmk 的处理一致）。
        self.option_phitex_live_build.set_visible(False)
        group_options.add(self.option_phitex_live_build)

        # 输出链（上游 issue #223）：仅 latexmk 路线可切换。'pdfps' 让 latexmk
        # 编排 latex → dvips → ps2pdf，PSTricks/psfrag 等只在 PostScript 阶段
        # 生效的宏包才能工作；最终产物仍是 PDF，预览不受影响。
        self.option_output_chain = Adw.ComboRow()
        self.option_output_chain.set_title(_('Output chain'))
        self.option_output_chain.set_subtitle(_('PDF via PostScript runs latex → dvips → ps2pdf. Needed by PSTricks/psfrag; your interpreter choice is ignored on this route.'))
        output_chain_model = Gtk.StringList()
        for label in [_('Direct PDF'),
                      _('PDF via PostScript (DVI → dvips → ps2pdf)')]:
            output_chain_model.append(label)
        self.option_output_chain.set_model(output_chain_model)
        group_options.add(self.option_output_chain)

        self.option_enable_synctex = Adw.SwitchRow()
        self.option_enable_synctex.set_title(_('Generate SyncTeX for preview sync'))
        self.option_enable_synctex.set_subtitle(_('Enables forward/reverse sync between editor and PDF preview. Disabling makes builds slightly faster.'))
        group_options.add(self.option_enable_synctex)

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

        # —— 个人 TeX 树（上游 issue #182）——
        group_texmf = Adw.PreferencesGroup()
        group_texmf.set_title(_('Personal TeX tree'))
        group_texmf.set_description(_('Directory with your own packages, classes and bibliography styles (kpathsea TEXMFHOME). Applied to every build; leave empty to use your environment or the TeX Live default (~/texmf).'))
        self.add(group_texmf)

        self.option_texmf_home = Adw.EntryRow()
        self.option_texmf_home.set_title(_('TEXMFHOME directory'))
        self.option_texmf_home.set_tooltip_text(_('A leading "~" is expanded to your home folder. Example: ~/.texmf'))
        group_texmf.add(self.option_texmf_home)

        group_reset = Adw.PreferencesGroup()
        self.add(group_reset)

        self.reset_button = Gtk.Button(label=_('Reset to Defaults'))
        self.reset_button.set_halign(Gtk.Align.END)
        self.reset_button.add_css_class('destructive-action')
        group_reset.add(self.reset_button)

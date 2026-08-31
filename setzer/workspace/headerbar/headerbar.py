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
# along with this program. If not, see <http://www.gnu.org/licenses/>

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Adw

import os.path

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.popovers.popover_manager import PopoverManager


class HeaderBar(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().headerbar

        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('update_recently_opened_documents', self.on_update_recently_opened_documents)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        # Build log 标题栏副本：show_build_log 状态变化时同步 active 态。
        self.workspace.connect('show_build_log_state_change', self.on_show_build_log_state_change)
        # show_shortcuts_bar 设置变化时显隐 build_log_toggle。
        self.workspace.settings.connect('settings_changed', self.on_settings_changed)

        # Initialize the correct Open button visibility now. The signal may
        # have been emitted before this controller was constructed, leaving
        # both buttons visible by default.
        self.on_update_recently_opened_documents(None, self.workspace.recently_opened_documents)

        self.activate_welcome_screen_mode()

        # 标题栏 Build log 按钮的点击处理：转发到 workspace.set_show_build_log，
        # 触发统一的 present/close 弹窗逻辑（见 workspace_presenter.update_build_log_visibility）。
        self.view.build_log_toggle.connect('clicked', self.on_build_log_toggle_clicked)
        # 初始 active 态同步为当前 workspace 状态。
        self.view.build_log_toggle.set_active(self.workspace.get_show_build_log())
        # 初始显隐根据 show_shortcuts_bar 设置决定。
        self.update_build_log_toggle_visibility()

        # 「在终端中打开 Agent」按钮：裸启动 AI Fix 配置的 Agent CLI。
        self.view.agent_terminal_button.connect('clicked', self.on_agent_terminal_clicked)

        # Compact 模式：窄窗（<700px breakpoint）时隐藏 save / help 按钮（有 Ctrl+S、
        # F1 兜底），缓解 headerbar 在 360px 下的按钮溢出。不能直接用
        # Adw.Breakpoint.add_setter(visible)：本 presenter 频繁 set_visible 这些按钮
        # （welcome/document 模式切换、show/hide_*_toggles），add_setter 会被覆盖。
        # 通过 _compact 标志在 show 路径末尾覆盖隐藏；set_compact 重跑当前模式生效/恢复。
        # F1/F9 直接操作 toggle 的 set_active（见 shortcut_controller_app），不受
        # set_visible 影响，故隐藏 help_toggle 不会困住用户。
        self._compact = False
        # 当前活动文档的 build_system 'build_state' 信号 handler id + 文档引用。
        # 切换文档时重新挂接；_disconnect_build_state_signal 基于 _build_state_doc
        # 安全断开（不能直接读 workspace.active_document，因为 on_new_active_document
        # 触发时它已是新文档）。
        self._build_state_handler_id = None
        self._build_state_doc = None
        # HeaderBar 在 workspace 启动后才构造；此时若已有活动文档，
        # 'new_active_document' 信号不会再次触发，需要手动挂 build_state 监听。
        # 与 shortcutsbar.__init__ 的处理方式保持对称。
        initial_doc = self.workspace.active_document
        if initial_doc is not None and initial_doc.is_latex_document():
            self._build_state_handler_id = initial_doc.build_system.connect('build_state', self.on_build_state)
            self._build_state_doc = initial_doc
            # 启动时若已有活动文档，按其当前编译结果初始化错误样式
            # （例如会话恢复了一个上次编译报错的文档），避免延后到下次构建才同步。
            self._refresh_build_log_error_style(initial_doc)
        main_window = ServiceLocator.get_main_window()
        main_window.connect('notify::current-breakpoint', self._on_breakpoint_change)
        # 同步初始状态（窗口启动时可能已在窄窗，breakpoint 已 apply）
        self._on_breakpoint_change(main_window, None)

    def on_document_removed(self, workspace, document):
        if self.workspace.active_document == None:
            self.set_build_button_state()
            self.activate_welcome_screen_mode()
            # 当前文档已全部移除，断开 build_state 监听并清掉 build_log_toggle 错误样式。
            self._disconnect_build_state_signal()
            self._clear_build_log_toggle_error_style()

    def on_new_active_document(self, workspace, document):
        self.set_build_button_state()
        self.activate_document_mode()
        self.show_document_name(document)
        self.update_toggles()

        document.connect('filename_change', self.on_name_change)
        document.connect('displayname_change', self.on_name_change)
        document.connect('modified_changed', self.on_modified_changed)

        # 切换活动文档：重新挂接 build_state 监听，使标题栏 build_log_toggle
        # 错误样式跟随新文档的编译结果。shortcutsbar 也在同一文档上挂监听，
        # 两份监听独立（各自操作自己的按钮），互不干扰。
        # 注意：on_new_active_document 触发时 workspace.active_document 已是
        # 新文档，必须基于先前存储的 _build_state_doc 断开旧监听，而不是
        # 直接读 workspace.active_document（那是新文档）。
        self._disconnect_build_state_signal()
        if document.is_latex_document():
            self._build_state_handler_id = document.build_system.connect('build_state', self.on_build_state)
            self._build_state_doc = document
        else:
            self._build_state_doc = None
        # 切换文档后立刻按新文档当前的编译结果刷新错误样式。
        # 关键修复：切换到无 error 的文档时，按钮必须退出红色状态，
        # 而不能残留上一个文档（曾有 error）的红色样式——因为切换文档本身
        # 不会触发 build_state 信号，仅靠 on_build_state 无法纠正残留态。
        self._refresh_build_log_error_style(document)

    def on_new_inactive_document(self, workspace, document):
        document.disconnect('filename_change', self.on_name_change)
        document.disconnect('displayname_change', self.on_name_change)
        document.disconnect('modified_changed', self.on_modified_changed)
        # 错误样式不在「文档变为非活动」时清除，而是在 on_new_active_document
        # 中按新活动文档的实际编译结果统一刷新（见 _refresh_build_log_error_style）。
        # build_state 监听由 on_new_active_document 在挂接新文档时断开旧文档。

    def on_root_state_change(self, workspace, state):
        self.set_build_button_state()
        self.update_toggles()

    def on_name_change(self, document, name=None):
        self.show_document_name(document)

    def on_modified_changed(self, document):
        self.show_document_name(document)

    def on_update_recently_opened_documents(self, workspace, recently_opened_documents):
        if self.workspace.active_document is None:
            self.view.open_document_button.set_visible(False)
            return
        self.view.open_document_button.set_visible(True)

    def set_build_button_state(self):
        document = self.workspace.get_root_or_active_latex_document()

        if document != None:
            current = self.view.build_wrapper.get_first_child()
            # 如果当前已显示的就是目标文档的 build_widget，跳过 remove+append。
            # 避免不必要的 widget 重建（每次根文档变化时都会调用此方法）。
            if current is not document.build_widget.view:
                if current is not None:
                    self.view.build_wrapper.remove(current)
                self.view.build_wrapper.append(document.build_widget.view)
        else:
            if self.view.build_wrapper.get_first_child() is not None:
                self.view.build_wrapper.remove(self.view.build_wrapper.get_first_child())

    def activate_welcome_screen_mode(self):
        self.hide_sidebar_toggles()
        self.hide_preview_help_toggles()
        self.view.open_document_button.set_visible(False)
        self.view.new_document_button.set_visible(False)
        self.view.center_button.set_sensitive(False)
        self.view.center_widget.set_visible_child_name('welcome')
        self.view.widget.add_css_class('welcome')
        self.update_build_log_toggle_visibility()
        # 「在终端中打开 Agent」按钮仅在有 root/active latex 文档时显示；
        # welcome 模式下必须无条件隐藏，避免 update_toggles 收敛到达之前
        # 短暂闪现。
        self.view.agent_terminal_button.set_visible(False)

    def activate_document_mode(self):
        self.view.new_document_button.set_visible(True)
        self.on_update_recently_opened_documents(None, self.workspace.recently_opened_documents)
        self.view.center_button.set_sensitive(True)
        self.view.center_widget.set_visible_child_name('button')
        self.view.widget.remove_css_class('welcome')
        self.update_build_log_toggle_visibility()

    def show_document_name(self, document):
        mod_text = '*' if document.source_buffer.get_modified() else ''
        self.view.document_title.set_title(document.get_basename() + mod_text)
        dirname = document.get_dirname()
        if dirname != '':
            folder_text = dirname.replace(os.path.expanduser('~'), '~')
            self.view.document_title.set_subtitle(folder_text)
        else:
            self.view.document_title.set_subtitle('')

    def update_toggles(self):
        if self.workspace.get_active_latex_document():
            self.show_sidebar_toggles()
        else:
            self.hide_sidebar_toggles()

        if self.workspace.get_root_or_active_latex_document():
            self.show_preview_help_toggles()
        else:
            self.hide_preview_help_toggles()

        # 标题栏 build_log_toggle 显隐需要叠加在 update_toggles 调用链中：
        # show_preview_help_toggles 已经基于「有 root/active latex 文档」判断，
        # 而 build_log_toggle 的额外条件是「show_shortcuts_bar=False」。
        # 在 update_toggles 末尾统一收敛，避免在 welcome 模式或非 latex 文档时
        # 误显。
        self.update_build_log_toggle_visibility()
        # 「在终端中打开 Agent」按钮的显隐同样收敛在此：有 root/active
        # latex 文档时显示，welcome 模式 / 非 latex 文档时隐藏。
        self.update_agent_terminal_button_visibility()

    def hide_sidebar_toggles(self):
        self.view.sidebar_toggle.set_visible(False)
        self.view.sidebar_toggle.set_sensitive(False)

    def hide_preview_help_toggles(self):
        self.view.preview_help_toggle.set_visible(False)
        self.view.preview_help_toggle.set_sensitive(False)

    def show_sidebar_toggles(self):
        self.view.sidebar_toggle.set_visible(True)
        self.view.sidebar_toggle.set_sensitive(True)

    def show_preview_help_toggles(self):
        self.view.preview_help_toggle.set_visible(True)
        self.view.preview_help_toggle.set_sensitive(True)

    def set_compact(self, compact):
        '''窄窗 compact 模式开关。设标志后重跑当前模式的可见性逻辑，
        让 activate_document_mode / show_preview_help_toggles 末尾的 compact
        覆盖生效（compact=True）或恢复（compact=False）。幂等。

        不能只 set_visible：welcome/document 模式与 toggle 状态共同决定可见性，
        必须重跑对应路径以保证 save/help 与其它按钮状态一致。'''
        if self._compact == compact:
            return
        self._compact = compact
        if self.workspace.active_document is not None:
            self.activate_document_mode()
        else:
            self.activate_welcome_screen_mode()
        self.update_toggles()

    def _on_breakpoint_change(self, window, pspec):
        '''notify::current-breakpoint 回调：当前 breakpoint 为 narrow_breakpoint
        时进入 compact 模式，否则（含 None=宽窗）退出。'''
        bp = window.get_current_breakpoint()
        narrow = getattr(window, 'narrow_breakpoint', None)
        self.set_compact(bp is not None and bp is narrow)

    # ---- Build log 标题栏副本（仅在 show_shortcuts_bar=False 时显示） ----

    def on_build_log_toggle_clicked(self, toggle_button, parameter=None):
        '''标题栏 build_log_toggle 点击：转发给 workspace.set_show_build_log。
        弹窗 present/close 统一在 workspace_presenter.update_build_log_visibility
        中处理（与 shortcutsbar 内同名按钮共享同一份逻辑）。'''
        self.workspace.set_show_build_log(toggle_button.get_active())

    def on_show_build_log_state_change(self, workspace, show_build_log):
        '''workspace.show_build_log 改变时（来自任何触发源：shortcutsbar 按钮点击、
        菜单、F9 快捷键、关弹窗等）同步标题栏 build_log_toggle 的 active 态。
        在 toggle 已被用户点击后、信号回环前 set_active 会触发 'notify::active'
        但不会再次 'clicked'，故不会形成回环。'''
        if self.view.build_log_toggle.get_active() != show_build_log:
            self.view.build_log_toggle.set_active(show_build_log)

    def on_settings_changed(self, settings, parameter):
        '''show_shortcuts_bar 偏好变化时，重新计算标题栏 build_log_toggle 显隐。
        其他偏好变化无需本 presenter 关心，忽略。'''
        section, item, value = parameter
        if item == 'show_shortcuts_bar':
            self.update_build_log_toggle_visibility()

    def on_build_state(self, build_system, message):
        '''active 文档的编译状态变化：error 时给 build_log_toggle 加红色样式，
        其它状态清掉。与 shortcutsbar.on_build_state 行为完全对称。'''
        if message == 'error':
            self.view.build_log_toggle.add_css_class('build-log-error')
        else:
            self._clear_build_log_toggle_error_style()

    def _clear_build_log_toggle_error_style(self):
        self.view.build_log_toggle.remove_css_class('build-log-error')

    def _refresh_build_log_error_style(self, document):
        '''根据文档当前的编译结果刷新 build_log_toggle 错误样式：
        - 文档未编译过 / 编译无 error（error_count == 0）：清除红色
        - 文档上次编译产生了 error（error_count > 0）：显示红色
        用于文档切换与初始构造，使按钮状态始终跟随当前活动文档，
        而非残留上一个文档的视觉状态。'''
        has_error = (document is not None and document.is_latex_document()
                     and document.build_system.get_error_count() > 0)
        if has_error:
            self.view.build_log_toggle.add_css_class('build-log-error')
        else:
            self._clear_build_log_toggle_error_style()

    def _disconnect_build_state_signal(self):
        '''安全断开先前挂接的 build_state 监听（无 handler / 无 document 时
        不报错）。仅在文档切换或全部文档移除时调用。'''
        handler = self._build_state_handler_id
        doc = self._build_state_doc
        if handler is None or doc is None:
            return
        try:
            doc.build_system.disconnect(handler)
        except Exception:
            # disconnect 可能因为 handler 已被其他代码断开而抛 TypeError；
            # 此处吞掉是因为我们的意图是确保不再监听 build_state，与谁先
            # 断开无关。
            pass
        self._build_state_handler_id = None
        self._build_state_doc = None

    def update_build_log_toggle_visibility(self):
        '''收敛 build_log_toggle 显隐的最终条件：
        - show_shortcuts_bar=False：用户主动关闭 Shortcuts Bar
        - get_root_or_active_latex_document() != None：当前是 latex 文档
          （非 latex 文档/欢迎页时，按钮不显示，避免误操作打开空日志）
        两个条件都满足时显示，否则隐藏。'''
        show = self.workspace.settings.get_value('preferences', 'show_shortcuts_bar')
        has_latex_doc = self.workspace.get_root_or_active_latex_document() is not None
        self.view.build_log_toggle.set_visible((not show) and has_latex_doc)

    def update_agent_terminal_button_visibility(self):
        '''收敛「在终端中打开 Agent」按钮的显隐：有 root/active latex 文档
        且不在 welcome 模式时显示；welcome 模式 / 非 latex 文档时隐藏
        （与 build_wrapper 内容的出现条件一致）。welcome 模式以
        headerbar 的 'welcome' css class 为权威信号（由
        activate_welcome_screen_mode / activate_document_mode 设置）。'''
        has_latex_doc = self.workspace.get_root_or_active_latex_document() is not None
        in_welcome = self.view.widget.has_css_class('welcome')
        self.view.agent_terminal_button.set_visible(has_latex_doc and not in_welcome)

    # ---- 快速打开 Agent 终端 ----

    def on_agent_terminal_clicked(self, button):
        '''「在终端中打开 Agent」按钮点击：裸启动 AI Fix 配置的 Agent CLI。

        与 Build Log 的 AI Fix 流程（build_log_dialog_controller._initiate_ai_fix）
        共用启用开关与工具配置，但不组词、不查信任目录（无 prompt 发送，
        用户在 TUI 中自行输入并控制对话）、不保存文档。工作目录 = 活动
        文档所在目录。
        '''
        settings = self.workspace.settings

        # 1. 全局开关（与 AI Fix 共用）
        if not settings.get_value('preferences', 'ai_fix_enabled'):
            self._toast(_('AI agent is disabled. Enable it in Preferences → General → AI Settings.'))
            return

        # 2. 取活动文档；未保存的新文档无目录可打开
        document = self.workspace.get_active_document()
        if document is None:
            self._toast(_('No active document'))
            return
        cwd = document.get_dirname()
        if not cwd:
            self._toast(_('Please save the document first'))
            return

        # 3. 取激活工具配置（找不到时回退第一个，与 _initiate_ai_fix 一致）
        active_tool_name = settings.get_value('preferences', 'ai_fix_active_tool')
        tools = settings.get_value('preferences', 'ai_fix_tools')
        tool_config = next((t for t in tools if t.get('name') == active_tool_name), None)
        if tool_config is None:
            tool_config = tools[0] if tools else None
            if tool_config is None:
                self._toast(_('No agent tool configured. Add one in Preferences → General → AI Settings.'))
                return

        # 4. 裸启动：终端 + executable（无参数，进入交互 TUI）
        from setzer.ai_fix import agent_runner
        # 本地化钩子（agent_runner 模块文档声明）：让返回的提示消息走 gettext。
        agent_runner._ = _
        terminal_cmd = settings.get_value('preferences', 'ai_fix_terminal_cmd') or None
        success, msg = agent_runner.run_headed_bare(tool_config, cwd, terminal_cmd=terminal_cmd)
        self._toast(msg)

    def _toast(self, message):
        '''主窗口 toast。本按钮不在任何弹窗内，始终用主窗口的 toast_overlay。'''
        try:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'toast_overlay'):
                main_window.toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception:
            pass  # toast 失败不影响主流程

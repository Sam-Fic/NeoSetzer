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
# along with this program. If not, see <http://www.gnu.org/licenses/>

"""AI 修复 Preferences 页。

镜像 page_build_system / page_autocomplete 的 Adw.PreferencesPage 范式。
提供：
  1. 启用开关（ai_fix_enabled）
  2. 当前激活的 Agent 工具（从 ai_fix_tools 选 name）
  3. 终端命令（覆盖自动检测）
  4. Agent 工具列表：内置预设只读、自定义可删；可添加自定义工具
  5. 已信任目录列表：可手动移除以撤销「不再提示」
  6. 重置按钮

无头模式已移除（安全考虑：Agent 在后台直接修改文件不安全）。
仅保留有头模式：用户在终端里看到 Agent 的每一步操作。

设计文档：.trae/documents/ai-fix-agent-integration.md §2.6（page_ai_fix.py）
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

from setzer.app.service_locator import ServiceLocator
from setzer.ai_fix import agent_runner
from setzer.ai_fix.presets import default_tools, builtin_names


# 注意：模块顶层不允许调用 _()，因为 gettext.install 尚未执行
# （setzer.in:113 才注入 builtins._）。所有 _() 调用都在 __init__ /
# 方法内运行时求值，与 page_appearance / build_log_dialog_viewgtk 一致。


class PageAI(object):

    def __init__(self, preferences, settings):
        self.view = PageAIView()
        self.preferences = preferences
        self.settings = settings
        self.main_window = ServiceLocator.get_main_window()

        # 缓存当前工具列表的 name → 索引映射，用于 combo 同步
        self._tool_names = []
        # 已加入 group 的 row 引用列表（Adw.PreferencesGroup.get_first_child
        # 返回内部 Gtk.Box 而非用户行，无法用 get_first_child 循环 remove；
        # 改为自维护列表，重建时按列表 remove）。
        self._tool_rows = []
        self._trusted_rows = []

    def init(self):
        # 1. 启用开关
        self.view.option_enabled.set_active(self.settings.get_value('preferences', 'ai_fix_enabled'))
        self.view.option_enabled.connect('notify::active', self.on_switch_toggled, 'ai_fix_enabled')
        self.view.option_enabled.connect('notify::active', self.on_enabled_toggled)
        self.on_enabled_toggled(self.view.option_enabled, None)

        # 2. 当前工具 combo
        self.view.option_tool.connect('notify::selected', self.on_tool_selected)
        self._rebuild_tool_combo()

        # 3. 终端命令 EntryRow
        self.view.option_terminal_cmd.set_text(self.settings.get_value('preferences', 'ai_fix_terminal_cmd') or '')
        self.view.option_terminal_cmd.connect('changed', self.on_terminal_cmd_changed)

        # 4. 工具列表 + 添加按钮
        self.view.add_tool_button.connect('clicked', self.on_add_tool_clicked)
        self._rebuild_tools_list()

        # 5. 已信任目录列表
        self._rebuild_trusted_dirs_list()

        # 6. 重置按钮
        self.view.reset_button.connect('clicked', self.on_reset_clicked)

    # ---------- 工具列表 / 信任目录列表刷新 ----------

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
            del_btn = Gtk.Button(icon_name='user-trash-symbolic')
            del_btn.set_has_frame(False)
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.add_css_class('flat')
            del_btn.set_tooltip_text(_('Remove from trusted list'))
            del_btn.connect('clicked', self.on_delete_trusted_clicked, d)
            row.add_suffix(del_btn)
            group.add(row)
            self._trusted_rows.append(row)

    # ---------- 信号回调 ----------

    def on_switch_toggled(self, switch, pspec, preference_name):
        self.settings.set_value('preferences', preference_name, switch.get_active())

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

    def on_reset_clicked(self, button):
        dialog = Adw.AlertDialog(
            heading=_('Reset AI Fix settings?'),
            body=_('All AI Fix preferences will be restored to their default values. '
                   'Custom tools and trusted directories will be removed.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('reset', _('Reset'))
        dialog.set_response_appearance('reset', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.main_window, None, self.on_reset_confirmed)

    def on_reset_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id != 'reset':
            return
        defaults = self.settings.defaults['preferences']
        for key in ('ai_fix_enabled', 'ai_fix_active_tool',
                    'ai_fix_terminal_cmd', 'ai_fix_trusted_dirs', 'ai_fix_tools'):
            self.settings.set_value('preferences', key, defaults[key])
        self.settings.pickle()
        # 重新同步 UI 控件
        self.view.option_enabled.set_active(defaults['ai_fix_enabled'])
        self.view.option_terminal_cmd.set_text(defaults['ai_fix_terminal_cmd'] or '')
        self._rebuild_tool_combo()
        self._rebuild_tools_list()
        self._rebuild_trusted_dirs_list()


class PageAIView(Adw.PreferencesPage):

    def __init__(self):
        Adw.PreferencesPage.__init__(self)
        self.set_title(_('AI Fix'))
        self.set_icon_name('applications-science-symbolic')

        # 1. 启用开关 group
        group_main = Adw.PreferencesGroup()
        self.add(group_main)

        self.option_enabled = Adw.SwitchRow()
        self.option_enabled.set_title(_('Enable AI Fix'))
        self.option_enabled.set_subtitle(_('Show "AI Fix" buttons in the Build Log dialog'))
        group_main.add(self.option_enabled)

        # 2. 当前工具 combo
        self.option_tool = Adw.ComboRow()
        self.option_tool.set_title(_('Active agent tool'))
        self.option_tool.set_subtitle(_('Which CLI to invoke when clicking AI Fix'))
        group_main.add(self.option_tool)

        # 3. 终端命令 EntryRow
        self.option_terminal_cmd = Adw.EntryRow()
        self.option_terminal_cmd.set_title(_('Terminal command (optional)'))
        self.option_terminal_cmd.set_tooltip_text(
            _('Leave empty to auto-detect (gnome-terminal / xterm / konsole / ...). '
              'Under Flatpak, flatpak-spawn --host is added automatically.'))
        group_main.add(self.option_terminal_cmd)

        # 4. Agent 工具列表（仅装工具行；添加按钮在独立 group_add，避免重建时被误移除）
        self.group_tools = Adw.PreferencesGroup()
        self.group_tools.set_title(_('Agent tools'))
        self.group_tools.set_description(_('Built-in presets cannot be removed. Add a custom tool to integrate other Agent CLIs.'))
        self.add(self.group_tools)

        # 4b. 添加按钮：独立 group，与 reset_button 一样直接放入 group（右对齐）。
        self.group_add = Adw.PreferencesGroup()
        self.add(self.group_add)
        self.add_tool_button = Gtk.Button(label=_('+ Add custom tool'))
        self.add_tool_button.set_halign(Gtk.Align.END)
        self.group_add.add(self.add_tool_button)

        # 5. 已信任目录列表
        self.group_trusted = Adw.PreferencesGroup()
        self.group_trusted.set_title(_('Trusted directories'))
        self.group_trusted.set_description(_('Directories where the preview dialog is skipped. '
                                             'Click delete to revoke trust for a project.'))
        self.add(self.group_trusted)

        # 6. 重置按钮
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

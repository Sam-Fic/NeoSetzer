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

from gi.repository import Gdk, Adw, Gtk, Gio
import os.path

# 延迟导入避免循环：controller 引用 presenter 的 ALL_TYPES 常量及 classify_warning_type。
import setzer.dialogs.build_log.build_log_dialog_presenter as presenter_module
from setzer.dialogs.build_log.build_log_dialog_presenter import classify_warning_type


class BuildLogDialogController(object):
    '''处理弹窗内的用户交互：单击行跳转报错行 + Copy All 按钮 + AI 修复按钮。'''

    def __init__(self, build_log, dialog_view):
        self.build_log = build_log
        self.view = dialog_view
        self.presenter = None

        # AI 修复 PreviewDialog 懒加载：避免在 controller 构造时即创建 GTK 控件
        # （controller 在 BuildLog.__init__ 时实例化，那时 main_window 可能尚未就绪）。
        # 首次点修复按钮时才创建；后续复用同一实例。
        self._preview_dialog = None

        # Copy All 按钮
        self.view.copy_all_button.connect('clicked', self.on_copy_all_clicked)

        # Save Log As 按钮
        self.view.save_log_button.connect('clicked', self.on_save_log_clicked)

        # AI Fix All 按钮（顶栏批量）：把当前可见的 Error 项批量发给 Agent CLI
        self.view.ai_fix_all_button.connect('clicked', self.on_ai_fix_all_clicked)

        # 搜索框：输入文本实时过滤日志项。
        self.view.search_entry.connect('changed', self.on_search_changed)

        # 过滤器信号（存储 handler_id 以便 presenter 更新下拉框时屏蔽信号）
        self.view._file_filter_handler_id = self.view.file_filter_combo.connect('changed', self.on_filter_changed)
        self.view._type_filter_handler_id = self.view.type_filter_combo.connect('changed', self.on_filter_changed)
        self.view.line_min_spin.connect('value-changed', self.on_filter_changed)
        self.view.line_max_spin.connect('value-changed', self.on_filter_changed)
        
        # 类型过滤复选框信号
        self.view.error_checkbox.connect('toggled', self.on_filter_changed)
        self.view.warning_checkbox.connect('toggled', self.on_filter_changed)
        self.view.badbox_checkbox.connect('toggled', self.on_filter_changed)

        # 每个 list 的 row-activated：单击跳转报错行（与原 BuildLogController 一致）。
        # 弹窗内有 3 个 list（Errors / Warnings / Badboxes），全部连同一个回调。
        # 同时注入 ai_fix_row_callback，使行尾 AI 修复按钮点击转回 controller。
        for lst in self.view.lists.values():
            lst.connect('row-activated', self.on_row_activated)
            lst.connect('selected-rows-changed', self.on_selected_rows_changed)
            lst.ai_fix_row_callback = self.on_ai_fix_row_clicked
            lst.ignore_row_callback = self.on_ignore_row_clicked

        # 「恢复忽略的警告」头部按钮：注入回调并初始化显隐（若有已忽略类型）。
        self.view.on_restore_ignored_callback = self.on_restore_ignored_clicked
        self._update_restore_button()

        # group 折叠/展开切换回调：保存状态到 settings
        self.view.on_group_toggle_callback = self.on_group_toggle

    def on_selected_rows_changed(self, listbox):
        '''保证整个 Build Log 弹窗内只有一个高亮选中项。

        弹窗内有 3 个独立的 Gtk.ListBox（Error / Warning / Badbox），各自
        的 SINGLE 选择模式只在「单个列表内部」保证唯一。点击不同分类里的
        行会导致多个分类同时各有一个高亮项，不符合「整体唯一选中」的语义。
        这里在任一列表出现选中时，清空其余列表的选中，使全局只有一项高亮。
        '''
        if listbox.get_selected_row() is None:
            return
        for other in self.view.lists.values():
            if other is not listbox:
                other.unselect_all()

    def on_group_toggle(self, item_type, expanded):
        '''用户切换 group 折叠/展开状态时调用，保存到 settings。'''
        self.presenter.on_group_toggle(item_type, expanded)

    def on_row_activated(self, listbox, row):
        '''单击行：打开对应源文件并定位到报错行。

        逻辑与原 BuildLogController.on_row_activated 完全一致，迁移至此。
        增加：跳转后高亮目标行，\\input 文件自动打开，跳转失败时 toast 提示。
        '''
        if self.build_log.document is None:
            return
        if row is None or row.filename is None:
            return

        document = self.build_log.workspace.open_document_by_filename(row.filename)
        if document is None:
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Could not open file')))
            return
        line_number = row.line_number - 1
        if line_number < 0:
            return

        # 错误行若落在折叠区内会不可见，跳转前先展开包含它的所有折叠区域。
        document.code_folding.unfold_region_containing_line(line_number)
        document.place_cursor(line_number)
        document.scroll_cursor_onscreen()
        document.source_view.grab_focus()

        start, end = document.source_buffer.get_iter_at_line(line_number)[1], None
        if start is not None:
            end = start.copy()
            if not start.ends_line():
                end.forward_to_line_end()
            document.highlight_section(start, end)

    def on_copy_all_clicked(self, button):
        '''Copy 所有当前显示的 items（按设置项过滤后），格式 file:line: description per line。'''
        if not self.build_log.has_items():
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('The log is empty, nothing to copy')))
            return
        lines = self._get_filtered_lines()
        Gdk.Display.get_default().get_clipboard().set('\n'.join(lines))
        self.view.toast_overlay.add_toast(Adw.Toast.new(_('Copied to clipboard')))

    def on_save_log_clicked(self, button):
        '''Save Log As：将过滤后的日志保存到文件。'''
        if not self.build_log.has_items():
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('The log is empty, nothing to save')))
            return
        dialog = Gtk.FileChooserNative(
            title=_('Save Build Log As'),
            transient_for=self.view.get_root(),
            action=Gtk.FileChooserAction.SAVE,
            accept_label=_('Save'),
            cancel_label=_('Cancel'))
        dialog.set_current_name('build_log.txt')

        # 过滤器：文本文件
        filter_text = Gtk.FileFilter()
        filter_text.set_name(_('Text files'))
        filter_text.add_mime_type('text/plain')
        dialog.add_filter(filter_text)

        filter_all = Gtk.FileFilter()
        filter_all.set_name(_('All files'))
        filter_all.add_pattern('*')
        dialog.add_filter(filter_all)

        dialog.connect('response', self.on_save_log_response)
        dialog.show()

    def on_save_log_response(self, dialog, response):
        '''处理文件保存对话框的响应。'''
        if response != Gtk.ResponseType.ACCEPT:
            return
        file = dialog.get_file()
        if file is None:
            return
        lines = self._get_filtered_lines()
        try:
            with open(file.get_path(), 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Build log saved')))
        except Exception:
            self.view.toast_overlay.add_toast(Adw.Toast.new(_('Failed to save build log')))

    def _get_filtered_lines(self):
        '''获取当前过滤条件下所有 items 的文本行列表。

        类型可见性始终为全部类型；`autoshow_build_log` 设置项只控制「何时
        自动弹出」弹窗，不用于过滤内容（内容筛选完全由弹窗内控件决定）。
        '''
        visible_types = presenter_module.BuildLogDialogPresenter.ALL_TYPES

        # 获取过滤器值
        file_filter = self.view.file_filter_combo.get_active_text()
        type_filter = self.view.type_filter_combo.get_active_text()

        line_min = int(self.view.line_min_spin.get_value())
        line_max = int(self.view.line_max_spin.get_value())
        if line_max == 0:
            line_max = 999999

        lines = []
        search_text = self.view.search_entry.get_text().lower()
        # 「忽略此类 warning」同样作用于复制 / 保存，与弹窗展示保持一致。
        ignored_keys = set(
            self.build_log.settings.get_value('preferences', 'ignored_warning_types') or [])
        for item in self.build_log.items:
            if item[0] not in visible_types:
                continue
            # 被忽略的 warning 类型：跳过
            if ignored_keys and classify_warning_type(item[0], item[4])[0] in ignored_keys:
                continue
            # 搜索过滤
            if search_text:
                description = (item[4] or '').lower()
                filename = (item[2] or '').lower()
                line_number = str(item[3]) if item[3] >= 0 else ''
                if not (search_text in description or search_text in filename or search_text in line_number):
                    continue
            # 文件过滤
            if file_filter and file_filter != _('All'):
                if item[2] is None or os.path.basename(item[2]) != file_filter:
                    continue
            # 错误类型过滤（基于描述内容的关键词匹配）
            if type_filter and type_filter != _('All'):
                desc = (item[4] or '').lower()
                if not self._matches_error_type(type_filter, desc, item[0]):
                    continue
            # 行号范围过滤
            if item[3] >= 0 and (item[3] < line_min or item[3] > line_max):
                continue
            lines.append(self._format_item(item))
        return lines

    def _matches_error_type(self, type_filter, description, item_type):
        '''检查日志项是否匹配指定的错误类型过滤。'''
        if type_filter == _('Undefined reference'):
            return 'undefined' in description and 'reference' in description
        elif type_filter == _('Missing package'):
            return 'missing' in description or 'not found' in description
        elif type_filter == _('Syntax error'):
            return 'syntax' in description or 'error' in description.lower()
        elif type_filter == _('All types'):
            return True
        return True

    def on_filter_changed(self, widget, *args):
        '''过滤器变化时触发视图重建。'''
        if self.presenter is not None:
            self.presenter.set_filter_values(
                self._get_file_filter_value(),
                self._get_type_filter_value(),
                int(self.view.line_min_spin.get_value()),
                int(self.view.line_max_spin.get_value()),
                self.view.get_selected_types())

    def _get_file_filter_value(self):
        return self.view.file_filter_combo.get_active_text()

    def _get_type_filter_value(self):
        return self.view.type_filter_combo.get_active_text()

    def on_search_changed(self, search_entry):
        self.presenter.set_search_text(search_entry.get_text())

    @staticmethod
    def _format_item(item):
        '''单行文本格式，与 BuildLogList._format_row_text 一致。'''
        # item 元组：item[0]=type, item[1]=stage, item[2]=filename, item[3]=line_number, item[4]=description
        item_type, _, filename, line_number, description = item
        parts = []
        if filename:
            parts.append(filename)
            if line_number >= 0:
                parts.append(str(line_number))
        text = ':'.join(parts)
        if description:
            text = (text + ': ' + description) if text else description
        return text

    # ==================== 忽略此类 warning ====================
    # 右键弹出的「忽略此类 warning」入口。被忽略的类型（稳定 key）存入
    # preferences.ignored_warning_types，写入配置文件持久化；弹窗展示、Copy All、
    # Save Log 均会跳过这些类型，从而消除「每次构建都看到同一类无意义 warning」。
    # 为避免误忽略无法挽回，忽略后弹 toast 提供 Undo。

    def on_ignore_row_clicked(self, row):
        '''右键菜单「忽略此类 warning」回调：把该 row 的类型加入忽略列表。'''
        key, label = classify_warning_type(row.item_type, row.description)
        settings = self.build_log.settings
        ignored = list(settings.get_value('preferences', 'ignored_warning_types') or [])
        if key in ignored:
            return
        ignored.append(key)
        settings.set_value('preferences', 'ignored_warning_types', ignored)
        settings.pickle()
        # 强制重建弹窗内容（绕过签名短路），被忽略项立即消失。
        if self.presenter is not None:
            self.presenter.refresh()
        self._update_restore_button()
        toast = Adw.Toast.new(_('Ignored “{label}” warnings').format(label=label))
        toast.set_timeout(6)
        toast.set_button_label(_('Undo'))
        toast.connect('button-clicked', self._on_undo_ignore, key)
        self._dispatch_toast(toast)

    def _on_undo_ignore(self, toast, key):
        '''toast 的 Undo：从忽略列表移除该类型并重建弹窗。'''
        settings = self.build_log.settings
        ignored = list(settings.get_value('preferences', 'ignored_warning_types') or [])
        if key in ignored:
            ignored.remove(key)
            settings.set_value('preferences', 'ignored_warning_types', ignored)
            settings.pickle()
            if self.presenter is not None:
                self.presenter.refresh()
            self._update_restore_button()

    def on_restore_ignored_clicked(self):
        '''头部「恢复忽略的警告」按钮：清空忽略列表并重建弹窗。'''
        settings = self.build_log.settings
        if settings.get_value('preferences', 'ignored_warning_types'):
            settings.set_value('preferences', 'ignored_warning_types', [])
            settings.pickle()
            if self.presenter is not None:
                self.presenter.refresh()
            self._toast(_('Restored all ignored warnings'))
        self._update_restore_button()

    def _update_restore_button(self):
        '''根据当前忽略列表刷新头部「恢复忽略的警告」按钮的显隐与计数。'''
        try:
            ignored = self.build_log.settings.get_value('preferences', 'ignored_warning_types') or []
            if self.view is not None and hasattr(self.view, 'set_restore_visible'):
                self.view.set_restore_visible(bool(ignored), len(ignored))
        except Exception:
            pass

    # ==================== AI 修复集成 ====================
    # 设计见 .trae/documents/ai-fix-agent-integration.md §3.2。
    # 流程：
    #   点按钮 → 组装 prompt → [若 cwd 已信任] 直接执行
    #                            └─→ [否则] 弹预览/确认单弹窗
    #                                  ├─ 用户点「发送」→ 执行 + 可选加信任
    #                                  └─ 用户点「取消」→ 什么都不做
    #   执行：先保存文档（保证磁盘=缓冲区，避免无头改文件触发冲突对话框）
    #        → 无头: 后台线程跑 + idle 回调 toast + 可选自动重构
    #        → 有头: 同步启动外部终端（flatpak 下用 flatpak-spawn --host）
    #   单弹窗即确认：用户点「发送」就是同意启动 Agent，不再叠第二个确认弹窗。

    def on_ai_fix_row_clicked(self, row):
        '''行内 AI 修复按钮点击：取该 row 的数据，调 _initiate_ai_fix(single=True)。'''
        # row 由 make_row 设置 .filename/.line_number/.description/.item_type
        item = (
            getattr(row, 'item_type', 'Error'),
            None,
            getattr(row, 'filename', None),
            getattr(row, 'line_number', -1),
            getattr(row, 'description', ''),
        )
        self._initiate_ai_fix(single=True, item=item)

    def on_ai_fix_all_clicked(self, button):
        '''顶栏 AI Fix All 按钮：批量修复当前在弹窗中可见的所有日志项。

        跟随筛选：发送的内容 = 用户在 Build Log 弹窗里「看到什么」就发什么。
        包括 autoshow_build_log 类型筛选（errors/errors_warnings/all）+
        搜索框文本 + 文件/类型/行号筛选器。Warning / Badbox 也会被纳入，
        因为有时用户也想修这些（如 Underfull \\hbox 等 badbox）。
        '''
        if self.presenter is None:
            return
        visible_items = self.presenter.get_visible_items()
        if not visible_items:
            self._toast(_('No visible log items to send'))
            return
        self._initiate_ai_fix(single=False, items=visible_items)

    def _initiate_ai_fix(self, single, item=None, items=None):
        '''统一的入口：检查 enabled / 取文档 / 组装 prompt / 信任跳过 / 弹预览。

        Args:
            single: True=单条；False=批量。
            item: 单条时传入的 build_log.items 元组。
            items: 批量时传入的元组列表。
        '''
        settings = self.build_log.settings

        # 1. 全局开关
        if not settings.get_value('preferences', 'ai_fix_enabled'):
            self._toast(_('AI Fix is disabled. Enable it in Preferences → AI Fix.'))
            return

        # 2. 取活动文档
        document = self.build_log.workspace.get_active_document()
        if document is None:
            self._toast(_('No active document'))
            return
        cwd = document.get_dirname()
        if not cwd:
            # 未保存的新文档：无 cwd 无法定位工作目录，也无法写文件
            self._toast(_('Please save the document first'))
            return

        # 3. 取激活工具配置
        active_tool_name = settings.get_value('preferences', 'ai_fix_active_tool')
        tools = settings.get_value('preferences', 'ai_fix_tools')
        tool_config = next((t for t in tools if t.get('name') == active_tool_name), None)
        if tool_config is None:
            # 配置异常：回退第一个工具
            tool_config = tools[0] if tools else None
            if tool_config is None:
                self._toast(_('No agent tool configured. Add one in Preferences → AI Fix.'))
                return

        # 4. 组装 prompt
        from setzer.ai_fix import prompt_builder
        if single:
            prompt = prompt_builder.build_prompt_for_item(document, item)
        else:
            prompt = prompt_builder.build_prompt_for_items(document, items)

        # 5. 信任跳过：cwd 已在 ai_fix_trusted_dirs → 直接执行
        # 用 realpath 规范化两边：文档目录可能是符号链接（如 /tmp → /private/tmp），
        # 不规范化会导致「明明加过信任还弹窗」的诡异现象。
        trusted_dirs = settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
        cwd_real = os.path.realpath(cwd)
        trusted_real = {os.path.realpath(d) for d in trusted_dirs}
        if cwd_real in trusted_real:
            self._execute(prompt, tool_config, cwd, document)
            return

        # 6. 未信任：弹预览/确认单弹窗
        parent = self._get_main_window()
        dialog = self._get_preview_dialog()
        dialog.present_for(
            parent=parent,
            prompt=prompt,
            tool_name=tool_config.get('name', 'agent'),
            cwd=cwd,
            on_send_cb=lambda edited_prompt, dont_ask: self._on_send_confirmed(
                edited_prompt, dont_ask, tool_config, cwd, document),
        )

    def _on_send_confirmed(self, prompt, dont_ask, tool_config, cwd, document):
        '''预览弹窗「发送」回调：可选加信任 + 执行。

        存信任列表时用 realpath 规范化：与 _initiate_ai_fix 的比较逻辑一致，
        避免符号链接路径导致「加了信任但不生效」。
        '''
        if dont_ask:
            trusted = self.build_log.settings.get_value('preferences', 'ai_fix_trusted_dirs') or []
            cwd_real = os.path.realpath(cwd)
            trusted_real = {os.path.realpath(d) for d in trusted}
            if cwd_real not in trusted_real:
                trusted = list(trusted) + [cwd_real]
                self.build_log.settings.set_value('preferences', 'ai_fix_trusted_dirs', trusted)
                # 落盘：set_value 只改内存 dict，pickle() 写回 settings.json。
                # 与 page_appearance.py:116 的范式一致——每次 set_value 后 pickle。
                # 不调则重启后信任目录丢失（用户会以为「加了不生效」）。
                self.build_log.settings.pickle()
                self._toast(_('Added to trusted directories'))
        self._execute(prompt, tool_config, cwd, document)

    def _execute(self, prompt, tool_config, cwd, document):
        '''执行：先保存文档，再启动有头终端 Agent。

        保存让缓冲区=磁盘：Agent 在终端修改文件后，Setzer 的 2 秒轮询
        （auto_reload_on_external_change）检测到 mtime 变化自动重载。
        若缓冲区有未保存改动，轮询会弹「磁盘已更改」对话框让用户选择。
        '''
        # 保存文档：无 filename 视为未保存，已检查过，此处兜底再判一次
        if document.get_filename() is None:
            self._toast(_('Please save the document first'))
            return
        if not document.save_to_disk():
            # 保存失败，中止 AI fix（toast 已由 save_to_disk 弹出）
            return

        from setzer.ai_fix import agent_runner
        filename = document.get_filename()
        terminal_cmd = self.build_log.settings.get_value('preferences', 'ai_fix_terminal_cmd') or None
        success, msg = agent_runner.run_headed(
            tool_config, prompt, cwd, filename, terminal_cmd=terminal_cmd)
        self._toast(msg)

    def _get_preview_dialog(self):
        '''懒加载 PreviewDialog 单例。'''
        if self._preview_dialog is None:
            from setzer.ai_fix.preview_dialog import PreviewDialog
            main_window = self._get_main_window()
            self._preview_dialog = PreviewDialog(main_window)
        return self._preview_dialog

    def _get_main_window(self):
        from setzer.app.service_locator import ServiceLocator
        return ServiceLocator.get_main_window()

    def _toast(self, message):
        '''显示 toast：若弹窗已打开用弹窗的 toast_overlay，否则用主窗口的。'''
        try:
            self._dispatch_toast(Adw.Toast.new(message))
        except Exception:
            pass  # toast 失败不影响主流程

    def _dispatch_toast(self, toast):
        """把已有 toast 对象派发到正确的 toast_overlay（弹窗已打开则弹窗内，否则主窗口）。"""
        try:
            if self.build_log.is_open and self.view is not None and self.view.toast_overlay is not None:
                self.view.toast_overlay.add_toast(toast)
            else:
                main_window = self._get_main_window()
                if main_window is not None and hasattr(main_window, 'toast_overlay'):
                    main_window.toast_overlay.add_toast(toast)
        except Exception:
            pass  # toast 失败不影响主流程

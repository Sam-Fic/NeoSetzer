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

import os.path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, GLib, Gtk, GObject, Pango, Adw

from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


# on_keypress 每次按键都跑，Gdk.keyval_from_name 模块级预计算避免每次 C 查表。
_KEYVAL_TAB = Gdk.keyval_from_name('Tab')
_KEYVAL_ISO_LEFT_TAB = Gdk.keyval_from_name('ISO_Left_Tab')


class DocumentController(object):

    def __init__(self, document, document_view):

        self.document = document
        self.view = document_view

        self.deleted_on_disk_dialog_shown_after_last_save = False
        self.changed_on_disk_dialog_shown_after_last_change = False
        self.continue_save_date_loop = True
        self.zoom_threshold = 0
        # 缩放持久化去抖 id：Ctrl+滚轮快速缩放时每个阈值跨越都更新 FontManager
        # 内存值（实时刷新字体），但 settings.set_value（磁盘写 + ~10 观察者通知）
        # 延迟到缩放停止 500ms 后执行一次，避免每帧写盘。
        self._zoom_persist_timeout_id = None
        # 自动静默重载的去抖 timeout id：检测到外部磁盘变更后延迟 1000ms 再重载，
        # 期间若再次检测到变更则重置 timer，避免文件写入过程中频繁重载。
        self._auto_reload_timeout_id = None
        # 保存 timeout id 以便文档关闭时移除。原实现仅置 continue_save_date_loop=False，
        # 定时器仍会再触发一次才退出；直接 remove 更及时。
        # 2000ms 而非 500ms：检测外部磁盘变更不需要亚秒级响应，2 秒足够
        # （VS Code/gedit/Kate 均用 2–5 秒）。per-document stat I/O 降低 75%。
        self._save_date_loop_timeout_id = GObject.timeout_add(2000, self.save_date_loop)

        self.primary_click_controller = Gtk.GestureClick()
        self.primary_click_controller.set_button(1)
        self.primary_click_controller.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self.primary_click_controller.connect('pressed', self.on_primary_buttonpress)
        self.view.source_view.add_controller(self.primary_click_controller)

        self.secondary_click_controller = Gtk.GestureClick()
        self.secondary_click_controller.set_button(3)
        self.secondary_click_controller.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self.secondary_click_controller.connect('pressed', self.on_secondary_buttonpress)
        self.view.source_view.add_controller(self.secondary_click_controller)

        self.scrolling_controller = Gtk.EventControllerScroll()
        self.scrolling_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.scrolling_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.KINETIC)
        self.scrolling_controller.connect('scroll', self.on_scroll)
        self.scrolling_controller.connect('decelerate', self.on_decelerate)
        self.view.scrolled_window.add_controller(self.scrolling_controller)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect('key-pressed', self.on_keypress)
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.document.view.source_view.add_controller(key_controller)

        # Ctrl+Click 前向同步的视觉反馈：Ctrl 按下并悬停时显示 pointer 光标，
        # 提示此处可 Ctrl+Click 跳转到 PDF。EventControllerMotion 在鼠标进入/
        # 移动时检查修饰键状态；静止时 Ctrl 按下/释放 不触发（局限性，但移动
        # 鼠标即可刷新光标，满足 Low 优先级增强需求）。控制器随 source_view
        # 销毁自动断开，shutdown 无需处理。
        # 缓存两个光标对象：motion 事件高频触发，避免每次 new_from_name 重建。
        self._cursor_pointer = Gdk.Cursor.new_from_name('pointer')
        self._cursor_text = Gdk.Cursor.new_from_name('text')
        self._motion_controller = Gtk.EventControllerMotion()
        self._motion_controller.connect('enter', self._on_motion_enter)
        self._motion_controller.connect('motion', self._on_motion_motion)
        self._motion_controller.connect('leave', self._on_motion_leave)
        self.view.source_view.add_controller(self._motion_controller)

        # 窗口获得焦点时立即检查外部磁盘变更，缩短用户切回 Setzer 时的感知
        # 延迟（原仅靠 2 秒轮询，Alt+Tab 切回后最多等 2 秒才提示）。2 秒
        # 轮询照常运行作为兜底。main_window 生命周期长于文档，shutdown 需断开。
        self._window_active_handler = None
        main_window = ServiceLocator.get_main_window()
        if main_window is not None:
            self._window_active_handler = main_window.connect(
                'notify::is-active', self._on_window_active_changed)

    def shutdown(self):
        '''文档关闭时由 workspace.remove_document 调用，移除 500ms 轮询定时器。'''
        self.continue_save_date_loop = False
        if self._save_date_loop_timeout_id is not None:
            GLib.Source.remove(self._save_date_loop_timeout_id)
            self._save_date_loop_timeout_id = None
        # 若有待持久化的缩放，立即写入（避免丢失最后一次缩放调整）。
        if self._zoom_persist_timeout_id is not None:
            GLib.Source.remove(self._zoom_persist_timeout_id)
            self._persist_zoom()
        # 取消挂起的自动静默重载，避免文档关闭后回调访问已释放对象。
        if self._auto_reload_timeout_id is not None:
            GLib.Source.remove(self._auto_reload_timeout_id)
            self._auto_reload_timeout_id = None
        # 断开窗口焦点信号：main_window 生命周期长于文档，不手动断开会
        # 导致已关闭文档的 _on_window_active_changed 被调用（访问已销毁的
        # self.document）。motion controller 随 source_view 销毁自动断开。
        if self._window_active_handler is not None:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None:
                main_window.disconnect(self._window_active_handler)
            self._window_active_handler = None

    def on_primary_buttonpress(self, controller, n_press, x, y):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
                workspace = ServiceLocator.get_workspace()
                active_document = workspace.get_active_document()
                if active_document is not None:
                    # forward_sync action 仅在 can_sync 时启用，但 Ctrl+Click
                    # 绕过 action enablement 直接调用。这里复用相同的可同步
                    # 判定：不可同步时（PDF 未生成 / 非 LaTeX）弹 toast 提示，
                    # 避免无声失败让用户困惑「为什么没反应」。
                    sync_document = workspace.root_document or active_document
                    if sync_document.is_latex_document() and sync_document.build_system.can_sync:
                        GLib.idle_add(workspace.actions.forward_sync)
                    else:
                        self._show_sync_unavailable_toast()

    def _show_sync_unavailable_toast(self):
        '''Ctrl+Click 前向同步不可用时提示用户。常见原因：PDF 尚未构建。'''
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(_('No PDF available for forward sync. Build the document first.'))
            toast.set_timeout(3)
            main_window.toast_overlay.add_toast(toast)

    def _on_motion_enter(self, controller, x, y):
        self._update_ctrl_cursor(controller)

    def _on_motion_motion(self, controller, x, y):
        self._update_ctrl_cursor(controller)

    def _on_motion_leave(self, controller):
        # 离开编辑区：恢复文本光标（I-beam）。
        self.view.source_view.set_cursor(self._cursor_text)

    def _update_ctrl_cursor(self, controller):
        '''Ctrl 按下时切换为 pointer 光标，提示可 Ctrl+Click 前向同步；
        否则恢复文本光标。仅在鼠标移动/进入时触发，静止状态下 Ctrl 按下/
        释放不刷新（移动鼠标即可刷新，局限性见 __init__ 注释）。'''
        modifiers = Gtk.accelerator_get_default_mod_mask()
        if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
            self.view.source_view.set_cursor(self._cursor_pointer)
        else:
            self.view.source_view.set_cursor(self._cursor_text)

    def on_secondary_buttonpress(self, controller, n_press, x, y):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            ServiceLocator.get_workspace().context_menu.popup_at_cursor(x, y)
        controller.reset()

    def on_keypress(self, controller, keyval, keycode, state):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if keyval in [_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB]:
            if state & modifiers == Gdk.ModifierType.SHIFT_MASK:
                # Shift+Tab：选区存在时反缩进，否则回退到 previous placeholder。
                if self.document.source_buffer.get_has_selection():
                    self.indent_selection(outdent=True)
                    return True
                self.document.select_previous_placeholder()
                if self.document.dot_selected():
                    return True
            else:
                # Tab：选区存在时缩进，否则处理 placeholder / 括号跳转。
                if self.document.source_buffer.get_has_selection():
                    self.indent_selection(outdent=False)
                    return True
                # 非 LaTeX 文档（BibTeX 等）无 placeholder / 括号跳转：
                # 直接插入缩进字符（Tab 或空格，取决于偏好设置）。
                if not self.document.is_latex_document():
                    if self.document.settings.get_value('preferences', 'spaces_instead_of_tabs'):
                        tab_width = self.document.settings.get_value('preferences', 'tab_width')
                        self.document.source_buffer.insert_at_cursor(' ' * tab_width)
                    else:
                        self.document.source_buffer.insert_at_cursor('\t')
                    return True
                self.document.select_next_placeholder()
                if self.document.dot_selected():
                    return True

                if not self.document.settings.get_value('preferences', 'tab_jump_brackets'): return False
                chars_at_cursor = self.document.get_chars_at_cursor(2)
                if chars_at_cursor in ['\\}', '\\)', '\\]']: forward_chars = 2
                elif len(chars_at_cursor) > 0 and chars_at_cursor[0] in ['}', ')', ']']: forward_chars = 1
                else: return False

                insert_iter = self.document.source_buffer.get_iter_at_mark(self.document.source_buffer.get_insert())
                insert_iter.forward_chars(forward_chars)
                self.document.source_buffer.place_cursor(insert_iter)
                return True

        return False

    def indent_selection(self, outdent=False):
        '''对选区覆盖的每一行前插 / 删除一个缩进单元。

        缩进单元取自偏好设置（spaces_instead_of_tabs / tab_width），与
        document.indent_text_with_whitespace_at_iter 保持一致。整段操作包在
        单个 user_action 内，保证可一次撤销。
        '''
        buffer = self.document.source_buffer
        use_spaces = self.document.settings.get_value('preferences', 'spaces_instead_of_tabs')
        tab_width = self.document.settings.get_value('preferences', 'tab_width')
        indent_unit = ' ' * tab_width if use_spaces else '\t'

        start, end = buffer.get_selection_bounds()
        first_line = start.get_line()
        last_line = end.get_line() if end.get_line_offset() > 0 else max(end.get_line() - 1, first_line)

        buffer.begin_user_action()
        for line_number in range(first_line, last_line + 1):
            found, line_start = buffer.get_iter_at_line(line_number)
            if outdent:
                # 删除行首至多一个缩进单元（空格数不超过 tab_width 或单个 \t）。
                line_text = self.document.get_line(line_number)
                if line_text.startswith('\t'):
                    delete_end = line_start.copy()
                    delete_end.forward_char()
                    buffer.delete(line_start, delete_end)
                elif line_text.startswith(' '):
                    spaces = 0
                    for ch in line_text:
                        if ch == ' ':
                            spaces += 1
                        else:
                            break
                    remove = min(spaces, tab_width)
                    delete_end = line_start.copy()
                    delete_end.forward_chars(remove)
                    buffer.delete(line_start, delete_end)
            else:
                buffer.insert(line_start, indent_unit)
        buffer.end_user_action()

    def on_scroll(self, controller, dx, dy):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
            if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
                self.zoom_threshold += dy
            else:
                self.zoom_threshold += dy * 0.05

            if self.zoom_threshold <= -1:
                font_desc = Pango.FontDescription.from_string(FontManager.font_string)
                font_desc.set_size(min(font_desc.get_size() * FontManager.FONT_ZOOM_FACTOR, FontManager.FONT_SIZE_MAX_PT * Pango.SCALE))
                FontManager.font_string = font_desc.to_string()
                FontManager.propagate_font_setting()
                self._schedule_zoom_persist()
                self.zoom_threshold = 0
            elif self.zoom_threshold >= 1:
                font_desc = Pango.FontDescription.from_string(FontManager.font_string)
                font_desc.set_size(max(font_desc.get_size() / FontManager.FONT_ZOOM_FACTOR, FontManager.FONT_SIZE_MIN_PT * Pango.SCALE))
                FontManager.font_string = font_desc.to_string()
                FontManager.propagate_font_setting()
                self._schedule_zoom_persist()
                self.zoom_threshold = 0
            return True
        return False

    def _schedule_zoom_persist(self):
        '''去抖持久化 font_string 到 settings。快速滚动时每个阈值跨越都
        即时更新 FontManager 内存值（propagate_font_setting 刷新所有文档字体），
        但 settings.set_value（磁盘写 + settings_changed 通知链）延迟到缩放
        停止 500ms 后执行一次。'''
        if self._zoom_persist_timeout_id is not None:
            GLib.Source.remove(self._zoom_persist_timeout_id)
        self._zoom_persist_timeout_id = GLib.timeout_add(500, self._persist_zoom)

    def _persist_zoom(self):
        self._zoom_persist_timeout_id = None
        ServiceLocator.get_settings().set_value('preferences', 'font_string', FontManager.font_string)
        return False

    def on_decelerate(self, controller, vel_x, vel_y):
        self.zoom_threshold = 0
        # 滚动手势结束，立即持久化（不再有后续缩放，无需等 500ms）。
        if self._zoom_persist_timeout_id is not None:
            GLib.Source.remove(self._zoom_persist_timeout_id)
            self._persist_zoom()

    def _on_window_active_changed(self, window, gparam):
        '''窗口获得焦点时立即检查外部磁盘变更，缩短用户切回 Setzer 时的
        感知延迟（原仅靠 2s 轮询）。save_date_loop 内部有 dialog_shown
        标志位防御重复弹窗。'''
        if window.is_active():
            # 用 one-shot idle 包装：save_date_loop 返回 continue_save_date_loop
            # （True），若直接传给 idle_add 会被 GLib 当作「返回 True 则重复
            # 调用」而无限触发。_check_external_changes_once 返回 False 终止。
            GLib.idle_add(self._check_external_changes_once)

    def _check_external_changes_once(self):
        '''窗口焦点触发的单次外部变更检查。返回 False 确保 idle 不重复。'''
        self.save_date_loop()
        return False

    def save_date_loop(self):
        if self.document.filename == None: return True
        if self.deleted_on_disk_dialog_shown_after_last_save: return True
        if self.changed_on_disk_dialog_shown_after_last_change:
            return True

        # 单次 os.stat 同时判定删除/变更（见 Document.get_disk_status），
        # 替代原 get_deleted_on_disk + get_changed_on_disk 两次独立 stat。
        deleted, changed = self.document.get_disk_status()
        if deleted:
            self.deleted_on_disk_dialog_shown_after_last_save = True
            self.document.source_buffer.set_modified(True)
            DialogLocator.get_dialog('document_deleted_on_disk').run({'document': self.document})
        elif changed:
            if self._can_auto_reload_silently():
                self._schedule_auto_reload()
            else:
                self.changed_on_disk_dialog_shown_after_last_change = True
                DialogLocator.get_dialog('document_changed_on_disk').run({'document': self.document}, self.changed_on_disk_cb)

        return self.continue_save_date_loop

    def _can_auto_reload_silently(self):
        '''判断是否满足自动静默重载条件。'''
        if self.document.filename is None:
            return False
        if not self.document.settings.get_value('preferences', 'auto_reload_on_external_change'):
            return False
        # 安全行为：本地有未保存修改时不静默覆盖，回退到对话框让用户决定。
        if self.document.source_buffer.get_modified():
            return False
        return True

    def _schedule_auto_reload(self):
        '''调度去防抖的自动静默重载。若已有挂起 timeout 则重置。'''
        if self._auto_reload_timeout_id is not None:
            GLib.Source.remove(self._auto_reload_timeout_id)
        self._auto_reload_timeout_id = GLib.timeout_add(1000, self._on_auto_reload_timeout)

    def _on_auto_reload_timeout(self):
        '''去防抖 timeout 触发：重新检查条件后执行静默重载或回退对话框。'''
        self._auto_reload_timeout_id = None

        if self.document.filename is None or self.document._is_shutdown:
            return False

        # 触发前重新读取磁盘状态：文件可能已被删除或恢复。
        deleted, changed = self.document.get_disk_status()
        if deleted:
            self.deleted_on_disk_dialog_shown_after_last_save = True
            self.document.source_buffer.set_modified(True)
            DialogLocator.get_dialog('document_deleted_on_disk').run({'document': self.document})
            return False
        if not changed:
            return False
        if not self.document.settings.get_value('preferences', 'auto_reload_on_external_change'):
            return False
        # 触发期间用户若开始编辑，回退到对话框避免覆盖未保存修改。
        if self.document.source_buffer.get_modified():
            self.changed_on_disk_dialog_shown_after_last_change = True
            DialogLocator.get_dialog('document_changed_on_disk').run({'document': self.document}, self.changed_on_disk_cb)
            return False

        # 执行静默重载：populate_from_filename 会更新 buffer、modified flag 与 save_date。
        self.document.populate_from_filename()
        return False

    def changed_on_disk_cb(self, do_reload):
        # 用户已通过对话框处理，取消任何挂起的自动重载。
        if self._auto_reload_timeout_id is not None:
            GLib.Source.remove(self._auto_reload_timeout_id)
            self._auto_reload_timeout_id = None
        if do_reload:
            self.document.populate_from_filename()
            self.document.source_buffer.set_modified(False)
        else:
            self.document.source_buffer.set_modified(True)
        self.changed_on_disk_dialog_shown_after_last_change = False
        self.document.update_save_date()

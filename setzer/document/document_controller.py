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
from setzer.settings.document_settings import DocumentSettings


# on_keypress 每次按键都跑，Gdk.keyval_from_name 模块级预计算避免每次 C 查表。
_KEYVAL_TAB = Gdk.keyval_from_name('Tab')
_KEYVAL_ISO_LEFT_TAB = Gdk.keyval_from_name('ISO_Left_Tab')
_KEYVAL_BACKSPACE = Gdk.keyval_from_name('BackSpace')
_KEYVAL_DELETE = Gdk.keyval_from_name('Delete')
_KEYVAL_RETURN = Gdk.keyval_from_name('Return')
_KEYVAL_KP_ENTER = Gdk.keyval_from_name('KP_Enter')
_KEYVAL_ESCAPE = Gdk.keyval_from_name('Escape')
_KEYVAL_UP = Gdk.keyval_from_name('Up')
_KEYVAL_DOWN = Gdk.keyval_from_name('Down')
_KEYVAL_LEFT = Gdk.keyval_from_name('Left')
_KEYVAL_RIGHT = Gdk.keyval_from_name('Right')


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

        # 失去焦点时关闭 undo 分组，避免用户切走后再切回时 Ctrl+Z 仍作用于
        # 上一段连续输入。
        self._focus_controller = Gtk.EventControllerFocus()
        self._focus_controller.connect('leave', self._on_focus_leave)
        self.view.source_view.add_controller(self._focus_controller)

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

        # Alt+Drag 列选（column selection）：通过 GestureDrag 识别 Alt 修饰键
        # 按下时从鼠标起点拖动形成矩形选区。同时也支持 Ctrl+点击添加光标
        # 和 Alt+点击添加光标（通过 primary_click_controller 的事件处理）。
        self._column_drag_controller = Gtk.GestureDrag()
        self._column_drag_controller.set_button(0)  # 接受所有按键（在回调中检查 Alt）
        self._column_drag_controller.set_propagation_phase(Gtk.PropagationPhase.TARGET)
        self._column_drag_controller.connect('drag-begin', self._on_column_drag_begin)
        self._column_drag_controller.connect('drag-update', self._on_column_drag_update)
        self._column_drag_controller.connect('drag-end', self._on_column_drag_end)
        self.view.source_view.add_controller(self._column_drag_controller)

        # 列选拖动状态
        self._column_dragging = False
        self._column_drag_start_iter = None

        # Ctrl+Click 添加光标（非前向同步）：在 primary_click_controller 的
        # on_primary_buttonpress 中已处理 Ctrl+Click 前向同步。这里额外识别
        # Ctrl+Click 不释放时添加额外光标的场景。
        self._ctrl_click_consumed_for_sync = False

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
        state = controller.get_current_event_state()

        if n_press == 1:
            # Alt+Click: 添加/移除额外光标（多光标模式）
            if state & Gdk.ModifierType.ALT_MASK:
                success, iter_at_click = self.view.source_view.get_iter_at_location(x, y)
                if success:
                    self._handle_alt_click(iter_at_click, x, y)
                return

            if state & modifiers == Gdk.ModifierType.CONTROL_MASK:
                workspace = ServiceLocator.get_workspace()
                active_document = workspace.get_active_document()
                if active_document is not None:
                    # 优先检查是否在 \ref{...} 上,如果是则跳转到定义
                    success, iter_at_click = self.view.source_view.get_iter_at_location(x, y)
                    if success:
                        label = active_document.get_label_at_iter(iter_at_click)
                        if label is not None:
                            # 在 ref 命令上:跳转到 \label{...} 定义
                            GLib.idle_add(self._do_jump_to_label, label)
                            return

                        # 在 \begin/\end 命令上:Ctrl+Click 跳转到配对端
                        if active_document.is_latex_document():
                            offset = iter_at_click.get_offset()
                            pair = active_document.begin_end_highlight.find_pair_at_offset(offset)
                            if pair is not None:
                                _, partner_span = pair
                                buffer = active_document.source_buffer
                                partner_iter = buffer.get_iter_at_offset(partner_span[0])
                                buffer.place_cursor(partner_iter)
                                active_document.scroll_cursor_onscreen()
                                active_document.view.source_view.grab_focus()
                                return

                    # 不在 ref 命令或环境命令上:执行 forward sync
                    # forward_sync action 仅在 can_sync 时启用,但 Ctrl+Click
                    # 绕过 action enablement 直接调用。这里复用相同的可同步
                    # 判定:不可同步时(PDF 未生成 / 非 LaTeX)弹 toast 提示,
                    # 避免无声失败让用户困惑「为什么没反应」。
                    sync_document = workspace.root_document or active_document
                    if sync_document.is_latex_document() and sync_document.build_system.can_sync:
                        GLib.idle_add(workspace.actions.forward_sync)
                    else:
                        self._show_sync_unavailable_toast()

    def _do_jump_to_label(self, label):
        r'''在 idle 时跳转到指定 label 的 \label{...} 定义位置。'''
        workspace = ServiceLocator.get_workspace()
        workspace.actions.actions['jump-to-definition'].activate(
            GLib.Variant('s', label))
        return False  # 确保 idle 只执行一次

    def _handle_alt_click(self, iter_at_click, x, y):
        """处理 Alt+Click 添加/移除额外光标。"""
        mc = self.document.multicursor
        # 检查点击位置是否靠近已有额外光标（移除）
        if mc.has_multiple_cursors():
            click_offset = iter_at_click.get_offset()
            # 检查是否点击了已有额外光标
            for cursor_mark, _ in mc.cursors:
                cursor_offset = cursor_mark.get_iter().get_offset()
                if abs(click_offset - cursor_offset) <= 2:
                    mc.remove_cursor_at_offset(click_offset)
                    return
            # 没有点击已有光标，添加新光标
            mc.add_cursor_at_iter(iter_at_click)
        else:
            # 只有主光标，添加额外光标
            mc.add_cursor_at_iter(iter_at_click)

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

    def _on_focus_leave(self, controller):
        # 失去焦点时关闭 undo 分组，避免切走后再切回时 Ctrl+Z 仍作用于
        # 上一段连续输入。
        self.document._close_undo_group()

    def _update_ctrl_cursor(self, controller):
        r'''Ctrl 按下时切换为 pointer 光标，提示可 Ctrl+Click 跳转
        （\ref 跳定义、\begin/\end 跳配对端、其它位置前向同步 PDF）；
        否则恢复文本光标。仅在鼠标移动/进入时触发，静止状态下 Ctrl 按下/
        释放不刷新（移动鼠标即可刷新，局限性见 __init__ 注释）。'''
        modifiers = Gtk.accelerator_get_default_mod_mask()
        if controller.get_current_event_state() & modifiers == Gdk.ModifierType.CONTROL_MASK:
            self.view.source_view.set_cursor(self._cursor_pointer)
        else:
            self.view.source_view.set_cursor(self._cursor_text)

    def _on_column_drag_begin(self, controller, x, y):
        """Alt+Drag 开始：检测 Alt 修饰键，设置起始位置。"""
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.ALT_MASK):
            return  # 非 Alt+Drag，忽略

        # Ctrl+Alt+Drag: 添加新的列选区到现有光标
        if state & Gdk.ModifierType.CONTROL_MASK:
            self._column_drag_additive = True
        else:
            self._column_drag_additive = False
            # 清除现有多光标（仅当非加法模式）
            if not self._column_drag_additive:
                self.document.multicursor.clear_all()

        self._column_dragging = True
        # GTK4: get_iter_at_location 返回 (found, iter) 元组，取 [1] 得 TextIter。
        self._column_drag_start_iter = self.view.source_view.get_iter_at_location(x, y)[1]
        self._column_drag_last_iter = self._column_drag_start_iter
        self._column_drag_last_x = x
        self._column_drag_last_y = y

    def _on_column_drag_update(self, controller, x, y):
        """Alt+Drag 进行中：更新列选区。"""
        if not self._column_dragging or self._column_drag_start_iter is None:
            return

        # 限制拖动距离（每像素更新过于频繁）
        dx = x - self._column_drag_last_x
        dy = y - self._column_drag_last_y
        if abs(dx) < 2 and abs(dy) < 2:
            return

        # GTK4: get_iter_at_location 返回 (found, iter) 元组，取 [1] 得 TextIter。
        current_iter = self.view.source_view.get_iter_at_location(x, y)[1]
        self._column_drag_last_iter = current_iter
        self._column_drag_last_x = x
        self._column_drag_last_y = y

        # 更新列选区
        self.document.multicursor.add_cursors_column(
            self._column_drag_start_iter, current_iter)

    def _on_column_drag_end(self, controller, x, y):
        """Alt+Drag 结束：完成列选区。"""
        if not self._column_dragging:
            return

        self._column_dragging = False
        self._column_drag_start_iter = None
        self._column_drag_last_iter = None

    def on_secondary_buttonpress(self, controller, n_press, x, y):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            # Detect label under cursor for right-click context menu
            label = self.document.get_label_at_cursor()
            workspace = ServiceLocator.get_workspace()
            workspace.context_menu.set_label_context(label)
            workspace.context_menu.popup_at_cursor(x, y)
        controller.reset()

    def on_keypress(self, controller, keyval, keycode, state):
        modifiers = Gtk.accelerator_get_default_mod_mask()

        # 撤销/重做快捷键：关闭当前 undo 分组，确保 can_undo/can_redo 立即可用。
        if keyval == Gdk.KEY_z and (state & modifiers & Gdk.ModifierType.CONTROL_MASK):
            self.document._close_undo_group()
        elif keyval == Gdk.KEY_y and (state & modifiers & Gdk.ModifierType.CONTROL_MASK):
            self.document._close_undo_group()
        elif keyval == Gdk.KEY_Z and (state & modifiers & Gdk.ModifierType.CONTROL_MASK) and (state & modifiers & Gdk.ModifierType.SHIFT_MASK):
            self.document._close_undo_group()

        mc = self.document.multicursor
        has_multi = mc.has_multiple_cursors() or mc.is_column_mode()

        # --- Multi-cursor specific shortcuts ---

        # Escape: 清除多光标
        if keyval == _KEYVAL_ESCAPE and has_multi:
            mc.clear_all()
            return True

        # Ctrl+D: 选中下一个相同词/匹配
        if keyval == Gdk.keyval_from_name('d') and state & modifiers == Gdk.ModifierType.CONTROL_MASK:
            mc.select_next_occurrence()
            return True

        # Ctrl+Shift+L: 选中所有相同词/匹配
        if keyval == Gdk.keyval_from_name('l') and state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            mc.select_all_occurrences()
            return True

        # Ctrl+Alt+Up: 每行上方添加光标
        if keyval == _KEYVAL_UP and state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            mc.add_cursor_above()
            return True

        # Ctrl+Alt+Down: 每行下方添加光标
        if keyval == _KEYVAL_DOWN and state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            mc.add_cursor_below()
            return True

        # --- Multi-cursor edit handling ---

        # Backspace: 多光标删除前一个字符
        if keyval == _KEYVAL_BACKSPACE and has_multi:
            if mc.handle_delete('backspace'):
                return True

        # Delete: 多光标删除后一个字符
        if keyval == _KEYVAL_DELETE and has_multi:
            if mc.handle_delete('delete'):
                return True

        # Tab / Shift+Tab: 多光标缩进/反缩进
        if keyval in [_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB] and has_multi:
            # 对所有光标位置应用相同的缩进操作
            if state & modifiers == Gdk.ModifierType.SHIFT_MASK:
                self._multi_cursor_indent(outdent=True)
            else:
                self._multi_cursor_indent(outdent=False)
            return True

        # Printable characters: 在所有光标位置插入
        if has_multi:
            # Gdk.keyval_to_unicode 返回 keyval 对应的 Unicode 码点（int），非字符返回 0。
            # GTK4 无 keyval_is_char；keyval_to_unicode 同时覆盖大小写与 Shift 修饰。
            unichar = Gdk.keyval_to_unicode(keyval)
            if unichar and unichar > 0:
                text = chr(unichar)
                if mc.handle_insert(text):
                    return True

        # Enter: 在所有光标位置换行（处理较复杂，先清除多光标让默认处理器处理）
        if keyval in [_KEYVAL_RETURN, _KEYVAL_KP_ENTER] and has_multi:
            # 简化处理：清除多光标，让 Enter 正常插入换行
            mc.clear_all()
            return False

        # --- Normal key handling (original code) ---

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
                    if DocumentSettings.get_effective_value(self.document, self.document.settings, 'spaces_instead_of_tabs'):
                        tab_width = DocumentSettings.get_effective_value(self.document, self.document.settings, 'tab_width')
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

    def _multi_cursor_indent(self, outdent=False):
        """多光标模式下的缩进/反缩进：对每个光标所在行执行操作。"""
        mc = self.document.multicursor
        buffer = self.document.source_buffer
        use_spaces = DocumentSettings.get_effective_value(
            self.document, self.document.settings, 'spaces_instead_of_tabs')
        tab_width = DocumentSettings.get_effective_value(
            self.document, self.document.settings, 'tab_width')
        indent_unit = ' ' * tab_width if use_spaces else '\t'

        # 收集所有需要编辑的行（去重）
        lines_to_edit = set()
        for cursor_mark, anchor_mark in mc.cursors:
            cursor_iter = cursor_mark.get_iter()
            lines_to_edit.add(cursor_iter.get_line())
            if anchor_mark:
                anchor_iter = anchor_mark.get_iter()
                lines_to_edit.add(anchor_iter.get_line())
        # 也包含主光标所在行
        primary = buffer.get_iter_at_mark(buffer.get_insert())
        lines_to_edit.add(primary.get_line())

        buffer.begin_user_action()
        for line_num in sorted(lines_to_edit, reverse=True):
            found, line_start = buffer.get_iter_at_line(line_num)
            if not found:
                continue

            line_text = self.document.get_line(line_num)
            if outdent:
                if line_text.startswith('\t'):
                    end_iter = line_start.copy()
                    end_iter.forward_char()
                    buffer.delete(line_start, end_iter)
                elif line_text.startswith(' '):
                    remove = min(len(line_text) - len(line_text.lstrip()), tab_width)
                    if remove > 0:
                        end_iter = line_start.copy()
                        end_iter.forward_chars(remove)
                        buffer.delete(line_start, end_iter)
            else:
                buffer.insert(line_start, indent_unit)
        buffer.end_user_action()
        mc.clear_all()

    def indent_selection(self, outdent=False):
        '''对选区覆盖的每一行前插 / 删除一个缩进单元。

        缩进单元取自偏好设置（spaces_instead_of_tabs / tab_width），与
        document.indent_text_with_whitespace_at_iter 保持一致。整段操作包在
        单个 user_action 内，保证可一次撤销。
        '''
        buffer = self.document.source_buffer
        use_spaces = DocumentSettings.get_effective_value(self.document, self.document.settings, 'spaces_instead_of_tabs')
        tab_width = DocumentSettings.get_effective_value(self.document, self.document.settings, 'tab_width')
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
                self._refresh_zoom_indicators()
                self.zoom_threshold = 0
            elif self.zoom_threshold >= 1:
                font_desc = Pango.FontDescription.from_string(FontManager.font_string)
                font_desc.set_size(max(font_desc.get_size() / FontManager.FONT_ZOOM_FACTOR, FontManager.FONT_SIZE_MIN_PT * Pango.SCALE))
                FontManager.font_string = font_desc.to_string()
                FontManager.propagate_font_setting()
                self._schedule_zoom_persist()
                self._refresh_zoom_indicators()
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

    def _refresh_zoom_indicators(self):
        '''Ctrl+滚轮缩放后，刷新所有显示的缩放百分比（状态栏 + 右键菜单按钮）。
        zoom_level 已由 propagate_font_setting 更新，这里复用 workspace 动作里
        统一的刷新逻辑。'''
        workspace = ServiceLocator.get_workspace()
        if workspace is not None:
            workspace.actions._update_zoom_indicators()

    def _persist_zoom(self):
        self._zoom_persist_timeout_id = None
        settings = ServiceLocator.get_settings()
        # 仅持久化缩放倍率到独立设置项；不再把缩放后的字号写回 settings.font_string
        # （那是干净基准，写入会导致 zoom_level 分母被污染、百分比被锁死）。
        settings.set_value('preferences', 'editor_font_zoom_level', FontManager.zoom_level)
        FontManager.saved_zoom_level = FontManager.zoom_level
        return False

    def on_decelerate(self, controller, vel_x, vel_y):
        self.zoom_threshold = 0
        # 滚动手势结束，立即持久化（不再有后续缩放，无需等 500ms）。
        if self._zoom_persist_timeout_id is not None:
            GLib.Source.remove(self._zoom_persist_timeout_id)
            self._persist_zoom()

    def _on_focus_leave(self, controller):
        """当 source_view 失去焦点时调用。

        预留钩子：未来可在此结束可能进行的 undo 分组（GTK 4 暂无
        inside_user_action 检测方法）。当前实现为空。
        """
        pass

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
        workspace = ServiceLocator.get_workspace()
        workspace._loading_start()
        try:
            self.document.populate_from_filename()
        finally:
            workspace._loading_finish()
        # 重载磁盘内容后唤醒 auto-build 倒计时（普通编辑经 'changed' 信号，
        # 但读盘期间 _loading_from_disk 使该信号被抑制，需显式触发）。
        workspace.auto_build.schedule_build_for_reload(self.document)
        return False

    def changed_on_disk_cb(self, do_reload):
        # 用户已通过对话框处理，取消任何挂起的自动重载。
        if self._auto_reload_timeout_id is not None:
            GLib.Source.remove(self._auto_reload_timeout_id)
            self._auto_reload_timeout_id = None
        if do_reload:
            workspace = ServiceLocator.get_workspace()
            workspace._loading_start()
            try:
                self.document.populate_from_filename()
            finally:
                workspace._loading_finish()
            self.document.source_buffer.set_modified(False)
            # 用户通过对话框选择重载后，同样唤醒 auto-build 倒计时。
            workspace.auto_build.schedule_build_for_reload(self.document)
        else:
            self.document.source_buffer.set_modified(True)
        self.changed_on_disk_dialog_shown_after_last_change = False
        self.document.update_save_date()

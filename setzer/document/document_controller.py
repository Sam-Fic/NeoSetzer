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

import os.path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gdk, GLib, Gtk, GObject, Pango, Adw

from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager
from setzer.document.math_preview import math_preview
from setzer.document.math_preview.math_preview_popover import MathPreviewPopover
from setzer.settings.document_settings import DocumentSettings
from setzer.document.smart_list import (
    SmartListNewlineKind,
    get_smart_list_newline_action,
)
from setzer.document.smart_typing import (
    get_smart_quote_insertion,
    is_auto_subscript_group,
    is_subscript_group_char,
    should_open_subscript,
)


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
_KEYVAL_HOME = Gdk.keyval_from_name('Home')
_KEYVAL_END = Gdk.keyval_from_name('End')
_KEYVAL_PAGE_UP = Gdk.keyval_from_name('Page_Up')
_KEYVAL_PAGE_DOWN = Gdk.keyval_from_name('Page_Down')
_KEYVAL_D = Gdk.keyval_from_name('d')
_KEYVAL_L = Gdk.keyval_from_name('l')
_KEYVAL_QUOTEDBL = Gdk.keyval_from_name('quotedbl')
# 纯导航键（方向/行首尾/翻页）：多光标激活时按下这些键先折叠附加光标，
# 再交给默认处理移动主光标，避免隐形光标滞留原地。
_NAV_KEYVALS = (_KEYVAL_UP, _KEYVAL_DOWN, _KEYVAL_LEFT, _KEYVAL_RIGHT,
                _KEYVAL_HOME, _KEYVAL_END, _KEYVAL_PAGE_UP, _KEYVAL_PAGE_DOWN)

# Alt+Drag 列选的最小有效位移（逻辑像素）。drag-begin 即 CLAIM 序列，
# 但最终位移小于该值时按"带抖动的点击"兜底成 Alt+Click（见
# _on_column_drag_end）——不构建列选区、不清空已有多光标。
_COLUMN_DRAG_MIN_OFFSET = 16


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

        # CAPTURE 阶段：Alt+Click 添加光标时必须先于 GtkTextView 内部手势
        # 运行并 CLAIM 事件序列，否则内部手势会同时把主光标移到点击处，
        # 造成主/附加光标重叠、打字双倍字符。
        self.primary_click_controller = Gtk.GestureClick()
        self.primary_click_controller.set_button(1)
        self.primary_click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.primary_click_controller.connect('pressed', self.on_primary_buttonpress)
        self.primary_click_controller.connect('released', self.on_primary_buttonrelease)
        self.view.source_view.add_controller(self.primary_click_controller)
        # Alt+Click 在 released 中处理（见 on_primary_buttonpress 注释）；
        # 此处记录按下前的主光标偏移，released 时恢复。
        self._alt_click_restore_offset = None

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

        # 自动下标/上标补出的 {…} 组：存闭合 } 处的 mark，下一次按键据此决定
        # 续写还是跳出。None 表示当前没有挂起的组。
        self._subscript_mark = None

        # 智能引号与自动下标的按键入口。刻意挂在 scrolled_window（source_view 的
        # 祖先）而不是 source_view：BracketCompletion 在 idle 里后挂到 source_view，
        # 同一 widget 的 capture 控制器实测按挂载逆序执行，它会先于本类 on_keypress
        # 看到按键并在「还在自动组里」的旧光标位置插配对字符（$x_i^2$ 打成
        # x_{i}^{2$$}$）。祖先的 capture 早于目标自身，规则因此总能先完成跳出重定位。
        smart_typing_controller = Gtk.EventControllerKey()
        smart_typing_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        smart_typing_controller.connect('key-pressed', self.on_smart_typing_keypress)
        self.document.view.scrolled_window.add_controller(smart_typing_controller)

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

        # 公式 hover 预览：独立 motion controller（上面的 _motion_controller
        # 只管 Ctrl+hover 指针反馈，二者互不掺和）。无修饰键驻留 DWELL_MS
        # 后弹预览弹窗；Ctrl（前向同步/加光标）、Alt（列选）、拖选、补全
        # 窗口期间一律不触发。非 autohide 弹窗由本类显式收起（seat-grab
        # 理由见 math_preview_popover.py / symbol_preview.py 注释）。
        self._math_hover_controller = Gtk.EventControllerMotion()
        self._math_hover_controller.connect('enter', self._on_math_hover_enter)
        self._math_hover_controller.connect('motion', self._on_math_hover_motion)
        self._math_hover_controller.connect('leave', self._on_math_hover_leave)
        self.view.source_view.add_controller(self._math_hover_controller)
        self._math_hover_timer_id = None
        self._math_hover_region = None       # 当前驻留的 MathRegion
        self._math_hover_request_id = None   # 在飞的预览请求 id
        self._math_hover_popover = None      # 懒创建（MathPreviewPopover）

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

        # 列选拖动状态。注意 GestureDrag 的 drag-update/drag-end 回调参数是
        # 相对 drag-begin 起点的偏移量，不是绝对坐标，需记录起点自行换算。
        self._column_dragging = False
        self._column_drag_additive = False
        self._column_drag_start_iter = None
        self._column_drag_start_x = 0
        self._column_drag_start_y = 0

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
        # 公式 hover 预览：取消驻留定时器，避免文档关闭后回调访问已销毁的
        # buffer / popover（motion controller 随 source_view 销毁自动断开）。
        self._math_hover_cancel_timer()
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

    def _is_mc_enabled(self, feature=None):
        '''多光标设置门控：总开关 multicursor_enabled 关闭时全部停用；
        feature 给定时再检查对应子开关（multicursor_alt_click 等）。'''
        settings = ServiceLocator.get_settings()
        if not settings.get_value('preferences', 'multicursor_enabled'):
            return False
        if feature is None:
            return True
        return settings.get_value('preferences', feature)

    def on_primary_buttonpress(self, controller, n_press, x, y):
        # 任何左键按下（点击/拖选开始）都收起公式预览弹窗。
        self._math_hover_popdown()
        modifiers = Gtk.accelerator_get_default_mod_mask()
        state = controller.get_current_event_state()

        # Alt+Click: 按下时不处理也不 CLAIM——CLAIM 会让列选手势
        # （GestureDrag）收不到该序列的任何事件，Alt+Drag 列选永远无法
        # 触发。真正的点击在 released 中处理；若按住后移动超过拖动阈值，
        # 列选手势会 CLAIM 序列，released 不会再触发，自然切换成列选。
        # 此处记录按下前的主光标偏移：内部手势在按下时会把主光标移到
        # 点击处，released 添加附加光标后需把主光标恢复回原位，避免
        # 主/附加光标重叠导致打字双倍插入。
        if state & Gdk.ModifierType.ALT_MASK:
            buffer = self.document.source_buffer
            self._alt_click_restore_offset = buffer.get_iter_at_mark(
                buffer.get_insert()).get_offset()
            return

        if n_press == 1:
            # 普通点击（无修饰键）：折叠所有附加光标。否则它们滞留在原位，
            # 用户看不见却会在下次打字时同时编辑多处隐藏位置。
            if not (state & modifiers):
                multicursor = getattr(self.document, 'multicursor', None)
                if multicursor is not None and (
                        multicursor.has_multiple_cursors()
                        or multicursor.is_column_mode()):
                    multicursor.clear_all()

            if state & modifiers == Gdk.ModifierType.CONTROL_MASK:
                workspace = ServiceLocator.get_workspace()
                active_document = workspace.get_active_document()
                if active_document is not None:
                    # 优先检查是否在 \ref{...} 上,如果是则跳转到定义
                    success, iter_at_click = self._iter_at_widget_coords(x, y)
                    if success:
                        label = active_document.get_label_at_iter(iter_at_click)
                        if label is not None:
                            # 在 ref 命令上:跳转到 \label{...} 定义
                            GLib.idle_add(self._do_jump_to_label, label)
                            return

                        # 在 \begin/\end 命令上:Ctrl+Click 跳转到配对端
                        # begin_end_highlight 延迟到 idle 构造（见
                        # Document._init_deferred_features），未就绪时跳过。
                        if active_document.is_latex_document():
                            offset = iter_at_click.get_offset()
                            beh = getattr(active_document, 'begin_end_highlight', None)
                            pair = beh.find_pair_at_offset(offset) if beh is not None else None
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

    def on_primary_buttonrelease(self, controller, n_press, x, y):
        """Alt+Click 的点击确认点：序列未被列选手势 CLAIM 才会走到这里，
        即这确实是一次点击而非拖动。任何 n_press 都处理（快速连点时
        GestureClick 上报 n_press≥2，每一次释放都应当添加/移除光标）。
        """
        state = controller.get_current_event_state()
        restore_offset = self._alt_click_restore_offset
        self._alt_click_restore_offset = None

        if not (state & Gdk.ModifierType.ALT_MASK):
            return

        # Alt+Click 添加/移除光标可在偏好设置中单独关闭。
        if not self._is_mc_enabled('multicursor_alt_click'):
            return

        success, iter_at_click = self._iter_at_widget_coords(x, y)
        if not success:
            return

        controller.set_state(Gtk.EventSequenceState.CLAIMED)

        # 恢复按下前的主光标位置（内部手势在按下时把它移到了点击处）。
        # 先恢复再添加附加光标：若点击处恰是主光标原位，去重逻辑会
        # 拒绝重叠的附加光标。
        buffer = self.document.source_buffer
        if restore_offset is not None:
            buffer.place_cursor(buffer.get_iter_at_offset(restore_offset))

        self._handle_alt_click(iter_at_click, x, y)

    def _do_jump_to_label(self, label):
        r'''在 idle 时跳转到指定 label 的 \label{...} 定义位置。'''
        workspace = ServiceLocator.get_workspace()
        workspace.actions.actions['jump-to-definition'].activate(
            GLib.Variant('s', label))
        return False  # 确保 idle 只执行一次

    def _iter_at_widget_coords(self, x, y):
        """事件坐标（source_view 部件坐标）→ TextIter，返回 (found, iter)。

        GTK4 的 get_iter_at_location 要求 **buffer** 坐标，而手势事件提供
        的是部件坐标（含行号栏宽度、左边距、滚动偏移）。直接传部件坐标
        会让点击位置向右偏移约一个行号栏宽度——等宽英文字体的偏移容易被
        当成"选到了相邻字符"，全宽中文字符下错位则非常明显。
        """
        buffer_x, buffer_y = self.view.source_view.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, x, y)
        return self.view.source_view.get_iter_at_location(buffer_x, buffer_y)

    def _handle_alt_click(self, iter_at_click, x, y):
        """处理 Alt+Click 添加/移除额外光标。"""
        mc = self.document.multicursor
        # 检查点击位置是否靠近已有额外光标（移除）
        if mc.has_multiple_cursors():
            click_offset = iter_at_click.get_offset()
            # 检查是否点击了已有额外光标
            buffer = self.document.source_buffer
            for cursor_mark, _anchor, _tag in mc.cursors:
                cursor_offset = buffer.get_iter_at_mark(cursor_mark).get_offset()
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

    # ---------- 公式 hover 预览 ----------

    def _math_hover_cancel_timer(self):
        if self._math_hover_timer_id is not None:
            GLib.Source.remove(self._math_hover_timer_id)
            self._math_hover_timer_id = None

    def _math_hover_popdown(self):
        '''取消驻留定时器并收起预览弹窗（离开/换区域/点击/滚动/编辑）。'''
        self._math_hover_cancel_timer()
        self._math_hover_region = None
        self._math_hover_request_id = None
        if self._math_hover_popover is not None:
            self._math_hover_popover.popdown()

    def _math_hover_allowed(self, controller):
        '''hover 预览的触发守卫：设置开关 + 无修饰键 + 无交互冲突。'''
        settings = ServiceLocator.get_settings()
        if not settings.get_value('preferences', 'math_hover_preview'):
            return False
        if getattr(self.document, 'math_preview', None) is None:
            return False
        # 任何修饰键都不触发：Ctrl+Click 是前向同步/跳转/加光标，
        # Alt 是列选——预览绝不与它们抢交互。
        modifiers = Gtk.accelerator_get_default_mod_mask()
        if controller.get_current_event_state() & modifiers:
            return False
        # 拖选进行中（按下未释放，含选区拖动）不触发。
        if self.primary_click_controller.is_active():
            return False
        # 补全窗口可见时不触发，避免两个浮层互相遮挡。
        autocomplete = getattr(self.document, 'autocomplete', None)
        widget = getattr(autocomplete, 'widget', None)
        if widget is not None and getattr(getattr(widget, 'model', None), 'is_active', False):
            return False
        return True

    def _on_math_hover_enter(self, controller, x, y):
        self._on_math_hover_motion(controller, x, y)

    def _on_math_hover_leave(self, controller):
        # 离开编辑区：取消驻留计时并收起预览弹窗。
        self._math_hover_popdown()

    def _on_math_hover_motion(self, controller, x, y):
        math_preview_module = getattr(self.document, 'math_preview', None)
        if not self._math_hover_allowed(controller):
            self._math_hover_popdown()
            return
        found, iter_at_pos = self._iter_at_widget_coords(x, y)
        if not found:
            self._math_hover_popdown()
            return
        region = math_preview_module.get_region_at(iter_at_pos.get_offset())
        if region is None:
            self._math_hover_popdown()
            return
        if self._math_hover_region is not None and region == self._math_hover_region:
            return  # 同一区域驻留中：定时器继续，不重置
        # 换了区域：收起旧弹窗（若有），为新区域重新驻留计时。
        self._math_hover_popdown()
        self._math_hover_region = region
        self._math_hover_timer_id = GLib.timeout_add(
            math_preview.DWELL_MS, self._on_math_hover_dwell)

    def _on_math_hover_dwell(self):
        '''驻留到期：弹加载态弹窗并请求渲染（内存命中/磁盘缓存/后台编译）。'''
        self._math_hover_timer_id = None
        math_preview_module = getattr(self.document, 'math_preview', None)
        region = self._math_hover_region
        if math_preview_module is None or region is None:
            return False
        popover = self._ensure_math_hover_popover()
        x, y, line_height = self._math_hover_region_rect(region)
        popover.show_loading_at(x, y, line_height)
        # request() 的回调一律异步（idle 回主线程），下面的赋值必然先于
        # 回调执行，_on_math_preview_result 的比对因此可靠。
        self._math_hover_request_id = math_preview_module.request(
            region.start, self._on_math_preview_result)
        if self._math_hover_request_id is None:
            # 不可预览（编译器缺失/区域超长）：不留一个空转的加载气泡。
            self._math_hover_popdown()
        return False

    def _on_math_preview_result(self, request_id, texture):
        if request_id != self._math_hover_request_id or self._math_hover_region is None:
            return  # 结果已过期：区域已换/已收起/文档已编辑
        if texture is None:
            self._math_hover_popdown()
            return
        self._math_hover_popover.show_texture(texture)

    def _ensure_math_hover_popover(self):
        if self._math_hover_popover is None:
            self._math_hover_popover = MathPreviewPopover(self.view.source_view)
        return self._math_hover_popover

    def _math_hover_region_rect(self, region):
        '''区域起点在 source_view 部件坐标系中的 (x, y, 行高)，用于弹窗定位。'''
        buffer = self.document.source_buffer
        iter_start = buffer.get_iter_at_offset(region.start)
        location = self.view.source_view.get_iter_location(iter_start)
        x, y = self.view.source_view.buffer_to_window_coords(
            Gtk.TextWindowType.WIDGET, location.x, location.y)
        line_height = location.height if location.height > 0 else 20
        return x, y, line_height

    def _on_column_drag_begin(self, controller, x, y):
        """Alt+Drag 开始：检测 Alt 修饰键，设置起始位置。

        立即 CLAIM 事件序列：拖动一开始就从 GtkTextView 内部手势手中
        接管，避免其拉出普通选区甚至反过来接管序列（不 CLAIM 会让
        列选前段毫无反应、表现"迟钝"）。带抖动的 Alt+Click 也会触发
        drag-begin——CLAIM 会阻断点击手势的 released，这类序列改在
        drag-end 按位移兜底判定（见 _on_column_drag_end）。
        """
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.ALT_MASK):
            return  # 非 Alt+Drag，忽略
        # Alt+Drag 列选可在偏好设置中单独关闭。
        if not self._is_mc_enabled('multicursor_alt_drag'):
            return
        multicursor = getattr(self.document, 'multicursor', None)
        if multicursor is None:
            return

        controller.set_state(Gtk.EventSequenceState.CLAIMED)

        # Ctrl+Alt+Drag: 添加新的列选区到现有光标（加法模式）
        self._column_drag_additive = bool(state & Gdk.ModifierType.CONTROL_MASK)

        self._column_dragging = True
        # drag-begin 的坐标是绝对坐标；drag-update/drag-end 给的是相对
        # 起点的偏移，记录起点供换算。
        self._column_drag_start_x = x
        self._column_drag_start_y = y
        self._column_drag_start_iter = self._iter_at_widget_coords(x, y)[1]

    def _column_drag_is_real(self, offset_x, offset_y):
        """位移是否构成真实列选拖动（而非连点时的指针抖动）。"""
        return max(abs(offset_x), abs(offset_y)) >= _COLUMN_DRAG_MIN_OFFSET

    def _on_column_drag_update(self, controller, x, y):
        """Alt+Drag 进行中：更新列选区。x/y 是相对起点的偏移。

        位移未达 _COLUMN_DRAG_MIN_OFFSET 前不构建列选区——那可能只是
        一次带抖动的点击，最终由 drag-end 按点击兜底处理。
        """
        if not self._column_dragging or self._column_drag_start_iter is None:
            return
        if not self._column_drag_is_real(x, y):
            return

        abs_x = self._column_drag_start_x + x
        abs_y = self._column_drag_start_y + y
        found, current_iter = self._iter_at_widget_coords(abs_x, abs_y)
        if not found:
            return

        # 更新列选区（multicursor 未就绪时无从添加，跳过）。
        # 非加法模式的清空由 add_cursors_column 在此执行——即只在真实
        # 拖动发生后，避免抖动"拖动"清空已有多光标。
        multicursor = getattr(self.document, 'multicursor', None)
        if multicursor is not None:
            # 收起原生选区到起点，避免残留高亮干扰列选显示。
            self.document.source_buffer.place_cursor(self._column_drag_start_iter)
            multicursor.add_cursors_column(
                self._column_drag_start_iter, current_iter,
                additive=self._column_drag_additive)

    def _on_column_drag_end(self, controller, x, y):
        """Alt+Drag 结束：位移足够时用最终位置构建列选区；位移过小
        （带抖动的点击）则兜底按 Alt+Click 处理——drag-begin 已 CLAIM
        序列、released 不会触发，必须在这里补上点击语义。
        """
        if not self._column_dragging:
            return

        multicursor = getattr(self.document, 'multicursor', None)
        if multicursor is not None and self._column_drag_start_iter is not None:
            abs_x = self._column_drag_start_x + x
            abs_y = self._column_drag_start_y + y
            if self._column_drag_is_real(x, y):
                # 真实拖动：构建最终列选区。
                found, end_iter = self._iter_at_widget_coords(abs_x, abs_y)
                if found:
                    self.document.source_buffer.place_cursor(
                        self._column_drag_start_iter)
                    multicursor.add_cursors_column(
                        self._column_drag_start_iter, end_iter,
                        additive=self._column_drag_additive)
            else:
                # 抖动点击：恢复主光标到按下前位置，再按释放点添加/移除
                # 附加光标（与 on_primary_buttonrelease 的处理保持一致）。
                found, end_iter = self._iter_at_widget_coords(abs_x, abs_y)
                if found:
                    buffer = self.document.source_buffer
                    restore_offset = self._alt_click_restore_offset
                    self._alt_click_restore_offset = None
                    if restore_offset is not None:
                        buffer.place_cursor(
                            buffer.get_iter_at_offset(restore_offset))
                    if self._is_mc_enabled('multicursor_alt_click'):
                        self._handle_alt_click(end_iter, abs_x, abs_y)

        self._column_dragging = False
        self._column_drag_additive = False
        self._column_drag_start_iter = None

    def on_secondary_buttonpress(self, controller, n_press, x, y):
        # 右键即将打开上下文菜单：预览弹窗是非 autohide 的，必须主动让位。
        self._math_hover_popdown()
        modifiers = Gtk.accelerator_get_default_mod_mask()

        if n_press == 1:
            # Detect label under cursor for right-click context menu
            label = self.document.get_label_at_cursor()
            workspace = ServiceLocator.get_workspace()
            # 右键处若是拼写错误词，向右键菜单注入建议/忽略/加词典项。
            spell = getattr(self.document, 'spellchecking', None)
            spell_word = spell.get_misspelled_word_at_position(x, y) \
                if spell is not None else None
            workspace.context_menu.set_spell_context(spell_word)
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

        mc = getattr(self.document, 'multicursor', None)
        if mc is None:
            return False
        has_multi = mc.has_multiple_cursors() or mc.is_column_mode()

        # --- Multi-cursor specific shortcuts ---

        # Escape: 清除多光标
        if keyval == _KEYVAL_ESCAPE and has_multi \
                and self._is_mc_enabled('multicursor_escape_clear'):
            mc.clear_all()
            return True

        # Ctrl+D: 选中下一个相同词/匹配
        if keyval == _KEYVAL_D and state & modifiers == Gdk.ModifierType.CONTROL_MASK \
                and self._is_mc_enabled('multicursor_select_next'):
            mc.select_next_occurrence()
            return True

        # Ctrl+Shift+L: 选中所有相同词/匹配（必须同时带 Ctrl 与 Shift）
        if keyval == _KEYVAL_L and (
                state & modifiers & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
                ) == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK) \
                and self._is_mc_enabled('multicursor_select_all'):
            mc.select_all_occurrences()
            return True

        # Ctrl+Alt+Up/Down: 每行上/下方添加光标。必须同时带 Ctrl 与 Alt
        # （只带 Alt 的 Alt+Up/Down 是移动行，不能误触）。
        ctrl_alt = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
        if keyval == _KEYVAL_UP and state & modifiers & ctrl_alt == ctrl_alt \
                and self._is_mc_enabled('multicursor_add_above'):
            mc.add_cursor_above()
            return True
        if keyval == _KEYVAL_DOWN and state & modifiers & ctrl_alt == ctrl_alt \
                and self._is_mc_enabled('multicursor_add_below'):
            mc.add_cursor_below()
            return True

        # 纯导航键（无修饰键或仅 Shift）：折叠附加光标后交给默认处理移动
        # 主光标，防止隐形光标滞留原地被后续编辑误中。上面的多光标快捷键
        # 均已提前消费，不受影响。
        if has_multi and keyval in _NAV_KEYVALS and not (
                state & modifiers & ~Gdk.ModifierType.SHIFT_MASK):
            mc.clear_all()
            has_multi = False
            return False

        # --- Multi-cursor edit handling ---
        # 编辑类操作要求光标已存在，且「多点编辑」开关开启。

        if has_multi and self._is_mc_enabled('multicursor_multiedit'):
            # Backspace: 多光标删除前一个字符
            if keyval == _KEYVAL_BACKSPACE:
                if mc.handle_delete('backspace'):
                    return True

            # Delete: 多光标删除后一个字符
            if keyval == _KEYVAL_DELETE:
                if mc.handle_delete('delete'):
                    return True

            # Tab / Shift+Tab: 多光标缩进/反缩进
            if keyval in (_KEYVAL_TAB, _KEYVAL_ISO_LEFT_TAB):
                self._multi_cursor_indent(
                    outdent=bool(state & modifiers & Gdk.ModifierType.SHIFT_MASK))
                return True

            # Enter: 在所有光标位置插入换行
            if keyval in (_KEYVAL_RETURN, _KEYVAL_KP_ENTER):
                if mc.handle_insert('\n'):
                    return True

            # 可打印字符: 在所有光标位置插入。排除 Ctrl/Alt 组合，
            # 否则会把 Ctrl+B 等快捷键当成字符 'b' 插入。
            if not (state & modifiers & (Gdk.ModifierType.CONTROL_MASK
                                         | Gdk.ModifierType.ALT_MASK)):
                # Gdk.keyval_to_unicode 返回 keyval 对应的 Unicode 码点（int），
                # 非字符返回 0；同时覆盖大小写与 Shift 修饰。
                unichar = Gdk.keyval_to_unicode(keyval)
                if unichar > 0:
                    if mc.handle_insert(chr(unichar)):
                        return True

        # --- Normal key handling (original code) ---

        if keyval in [_KEYVAL_RETURN, _KEYVAL_KP_ENTER]:
            has_default_modifier = bool(state & modifiers)
            if (not has_default_modifier
                    and not has_multi
                    and not self.document.source_buffer.get_has_selection()
                    and self.document.is_latex_document()
                    and self.handle_smart_list_newline()):
                return True

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

    def handle_smart_list_newline(self):
        '''Continue or exit a literal LaTeX ``\\item`` at the current line end.

        Returning ``False`` leaves Return to GtkSourceView, preserving ordinary
        newline, selection, and non-list behaviour. The actual text change is
        one user action so a single Undo restores the exact prior list state.
        '''
        buffer = self.document.source_buffer
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        line_text = self.document.get_line(insert_iter.get_line())
        action = get_smart_list_newline_action(
            line_text, insert_iter.get_line_offset())
        if action is None:
            return False

        buffer.begin_user_action()
        try:
            if action.kind == SmartListNewlineKind.CONTINUE:
                buffer.insert_at_cursor('\n' + action.indentation + '\\item ')
            else:
                line_start = buffer.get_iter_at_line(insert_iter.get_line())[1]
                buffer.delete(line_start, insert_iter)
                # The deletion moves the insertion mark to the empty line start.
                # Inserting only a newline leaves one blank line and moves the
                # cursor outside the list on the next line.
                buffer.insert_at_cursor('\n')
        finally:
            buffer.end_user_action()
        return True

    def on_smart_typing_keypress(self, controller, keyval, keycode, state):
        '''智能输入规则入口：引号配对与自动下标/上标花括号。

        独立于 on_keypress：multicursor 与其它编辑器子系统一样由 idle 延迟构造，
        on_keypress 在它还不存在时直接早退，规则不该跟着失效。多光标时整体让路——
        只改主光标会让各光标内容不一致。
        '''
        modifiers = Gtk.accelerator_get_default_mod_mask()
        if state & modifiers & (Gdk.ModifierType.CONTROL_MASK
                                | Gdk.ModifierType.ALT_MASK):
            return False
        mc = getattr(self.document, 'multicursor', None)
        if mc is not None and (mc.has_multiple_cursors() or mc.is_column_mode()):
            return False
        if not self.document.is_latex_document():
            return False
        if keyval == _KEYVAL_QUOTEDBL:
            return self.handle_smart_quote()
        return self.handle_smart_subscript(keyval)

    def handle_smart_quote(self):
        r'''打字时把 " 换成 LaTeX 的 `` `` `` / ``''``（一次 user-action 两个字符）。'''
        if not self.document.settings.get_value('preferences', 'enable_smart_quotes'):
            return False
        buffer = self.document.source_buffer
        if buffer.get_has_selection() or self._completion_popup_is_active():
            return False
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        # verbatim / \url / 命令名 / 数学区都带 no-spell-check 语法 class，
        # 这些位置保留字面 "（拼写检查用同一判据，见 spellchecking.py）。
        if self._has_syntax_class(insert_iter, 'no-spell-check'):
            return False
        insertion = get_smart_quote_insertion(
            self.document.get_chars_at_iter(insert_iter, -1))
        buffer.begin_user_action()
        try:
            buffer.insert_at_cursor(insertion)
        finally:
            buffer.end_user_action()
        return True

    def handle_smart_subscript(self, keyval):
        r'''把 _ / ^ 后的第一个字母数字包进花括号，组结束时把光标跳出去。

        只有「吃掉一个字符」时返回 True；组内续写与跳出后落字符都返回 False，
        把实际文本改动留给 GtkTextView 的默认插入——组内插入天然落在 } 之前，
        跳出时已把光标移到 } 之后。
        '''
        if not self.document.settings.get_value('preferences', 'enable_auto_subscript'):
            return False
        unichar = Gdk.keyval_to_unicode(keyval)
        # Tab=9 / Return=13 / BackSpace=8 都有非零 unicode，只挡 0 会让规则把它们
        # 当成可打印字符：先跳出组再交回默认处理，结果是「跳出 + 插入制表符」。
        # 控制字符一律不接管——Tab 的跳出本就由 tab_jump_brackets 负责。
        if unichar < 0x20:
            return False
        typed_char = chr(unichar)
        buffer = self.document.source_buffer
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())

        group_end_iter = self._checked_subscript_group_end(insert_iter)
        if group_end_iter is not None:
            if is_subscript_group_char(typed_char):
                return False
            # 输入 } 直接跳出整组；其余字符先把光标移到 } 之后再让默认处理插入，
            # x_{i}^2 才不会嵌进上一组花括号里。
            group_end_iter.forward_char()
            buffer.place_cursor(group_end_iter)
            return typed_char == '}'

        if buffer.get_has_selection() or self._completion_popup_is_active():
            return False
        if not should_open_subscript(
                self.document.get_chars_at_iter(insert_iter, -4), typed_char,
                self._has_syntax_class(insert_iter, 'math')):
            return False

        buffer.begin_user_action()
        try:
            buffer.insert_at_cursor('{' + typed_char + '}')
        finally:
            buffer.end_user_action()
        insert_iter = buffer.get_iter_at_mark(buffer.get_insert())
        insert_iter.backward_char()
        # right gravity：组内继续插字符时 mark 随之后移，始终停在 } 上。
        mark_name = 'auto_subscript_end' + str(
            ServiceLocator.get_increment('auto_subscript_end'))
        self._subscript_mark = buffer.create_mark(mark_name, insert_iter, False)
        buffer.place_cursor(insert_iter)
        return True

    def _checked_subscript_group_end(self, insert_iter):
        '''校验挂起的自动组，返回其 } 所在位置；不成立即丢弃 mark 返回 None。

        mark 会随缓冲区编辑自动平移，光凭「存在」不足以外推组仍然有效：用户可能
        删掉了 }，或把光标移开后又移回别处。因此每次都重读当前位置的字符与左侧
        文本。
        '''
        mark = self._subscript_mark
        if mark is None:
            return None
        buffer = self.document.source_buffer
        group_end_iter = buffer.get_iter_at_mark(mark)
        is_open_group = (
            group_end_iter.get_offset() == insert_iter.get_offset()
            and group_end_iter.get_char() == '}'
            and is_auto_subscript_group(
                self.document.get_chars_at_iter(insert_iter, -64)))
        if not is_open_group:
            self._drop_subscript_mark(mark)
            return None
        return group_end_iter

    def _drop_subscript_mark(self, mark):
        self._subscript_mark = None
        self.document.source_buffer.delete_mark(mark)

    def _completion_popup_is_active(self):
        '''补全弹窗激活时两条规则让路。autocomplete 属于 LaTeX 延迟构造的子系统，
        未就绪时文档上还没有该属性，不能直接取。'''
        autocomplete = getattr(self.document, 'autocomplete', None)
        return autocomplete is not None and autocomplete.is_active

    def _has_syntax_class(self, text_iter, syntax_class):
        '''查询语法引擎在 text_iter 处的 context class。

        GtkSourceView 惰性解析，未解析区域查不到 class，所以先对当前行
        ensure_highlight（实测热路径 ~2µs；跨行数学环境由解析器从最近的
        有效状态继承，无需扫描整篇）。

        行末/文档末尾的 iter 上没有字符可归属，class 恒为空——正敲到
        verbatim 或数学区最后一字时光标就在那里，规则会误判为普通文本。
        此时改问前一个字符：在它末尾插入的文本延续它的上下文。
        '''
        buffer = self.document.source_buffer
        _, line_start = buffer.get_iter_at_line(text_iter.get_line())
        line_end = line_start.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        buffer.ensure_highlight(line_start, line_end)
        if buffer.iter_has_context_class(text_iter, syntax_class):
            return True
        if not text_iter.ends_line():
            return False
        previous = text_iter.copy()
        if previous.get_offset() == 0:
            return False
        previous.backward_char()
        return buffer.iter_has_context_class(previous, syntax_class)

    def _multi_cursor_indent(self, outdent=False):
        """多光标模式下的缩进/反缩进：对每个光标（及其选区覆盖）所在行执行操作。

        缩进后保留多光标：mark 随文本自动平移，用户可继续编辑。
        """
        mc = self.document.multicursor
        buffer = self.document.source_buffer
        use_spaces = DocumentSettings.get_effective_value(
            self.document, self.document.settings, 'spaces_instead_of_tabs')
        tab_width = DocumentSettings.get_effective_value(
            self.document, self.document.settings, 'tab_width')
        indent_unit = ' ' * tab_width if use_spaces else '\t'

        # 收集所有需要编辑的行（去重）：选区覆盖端点之间的每一行
        lines_to_edit = set()
        for cursor_mark, anchor_mark, _tag in mc.cursors:
            cursor_line = buffer.get_iter_at_mark(cursor_mark).get_line()
            if anchor_mark:
                anchor_line = buffer.get_iter_at_mark(anchor_mark).get_line()
                lines_to_edit.update(
                    range(min(cursor_line, anchor_line),
                          max(cursor_line, anchor_line) + 1))
            else:
                lines_to_edit.add(cursor_line)
        # 列选模式下主光标位于矩形角落、由列光标覆盖，不单独编辑其所在行；
        # 普通多光标则包含主光标所在行。
        if not mc.is_column_mode():
            primary = buffer.get_iter_at_mark(buffer.get_insert())
            if buffer.get_has_selection():
                sel_bounds = buffer.get_selection_bounds()
                lines_to_edit.update(
                    range(sel_bounds[0].get_line(), sel_bounds[1].get_line() + 1))
            else:
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
        mc._queue_draw()

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
        # 滚动后文本相对视口移动，锚定在区域起点的预览弹窗会错位：直接收起
        # （Ctrl+滚轮缩放使文本重排，同样需要收起）。
        self._math_hover_popdown()
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

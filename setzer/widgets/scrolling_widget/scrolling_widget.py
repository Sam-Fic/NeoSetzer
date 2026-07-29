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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, GObject, Gdk, Gtk

import time

from setzer.helpers.observable import Observable


class ScrollingWidget(Observable):
    '''Scrollable canvas backed by a standard ``Gtk.ScrolledWindow``.

    The child ``Gtk.DrawingArea`` is sized to the full canvas dimensions so
    that the ``ScrolledWindow`` provides the overlay scrollbars and the
    viewport clipping. Event controllers attached to the (canvas-sized)
    ``content`` drawing area report coordinates in canvas/document space, so
    click handlers emit document coordinates directly. The motion controller
    is attached to the (viewport-sized) ``view`` so that the tracked cursor
    stays viewport-relative, matching the expectations of the preview
    controller/zoom manager.
    '''

    def __init__(self):
        Observable.__init__(self)

        self.scrolling_offset_x, self.scrolling_offset_y = 0, 0
        self.width, self.height = 0, 0
        self.cursor_x, self.cursor_y = None, None
        self.scrolling_multiplier = 2.5
        # 跟踪当前减速动画的 timeout 源 ID,以便在 widget 销毁或发起新的
        # 滚动时取消它,避免回调访问已释放的对象或在后台反复触发。
        self._deceleration_id = None

        self.view = Gtk.ScrolledWindow()
        self.view.set_overlay_scrolling(True)
        self.content = Gtk.DrawingArea()
        self.view.set_child(self.content)

        self.adjustment_x = self.view.get_hadjustment()
        self.adjustment_y = self.view.get_vadjustment()

        self.scrolling_controller = Gtk.EventControllerScroll()
        self.scrolling_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.KINETIC)
        self.scrolling_controller.connect('scroll', self.on_scroll)
        self.scrolling_controller.connect('decelerate', self.on_decelerate)
        self.content.add_controller(self.scrolling_controller)

        self.adjustment_x.connect('changed', self.on_adjustment_changed)
        self.adjustment_x.connect('value-changed', self.on_adjustment_changed)
        self.adjustment_y.connect('changed', self.on_adjustment_changed)
        self.adjustment_y.connect('value-changed', self.on_adjustment_changed)
        self.content.connect('resize', self.on_resize)

        self.motion_controller = Gtk.EventControllerMotion()
        self.motion_controller.connect('enter', self.on_enter)
        self.motion_controller.connect('motion', self.on_hover)
        self.motion_controller.connect('leave', self.on_leave)
        self.view.add_controller(self.motion_controller)

        self.primary_click_controller = Gtk.GestureClick()
        self.primary_click_controller.set_button(1)
        self.primary_click_controller.connect('pressed', self.on_primary_button_press)
        self.primary_click_controller.connect('released', self.on_primary_button_release)
        self.content.add_controller(self.primary_click_controller)

        self.secondary_click_controller = Gtk.GestureClick()
        self.secondary_click_controller.set_button(3)
        self.secondary_click_controller.connect('pressed', self.on_secondary_button_press)
        self.secondary_click_controller.connect('released', self.on_secondary_button_release)
        self.content.add_controller(self.secondary_click_controller)

        # 双指缩放手势：在 viewport (ScrolledWindow) 上监听捏合手势，
        # 因为捏合中心点需要 viewport-relative 坐标（与 cursor_x/y 同坐标系）。
        # 使用 'update' 信号而非 'zoom-changed'，确保每帧都能连续缩放。
        self.zoom_gesture = Gtk.GestureZoom()
        self.zoom_gesture.connect('begin', self.on_zoom_gesture_begin)
        self.zoom_gesture.connect('update', self.on_zoom_gesture_update)
        self.zoom_gesture.connect('end', self.on_zoom_gesture_end)
        self.zoom_gesture.connect('cancel', self.on_zoom_gesture_end)
        self.view.add_controller(self.zoom_gesture)

        # widget 销毁时自动取消减速动画 timeout。原实现仅靠各调用方
        # (preview.shutdown 等)手动调 cancel_deceleration，若有调用方遗漏，
        # ScrolledWindow 销毁后 timeout 仍会反复触发，访问已释放的 adjustment
        # 并持有 widget 引用阻碍 GC。连接 'destroy' 使本组件自清理。
        self.view.connect('destroy', self._on_destroy)

    def _on_destroy(self, widget=None):
        self.cancel_deceleration()

    def queue_draw(self):
        self.content.queue_draw()

    def scroll_to_position(self, position):
        yoffset = max(position[1], 0)
        xoffset = max(position[0], 0)
        self.scroll_now([xoffset, yoffset])

    def scroll_now(self, position):
        self.adjustment_x.set_value(position[0])
        self.adjustment_y.set_value(position[1])

    def on_scroll(self, controller, dx, dy):
        if abs(dx) > 0 and abs(dy / dx) >= 1: dx = 0

        modifiers = Gtk.accelerator_get_default_mod_mask()
        # 缓存 C 调用结果：原代码各调 2 次 get_current_event_state / get_unit。
        event_state = controller.get_current_event_state()
        unit = controller.get_unit()

        if event_state & modifiers == 0:
            if unit == Gdk.ScrollUnit.WHEEL:
                dx *= self.adjustment_x.get_page_size() ** (2/3)
                dy *= self.adjustment_y.get_page_size() ** (2/3)
            else:
                dy *= self.scrolling_multiplier
                dx *= self.scrolling_multiplier

            self.adjustment_x.set_value(self.adjustment_x.get_value() + dx)
            self.adjustment_y.set_value(self.adjustment_y.get_value() + dy)

        if event_state & modifiers == Gdk.ModifierType.CONTROL_MASK:
            if unit == Gdk.ScrollUnit.WHEEL:
                zoom_amount = dy * 0.1
            else:
                zoom_amount = (dy + dx) * 0.005
            self.add_change_code('zoom_request', zoom_amount)

    def on_decelerate(self, controller, vel_x, vel_y):
        if abs(vel_x) > 0 and abs(vel_y / vel_x) > 1: vel_x = 0

        # 取消任何正在进行的减速动画,避免多个 timeout 同时驱动滚动
        # (用户在减速期间再次滑动时会出现这种情况)。
        self.cancel_deceleration()
        data = {'starting_time': time.time(), 'initial_position': self.scrolling_offset_y, 'position': self.scrolling_offset_y, 'vel_y': vel_y * self.scrolling_multiplier}
        self.deceleration(data)

    def cancel_deceleration(self):
        '''取消当前正在运行的减速动画 timeout。应在 widget 销毁时调用,
        以免回调继续访问已释放的 Gtk 对象。'''
        if self._deceleration_id is not None:
            GLib.source_remove(self._deceleration_id)
            self._deceleration_id = None

    def deceleration(self, data):
        # 若已被取消(新滑动开始或 widget 销毁),立即停止。
        if data['position'] != self.scrolling_offset_y: return False

        time_elapsed = time.time() - data['starting_time']

        exponential_factor = 2.71828 ** (-4 * time_elapsed)
        position = data['initial_position'] + (1 - exponential_factor) * (data['vel_y'] / 4)
        velocity = data['vel_y'] * exponential_factor

        if abs(velocity) < 0.1:
            self._deceleration_id = None
            return False

        x = self.scrolling_offset_x
        y = position
        self.scroll_now([x, y])
        data['position'] = y
        self._deceleration_id = GObject.timeout_add(15, self.deceleration, data)

        return False

    def on_resize(self, drawing_area, width, height):
        self.content.queue_draw()

    def on_adjustment_changed(self, adjustment):
        self.scrolling_offset_y = self.adjustment_y.get_value()
        self.scrolling_offset_x = self.adjustment_x.get_value()
        self.add_change_code('scrolling_offset_changed')

        # The ScrolledWindow keeps the adjustment page size in sync with the
        # visible viewport; treat a page-size change as a viewport resize so
        # the preview can recompute zoom levels and layout.
        visible_width = self.adjustment_x.get_page_size()
        visible_height = self.adjustment_y.get_page_size()
        if visible_width != self.width or visible_height != self.height:
            self.width, self.height = visible_width, visible_height
            self.add_change_code('size_changed')

        self.content.queue_draw()

    def on_primary_button_press(self, controller, n_press, x, y):
        if n_press != 1: return
        modifiers = Gtk.accelerator_get_default_mod_mask()
        # ``x``/``y`` are already in canvas/document coordinates because the
        # drawing area is canvas-sized inside the ScrolledWindow.
        self.add_change_code('primary_button_press', (x, y, controller.get_current_event_state() & modifiers))

    def on_primary_button_release(self, controller, n_press, x, y):
        if n_press != 1: return
        modifiers = Gtk.accelerator_get_default_mod_mask()
        self.add_change_code('primary_button_release', (x, y, controller.get_current_event_state() & modifiers))

    def on_secondary_button_press(self, controller, n_press, x, y):
        if n_press != 1: return
        # claim 序列阻止事件继续传播。右键菜单在 released 时才弹出——若在
        # pressed 里 popup，popover 会被随后的 release 序列立刻关闭。
        controller.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_secondary_button_release(self, controller, n_press, x, y):
        if n_press != 1: return
        modifiers = Gtk.accelerator_get_default_mod_mask()
        # ``x``/``y`` are already in canvas/document coordinates because the
        # drawing area is canvas-sized inside the ScrolledWindow.
        self.add_change_code('secondary_button_press', (x, y, controller.get_current_event_state() & modifiers))

    def on_enter(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_hover(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_leave(self, controller):
        self.set_cursor_position(None, None)

    def set_cursor_position(self, x, y):
        if x != self.cursor_x or y != self.cursor_y:
            self.cursor_x, self.cursor_y = x, y
            self.add_change_code('hover_state_changed')
            self.content.queue_draw()

    # ---- 双指缩放 (pinch-to-zoom) ----

    def on_zoom_gesture_begin(self, gesture, n_events):
        if len(gesture.get_sequences()) < 2:
            return
        success, cx, cy = gesture.get_bounding_box_center()
        if not success:
            return
        self.add_change_code('pinch_zoom', ('begin', gesture.get_scale_delta(), cx, cy))

    def on_zoom_gesture_update(self, gesture, n_events):
        success, cx, cy = gesture.get_bounding_box_center()
        if not success:
            return
        self.add_change_code('pinch_zoom', ('update', gesture.get_scale_delta(), cx, cy))

    def on_zoom_gesture_end(self, gesture, n_events):
        self.add_change_code('pinch_zoom', ('end', gesture.get_scale_delta(), 0, 0))

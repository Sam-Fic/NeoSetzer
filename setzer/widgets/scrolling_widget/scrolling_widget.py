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
from gi.repository import GLib, Gdk, Gtk

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

    The drawing area is wrapped in a ``Gtk.Overlay`` (``self.canvas``) so that
    canvas-positioned widgets (e.g. per-page page indicator buttons) can be
    placed on top of the drawing area. Positioning goes through a
    ``Gtk.Fixed`` layer (``self.fixed``): ``Gtk`` margins are ``gint16``
    (capped at 32767px) which tall canvases (long PDF, high zoom) exceed,
    while ``Gtk.Fixed.move`` takes unconstrained float canvas coordinates.
    These widgets scroll with the content naturally, since they are children
    of the canvas-sized overlay.
    '''

    def __init__(self):
        Observable.__init__(self)

        self.scrolling_offset_x, self.scrolling_offset_y = 0, 0
        self.width, self.height = 0, 0
        self.cursor_x, self.cursor_y = None, None
        self.scrolling_multiplier = 2.5
        # 跟踪当前减速动画的 tick 回调 ID,以便在 widget 销毁或发起新的
        # 滚动时取消它,避免回调访问已释放的对象或在后台反复触发。
        # 改用 add_tick_callback + FrameClock 驱动(对齐显示器刷新率),
        # 取代原先 GObject.timeout_add(15ms) 的固定 66fps 上限及帧抖动。
        self._deceleration_id = None
        self._decel_data = None

        self.view = Gtk.ScrolledWindow()
        self.view.set_overlay_scrolling(True)
        # 把 canvas-sized drawing area 包到 Gtk.Overlay 里。这样可以在画布
        # 坐标系内放置额外的子 widget（如每页页码按钮），它们随滚动一起平移。
        # Overlay 大小 = 主子（drawing area）大小，所以 ScrolledWindow 的可滚动
        # 区域与原来一致。
        self.canvas = Gtk.Overlay()
        self.content = Gtk.DrawingArea()
        self.canvas.set_child(self.content)
        # 画布坐标定位层：Gtk margin 是 gint16（上限 32767px），长 PDF 高缩放
        # 下画布高度轻松超过该上限（触发 Gtk-CRITICAL: margin <= G_MAXINT16
        # 且定位失败）。Gtk.Fixed.move 用 float 坐标无此限制，overlay 子控件
        # 统一经 add_overlay_widget / move_overlay_widget 绝对定位。
        self.fixed = Gtk.Fixed()
        # Fixed 自身不对指针事件可命中（can-target=False 等价 GTK3 overlay 的
        # pass-through）：它全画布大小盖在 drawing area 上方，若可命中会把
        # 点击 / hover 事件从 drawing area 的 GestureClick / 控制器手里全部
        # 截走（放大镜按住、链接悬停等全部失效）。GTK4 pick 先递归子控件再
        # 检查自身 can-target，因此其中的交互子控件（页码徽章按钮）仍可点击。
        self.fixed.set_can_target(False)
        self.canvas.add_overlay(self.fixed)
        self.view.set_child(self.canvas)

        self.adjustment_x = self.view.get_hadjustment()
        self.adjustment_y = self.view.get_vadjustment()

        # scroll controller 挂到 canvas overlay 而不是 drawing area：
        # overlay 子（如按钮）位于 drawing area 上方。GTK 事件冒泡时，挂在
        # drawing area 上的 controller 收不到按钮上发生的滚动；改挂到 canvas
        # 后，按钮上的滚动事件通过冒泡被 canvas 上的 controller 捕获。
        self.scrolling_controller = Gtk.EventControllerScroll()
        self.scrolling_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.KINETIC)
        self.scrolling_controller.connect('scroll', self.on_scroll)
        self.scrolling_controller.connect('decelerate', self.on_decelerate)
        self.canvas.add_controller(self.scrolling_controller)

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

    def add_overlay_widget(self, widget):
        '''把 widget 放到画布坐标系的定位层（Gtk.Fixed）。

        widget 的位置随后通过 move_overlay_widget(widget, x, y) 以画布左上角
        为原点的坐标设定（float，无 Gtk margin 的 int16 上限）。widget 会随
        画布一起随滚动平移，不会停在视口固定位置。'''
        widget.set_halign(Gtk.Align.START)
        widget.set_valign(Gtk.Align.START)
        self.fixed.put(widget, 0, 0)

    def move_overlay_widget(self, widget, x, y):
        '''把定位层中的 widget 摆到画布坐标 (x, y)。

        坐标为 float，不受 Gtk margin 的 G_MAXINT16 上限约束。'''
        self.fixed.move(widget, float(x), float(y))

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

            # 用户主动滚动时取消正在进行的惯性减速,避免两套位移叠加。
            self.cancel_deceleration()
            self.adjustment_x.set_value(self.adjustment_x.get_value() + dx)
            self.adjustment_y.set_value(self.adjustment_y.get_value() + dy)
            # 已手动处理滚动,消费事件避免 ScrolledWindow 再次平移。
            return True

        if event_state & modifiers == Gdk.ModifierType.CONTROL_MASK:
            if unit == Gdk.ScrollUnit.WHEEL:
                zoom_amount = dy * 0.1
            else:
                zoom_amount = (dy + dx) * 0.005
            self.add_change_code('zoom_request', zoom_amount)
            # 已处理为缩放,消费事件避免 ScrolledWindow 同时做平移。
            return True

        return False

    def on_decelerate(self, controller, vel_x, vel_y):
        if abs(vel_x) > 0 and abs(vel_y / vel_x) > 1: vel_x = 0
        # 取消任何正在进行的减速动画,避免多个 tick 回调同时驱动滚动
        # (用户在减速期间再次滑动时会出现这种情况)。
        self.cancel_deceleration()
        # 用 FrameClock 帧时间驱动,而非 wall-clock。记录起始帧时间用于
        # 计算真实经过的帧数,使惯性速度与显示器刷新率无关(60/120/144Hz 一致)。
        self._decel_data = {
            'initial_position': self.scrolling_offset_y,
            'position': self.scrolling_offset_y,
            'vel_y': vel_y * self.scrolling_multiplier,
            'last_frame_time': 0,
        }
        self._deceleration_id = self.content.add_tick_callback(self._decel_tick)

    def cancel_deceleration(self):
        '''取消当前正在运行的减速动画 tick 回调。应在 widget 销毁时调用,
        以免回调继续访问已释放的 Gtk 对象。'''
        if self._deceleration_id is not None:
            self.content.remove_tick_callback(self._deceleration_id)
            self._deceleration_id = None
        self._decel_data = None

    def _decel_tick(self, widget, frame_clock):
        '''由 FrameClock 每帧调用,驱动减速惯性滚动。

        返回 True 继续下一帧,返回 False 停止并清理。使用 frame_clock 的
        真实帧间隔计算位移,确保动画与显示器刷新率严格同步,消除
        timeout_add 的帧率上限与抖动。'''
        data = self._decel_data
        if data is None:
            self._deceleration_id = None
            return False
        # 首帧仅记录基准帧时间,不做位移(避免用异常的 dt)。
        frame_time = frame_clock.get_frame_time()
        if data['last_frame_time'] == 0:
            data['last_frame_time'] = frame_time
            return True
        # 帧间隔(微秒)-> 秒。夹在合理区间,防止后台标签页恢复时
        # 出现巨大 dt 导致单帧跳变。
        dt = (frame_time - data['last_frame_time']) / 1_000_000.0
        data['last_frame_time'] = frame_time
        dt = min(max(dt, 0.0), 0.05)

        # 指数衰减惯性模型:每帧按真实 dt 推进,与帧率解耦。
        # vel_y 单位 px/s;衰减常数 4 (1/s)。
        decay = 2.71828 ** (-4 * dt)
        dy = data['vel_y'] * dt
        position = data['position'] + dy
        velocity = data['vel_y'] * decay

        if abs(velocity) < 0.1:
            self._deceleration_id = None
            self._decel_data = None
            return False

        x = self.scrolling_offset_x
        self.scroll_now([x, position])
        data['position'] = position
        data['vel_y'] = velocity

        return True

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

        # 关键性能优化(流畅滚动):
        # 滚动偏移变化时不再调用 content.queue_draw()。DrawingArea 是 canvas 尺寸,
        # ScrolledWindow 通过移动该子控件实现滚动,GSK 会平移已渲染的纹理(由
        # set_draw_func 产生的 surface)而 *不* 重新调用 draw —— 这是纯 GPU 合成,
        # 每帧零 cairo 重绘,从而达成与显示器刷新率对齐的高帧率连续滚动。
        # 真正的内容变化(缩放/布局/hover/点击/页面纹理就绪)由各自的调用方
        # 显式 queue_draw,无需在此冗余重绘。

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

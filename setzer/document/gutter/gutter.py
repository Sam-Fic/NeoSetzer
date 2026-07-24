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
from gi.repository import Gtk, Gdk, GObject, GLib, Pango, PangoCairo

import math, time

from setzer.helpers.timer import timer
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager
from setzer.app.font_manager import FontManager


class Gutter(object):

    def __init__(self, document, document_view):
        self.document = document
        self.document_view = document_view
        self.source_buffer = document.source_buffer
        self.source_view = document_view.source_view
        self.adjustment = self.document_view.scrolled_window.get_vadjustment()
        self.settings = ServiceLocator.get_settings()

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_halign(Gtk.Align.START)
        self.document_view.overlay.add_overlay(self.drawing_area)
        self.drawing_area.set_draw_func(self.draw)

        self.line_numbers_visible = self.settings.get_value('preferences', 'show_line_numbers')
        self.line_numbers_width = None

        self.code_folding_visible = self.document.is_latex_document() and self.settings.get_value('preferences', 'enable_code_folding')
        self.code_folding_width = None

        self.highlight_current_line = self.settings.get_value('preferences', 'highlight_current_line')

        self.char_width = FontManager.get_char_width(self.source_view)
        self.line_height = FontManager.get_line_height(self.source_view)
        self.total_width = None
        self.cursor_x, self.cursor_y = None, None
        self.hovered_folding_region = None

        # 字体度量缓存：char_width / line_height 仅在 FontManager.font_string
        # 变化（字体/缩放改变）时重算。原实现每次 update_size 都重建
        # Pango.Layout 并遍历显示行，而 update_size 在每次文本/光标/滚动变化
        # 时都被调用——是打字期间的主要无谓开销之一。
        self._last_font_string = FontManager.font_string

        self.layout = Pango.Layout(self.source_view.get_pango_context())
        self.layout.set_alignment(Pango.Alignment.RIGHT)

        # idle 去抖 id：5 路信号（文档变化/光标移动/滚动/折叠状态）共用一次
        # idle 刷新，避免单次按键触发 on_document_change + on_cursor_change
        # 两路各跑一遍 update_hovered_folding_region + update_size + queue_draw。
        self._refresh_idle_id = None
        # 跟踪减速动画 timeout 源 ID，以便在文档关闭时取消，避免回调访问
        # 已销毁的 adjustment。
        self._deceleration_id = None

        self.update_size()

        self.settings.connect('settings_changed', self.on_settings_changed)
        # 保存 settings 信号连接的回调引用，shutdown 时据此断开。
        # settings 是进程级单例，若不断开，单例会持续持有 gutter 回调引用，
        # 进而通过 gutter 持有 document，导致文档关闭后无法被 GC，且后续
        # 设置变更会调到已失效的 on_settings_changed。
        self._settings_callback = self.on_settings_changed
        self.document.connect('changed', self.on_document_change)
        self.document.connect('cursor_position_changed', self.on_cursor_change)
        self.document.code_folding.connect('folding_state_changed', self.on_folding_state_changed)
        self.document_view.scrolled_window.get_vadjustment().connect('changed', self.on_adjustment_changed)
        self.document_view.scrolled_window.get_vadjustment().connect('value-changed', self.on_adjustment_value_changed)

        scrolling_controller = Gtk.EventControllerScroll()
        scrolling_controller.set_flags(Gtk.EventControllerScrollFlags.BOTH_AXES | Gtk.EventControllerScrollFlags.KINETIC)
        scrolling_controller.connect('scroll', self.on_scroll)
        scrolling_controller.connect('decelerate', self.on_decelerate)
        self.drawing_area.add_controller(scrolling_controller)

        event_controller = Gtk.GestureClick()
        event_controller.connect('pressed', self.on_button_press)
        event_controller.set_button(1)
        self.drawing_area.add_controller(event_controller)

        event_controller = Gtk.EventControllerMotion()
        event_controller.connect('enter', self.on_enter)
        event_controller.connect('motion', self.on_hover)
        event_controller.connect('leave', self.on_leave)
        self.drawing_area.add_controller(event_controller)

    def shutdown(self):
        '''文档关闭时由 Document.shutdown 调用。断开 settings 单例信号连接、
        取消挂起的 idle 回调和减速动画 timeout。

        settings 是进程级单例，不断开会导致单例持续持有 gutter 回调引用，
        进而通过 gutter 持有 document，文档对象无法被 GC 回收，且后续设置
        变更会调到已失效的 on_settings_changed（访问已销毁的 drawing_area）。
        '''
        try:
            self.settings.disconnect('settings_changed', self._settings_callback)
        except (TypeError, KeyError, AttributeError):
            pass

        if self._refresh_idle_id is not None:
            GLib.source_remove(self._refresh_idle_id)
            self._refresh_idle_id = None

        self.cancel_deceleration()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'show_line_numbers':
            self.line_numbers_visible = self.settings.get_value('preferences', 'show_line_numbers')
            self.update_hovered_folding_region()
            self.update_size()
            self.drawing_area.queue_draw()

        if item == 'highlight_current_line':
            self.highlight_current_line = self.settings.get_value('preferences', 'highlight_current_line')
            self.drawing_area.queue_draw()

        if item == 'enable_code_folding':
            self.code_folding_visible = self.document.is_latex_document() and self.settings.get_value('preferences', 'enable_code_folding')
            self.update_hovered_folding_region()
            self.update_size()
            self.drawing_area.queue_draw()

    def on_document_change(self, document):
        self._schedule_refresh()

    def on_cursor_change(self, document):
        self._schedule_refresh()

    def on_adjustment_value_changed(self, adjustment):
        self._schedule_refresh()

    def on_adjustment_changed(self, adjustment):
        self._schedule_refresh()

    def on_folding_state_changed(self, code_folding):
        self._schedule_refresh()

    def _schedule_refresh(self):
        '''5 路信号共用一次 idle 刷新。单次按键至少触发 on_document_change +
        on_cursor_change 两路，去抖后只跑一遍 update + queue_draw。'''
        if self._refresh_idle_id is None:
            self._refresh_idle_id = GLib.idle_add(self._refresh_idle)

    def _refresh_idle(self):
        self._refresh_idle_id = None
        self.update_hovered_folding_region()
        self.update_size()
        self.drawing_area.queue_draw()
        return False

    def on_button_press(self, event_controller, n_press, x, y):
        if self.hovered_folding_region != None:
            if self.hovered_folding_region['is_folded']:
                self.document.code_folding.unfold(self.hovered_folding_region)
            else:
                self.document.code_folding.fold(self.hovered_folding_region)
        else:
            offset = self.adjustment.get_value()
            target = self.source_view.get_line_at_y(offset + y).target_iter
            self.source_buffer.place_cursor(target)
        return True

    def on_scroll(self, controller, dx, dy):
        modifiers = Gtk.accelerator_get_default_mod_mask()
        event_state = controller.get_current_event_state()

        if event_state & modifiers == 0:
            if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
                dy *= self.adjustment.get_page_size() ** (2/3)
            else:
                dy *= 2.5
            self.document_view.scrolled_window.set_kinetic_scrolling(False)
            self.adjustment.set_value(self.adjustment.get_value() + dy)
            self.document_view.scrolled_window.set_kinetic_scrolling(True)

    def on_decelerate(self, controller, vel_x, vel_y):
        # 取消任何正在进行的减速动画，避免多个 timeout 同时驱动滚动。
        self.cancel_deceleration()
        data = {'starting_time': time.time(), 'initial_position': self.adjustment.get_value(), 'position': self.adjustment.get_value(), 'vel_y': vel_y * 2.5}
        self.deceleration(data)

    def cancel_deceleration(self):
        '''取消当前正在运行的减速动画 timeout。'''
        if self._deceleration_id is not None:
            GLib.source_remove(self._deceleration_id)
            self._deceleration_id = None

    def deceleration(self, data):
        # 若已被取消（新滑动开始或文档关闭），立即停止。
        if data['position'] != self.adjustment.get_value():
            self._deceleration_id = None
            return False

        time_elapsed = time.time() - data['starting_time']
        exponential_factor = 2.71828 ** (-4 * time_elapsed)
        position = data['initial_position'] + (1 - exponential_factor) * (data['vel_y'] / 4)
        velocity = data['vel_y'] * exponential_factor
        if abs(velocity) >= 0.1:
            self.adjustment.set_value(position)
            data['position'] = position
            self._deceleration_id = GObject.timeout_add(15, self.deceleration, data)
        else:
            self._deceleration_id = None

        return False

    def on_enter(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_hover(self, controller, x, y):
        self.set_cursor_position(x, y)

    def on_leave(self, controller):
        self.set_cursor_position(None, None)

    def set_cursor_position(self, x, y):
        if x != self.cursor_x or y != self.cursor_y:
            self.cursor_x, self.cursor_y = x, y
            self.drawing_area.queue_draw()
        if self.cursor_x != None and self.cursor_x > self.total_width + 1:
            self.drawing_area.set_cursor_from_name('text')
        else:
            self.drawing_area.set_cursor_from_name('default')
        self.update_hovered_folding_region()

    def update_hovered_folding_region(self):
        self.hovered_folding_region = None
        if self.get_cursor_area() == 'code_folding':
            line = self.source_view.get_line_at_y(self.cursor_y + self.adjustment.get_value()).target_iter.get_line()
            self.hovered_folding_region = self.document.code_folding.get_region_by_line(line)

    def update_size(self):
        # 仅在字体/缩放变化时重算度量（get_char_width 新建 Pango.Layout，
        # get_line_height 遍历显示行，均不廉价）。其余情况复用缓存值。
        font_string = FontManager.font_string
        if font_string != self._last_font_string:
            self._last_font_string = font_string
            self.char_width = FontManager.get_char_width(self.source_view)
            self.line_height = FontManager.get_line_height(self.source_view)
        total_width = 0
        line_numbers_width = 0
        if self.line_numbers_visible:
            total_width += int(math.log10(self.source_buffer.get_line_count()) + 3) * self.char_width
            line_numbers_width = total_width
        if self.code_folding_visible:
            total_width += 3 * self.char_width
            self.code_folding_width = 3 * self.char_width
        else:
            self.code_folding_width = 0

        if total_width != self.total_width or line_numbers_width != self.line_numbers_width:
            self.total_width = total_width
            self.line_numbers_width = line_numbers_width
            self.layout.set_width((line_numbers_width - self.char_width) * Pango.SCALE)
            self.drawing_area.set_size_request(total_width, -1)
            self.document_view.margin.set_size_request(total_width, -1)

    #@timer
    def draw(self, drawing_area, ctx, width, height, data=None):
        if self.total_width == 0: return

        self.draw_background_and_border(ctx, width, height)
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('view_fg_color'))

        # 缓存 source_view 到局部变量：循环内每轮访问 2 次（get_line_yrange +
        # get_line_at_y），每次 self.source_view 经 __dict__ 哈希。50 可见行
        # × 60fps = 6000 次/秒无谓查找。
        source_view = self.source_view
        # 仅当没有文本选区时，光标行才作为“当前行”被加粗。有选区时
        # get_insert 落在选区末端，会把选区内每一行都误判为当前行，
        # 导致大量行号被加粗——行号加粗应只在光标独占某行时出现。
        if self.source_buffer.get_has_selection():
            current_line = -1
        else:
            current_line = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert()).get_line()
        # 提前计算循环上界，避免每次循环都调用 adjustment getter（C 调用）。
        scroll_top = self.adjustment.get_value()
        scroll_bottom = scroll_top + height
        # 起始可见行：从滚动顶部对应的行开始绘制。原实现先取一次 line_at_y
        # 作为 offset 初值，进入循环立刻又取一次，第一次取值仅用于初值条件
        # 判断，被立即覆盖——属无谓 C 调用。改为循环内统一获取。
        line_iter, offset = source_view.get_line_at_y(scroll_top)
        prev_line = None
        line = -1
        total_lines = self.source_buffer.get_end_iter().get_line()
        while offset <= scroll_bottom and line < total_lines:
            line = line_iter.get_line()
            line_height = source_view.get_line_yrange(line_iter).height
            if line != prev_line:
                drawing_offset = offset - scroll_top
                if drawing_offset < 0:
                    # 原代码 min(0, drawing_offset) 在 drawing_offset<0 时
                    # 返回 drawing_offset 自身（负 < 0），是无操作。意图显然
                    # 是 clamp 到 0：行号文字在视觉顶部裁切时仍贴边对齐，
                    # 不再向上溢出到 gutter 边界外。改用 max(0, ...) 达成。
                    drawing_offset = 0
                self.draw_line(ctx, line, current_line == line, drawing_offset, line_height)

            prev_line = line
            offset += line_height
            # 前进到下一行的 y 位置，下一轮循环据此判断是否还在视口内。
            line_iter, _ = source_view.get_line_at_y(offset)

        self.draw_hovered_folding_region(ctx)

    def draw_background_and_border(self, ctx, width, height):
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('view_bg_color'))
        ctx.rectangle(0, 0, self.total_width, height)
        ctx.fill()

    def draw_line(self, ctx, line, is_current, offset, line_height):
        if self.line_numbers_visible:
            self.draw_line_number(ctx, line, is_current, offset, line_height)

        if self.code_folding_visible:
            self.draw_folding_region(ctx, line, is_current, offset)

    def draw_line_number(self, ctx, line, is_current, offset, line_height):
        if is_current:
            text = '<b>' + str(line + 1) + '</b>'
        else:
            text = str(line + 1)

        if is_current and self.highlight_current_line:
            Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('line_highlighting_color'))
            ctx.rectangle(0, offset, self.total_width, line_height)
            ctx.fill()
            ctx.rectangle(self.total_width + 1, offset, self.char_width, line_height)
            ctx.fill()
            Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('view_fg_color'))

        # 非当前行用 set_text 避免 Pango markup 解析（每帧每可见行一次）。
        # 仅当前行需要 <b> 加粗，用 set_markup。
        if is_current:
            self.layout.set_markup(text)
        else:
            self.layout.set_text(text, -1)

        # 行号在第一条 visual line 内垂直居中。wrap 行时 line_height 是整个
        # 逻辑行高度，用 self.line_height（单行高度）居中才对齐到第一行。
        text_height = self.layout.get_extents().logical_rect.height / Pango.SCALE
        offset += (self.line_height - text_height) / 2
        ctx.move_to(0, offset)

        PangoCairo.show_layout(ctx, self.layout)

    def draw_folding_region(self, ctx, line, is_current, offset):
        folding_region = self.document.code_folding.get_region_by_line(line)
        if folding_region == None: return

        ctx.set_line_width(0)

        # 缓存到局部变量：原代码 self.char_width / self.line_numbers_width /
        # self.line_height 在下方被访问 13+ 次，每次都经 __dict__ 哈希。
        # 同时移除 xoff3/xoff4/xoff5/xoff9/yoff1/yoff4 等从未使用的死代码
        # （原代码计算了但下方 if/else 两分支均未引用）。
        cw = self.char_width
        lnw = self.line_numbers_width
        lh = self.line_height

        xoff1 = 6.5 * cw / 6
        xoff2 = 9.5 * cw / 6
        xoff6 = 6 * cw / 8
        xoff7 = 11 * cw / 8
        xoff8 = 16 * cw / 8
        yoff2 = 2.5 * cw / 4
        yoff3 = 5 * cw / 4
        yoff5 = 1 * cw / 2
        line_gap_folded = ((lh - cw * 5 / 4) / 2)
        line_gap_unfolded = ((lh - cw * 1 / 2) / 2)

        if folding_region['is_folded']:
            ctx.move_to(lnw + xoff1, offset + line_gap_folded + 0.5)
            ctx.line_to(lnw + xoff2, offset + line_gap_folded + yoff2 + 0.5)
            ctx.line_to(lnw + xoff1, offset + line_gap_folded + yoff3 + 0.5)
            ctx.line_to(lnw + xoff1, offset + line_gap_folded + 0.5)
            ctx.fill()
            for i in range(4):
                ctx.rectangle(lnw + (i + 0.5) * cw, offset + lh, cw / 2, 1)
                ctx.fill()
        else:
            ctx.move_to(lnw + xoff6, offset + line_gap_unfolded)
            ctx.line_to(lnw + xoff7, offset + line_gap_unfolded + yoff5)
            ctx.line_to(lnw + xoff8, offset + line_gap_unfolded)
            ctx.line_to(lnw + xoff6, offset + line_gap_unfolded)
            ctx.fill()

    def draw_hovered_folding_region(self, ctx):
        Gdk.cairo_set_source_rgba(ctx, ColorManager.get_ui_color('code_folding_hover'))
        if self.hovered_folding_region != None:
            region = self.hovered_folding_region
            yrange_1 = self.source_view.get_line_yrange(self.source_buffer.get_iter_at_line(region['starting_line']).iter)
            yrange_2 = self.source_view.get_line_yrange(self.source_buffer.get_iter_at_line(region['ending_line']).iter)

            ctx.rectangle(self.total_width - 1, yrange_1.y - self.adjustment.get_value(), 3, yrange_2.y - yrange_1.y + yrange_2.height)
            ctx.fill()

    def get_cursor_area(self):
        if self.cursor_x == None: return None
        offset = 0

        if self.line_numbers_visible:
            offset += self.line_numbers_width
        if self.cursor_x <= offset: return 'line_numbers'

        if self.code_folding_visible:
            offset += self.code_folding_width
        if self.cursor_x <= offset: return 'code_folding'

        return None



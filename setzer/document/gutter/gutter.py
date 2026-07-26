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
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Gdk, GObject, GLib, Pango, PangoCairo, GtkSource, Gsk

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
        self._folding_icon_nodes = dict()

        # 字体度量缓存：char_width / line_height 仅在字体实际变化时重算。
        # 原实现每次 update_size 都重建 Pango.Layout 并遍历显示行，而
        # update_size 在每次文本/光标/滚动变化时都被调用——是打字期间
        # 的主要无谓开销之一。
        #
        # 仅比对 font_string 不足以检测字体实际变化：__init__ 在 source_view
        # realize 前就调用了 get_line_height/get_char_width，此时 textview.monospace
        # CSS 尚未应用到 widget 的 pango context，拿到的度量要么为零、要么基于
        # 系统默认字体（如 MiWithJBMonoNL 10pt），而非 CSS 指定的字体。font_string
        # 在此过程中并不变化，缓存永不失效，self.line_height 长期为陈旧值。
        #
        # self.char_width / self.line_height 仅用于字符宽度估算、gutter 总宽
        # 计算等，不参与"行内竖直定位"——竖直定位一律使用 draw() 主循环传入
        # 的真实行高（source_view.get_line_yrange().height，含行距）。
        # 之所以要在 update_size + draw 两处都做 font_description 比对，
        # 是因为 self.line_height 缓存的是 realize 前的字体度量，若首帧 draw
        # 早于任何信号触发 update_size，比对能及时用实际字体重算，避免
        # gutter 总宽/字符宽用陈旧值。
        self._last_font_string = FontManager.font_string
        self._last_actual_font_str = None

        self.layout = Pango.Layout(self.source_view.get_pango_context())
        self.layout.set_alignment(Pango.Alignment.RIGHT)
        # 当前行加粗用的独立 Layout。原先只有这一个共享 Layout，循环里对当前行
        # set_markup('<b>')、对其它行 set_text()，两者复用同一 Layout 在 GTK cairo
        # 重绘时偶发加粗属性泄漏，表现为“光标明明在一行，却有多行号被加粗”。
        # 拆成两个 Layout 后，加粗与普通文本互不干扰，不再串扰。
        self.layout_current = Pango.Layout(self.source_view.get_pango_context())
        self.layout_current.set_alignment(Pango.Alignment.RIGHT)

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
        self.source_buffer.connect('notify::style-scheme', self.on_scheme_changed)

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

    def on_scheme_changed(self, buffer, pspec):
        self.drawing_area.queue_draw()

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

    def _refresh_font_metrics_if_changed(self):
        # 缓存失效检查：除 FontManager.font_string（用户字体/缩放设置）外，
        # 还需比对 source_view 实际生效的 font_description。原因是 Gutter.__init__
        # 在 source_view realize 前就首次取了度量，此时 textview.monospace CSS
        # 尚未应用到 widget 的 pango context，拿到的度量要么为零、要么基于系统
        # 默认字体（如 MiWithJBMonoNL 10pt）而非 CSS 指定的字体（如 monospace
        # 11pt），导致 self.line_height 偏离实际行高。
        #
        # font_string 在此过程中并不变化，仅比对 font_string 无法触发重算。
        # get_font_description / to_string 均为 O(1)，不会成为绘制热点。
        #
        # 该检查在 update_size（idle 去抖路径）和 draw（每帧）中都调用：
        # update_size 依赖信号触发，但 source_view realize 后的首帧 draw 可能
        # 早于任何信号到达（cursor/scroll/change 都尚未发生），此时若不做
        # 检查，self.line_height 仍是 realize 前的陈旧值，draw_folding_region
        # 等用到 self.line_height 的地方会错位。
        font_string = FontManager.font_string
        actual_fd = self.source_view.get_pango_context().get_font_description()
        actual_font_str = actual_fd.to_string() if actual_fd is not None else ''
        if (font_string != self._last_font_string
                or actual_font_str != self._last_actual_font_str):
            self._last_font_string = font_string
            self._last_actual_font_str = actual_font_str
            self.char_width = FontManager.get_char_width(self.source_view)
            self.line_height = FontManager.get_line_height(self.source_view)
            # 图标渲染节点是按尺寸缓存的，字体变化导致尺寸变化需失效重算。
            self._folding_icon_nodes.clear()

    def update_size(self):
        self._refresh_font_metrics_if_changed()
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
            self.layout_current.set_width((line_numbers_width - self.char_width) * Pango.SCALE)
            self.drawing_area.set_size_request(total_width, -1)
            self.document_view.margin.set_size_request(total_width, -1)

    #@timer
    def draw(self, drawing_area, ctx, width, height, data=None):
        if self.total_width == 0: return

        # realize 后首帧可能早于任何信号到达 update_size，此处补一次字体
        # 度量检查，确保用于 gutter 总宽/字符宽的 self.line_height、
        # self.char_width 不会用到 realize 前的陈旧值。O(1) 检查，
        # 仅在字体变化时重算。
        self._refresh_font_metrics_if_changed()

        ctx.save()
        ctx.rectangle(0, 0, width, height)
        ctx.clip()

        self.draw_background_and_border(ctx, width, height)
        fg, _ = self._get_scheme_colors()
        Gdk.cairo_set_source_rgba(ctx, fg)

        # 缓存 source_view 到局部变量：循环内每轮访问 2 次（get_line_yrange +
        # get_line_at_y），每次 self.source_view 经 __dict__ 哈希。50 可见行
        # × 60fps = 6000 次/秒无谓查找。
        source_view = self.source_view
        # 光标所在行始终作为“当前行”加粗，无论是否有文本选区。
        # get_insert() 返回单一光标位置，get_line() 返回单一行号，
        # current_line == line 只会匹配一行，不会因选区而多行加粗。
        # 之前曾误以为选区会导致多行误判而加 has_selection 短路，反而
        # 让加粗在选区时全部消失——点击/选中/取消选区时加粗时有时无，
        # 行为不可预测。恢复为始终加粗光标行，规则清晰一致。
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
                self.draw_line(ctx, line, current_line == line, drawing_offset, line_height)

            prev_line = line
            offset += line_height
            line_iter, _ = source_view.get_line_at_y(offset)

        self.draw_hovered_folding_region(ctx)

        ctx.restore()

    def draw_background_and_border(self, ctx, width, height):
        fg, bg = self._get_scheme_colors()
        Gdk.cairo_set_source_rgba(ctx, bg)
        ctx.rectangle(0, 0, self.total_width, height)
        ctx.fill()

    def _get_scheme_colors(self):
        scheme = self.source_buffer.get_style_scheme()
        style = scheme.get_style('text') if scheme else None

        def _parse_hex(s):
            if not s:
                return None
            s = s.strip().lstrip('#')
            if len(s) == 6:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0, alpha=1.0)
            elif len(s) == 8:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0,
                                alpha=int(s[6:8], 16)/255.0)
            return None

        fg = _parse_hex(style.props.foreground) if style else None
        bg = _parse_hex(style.props.background) if style else None
        if fg is None:
            fg = ColorManager.get_ui_color('view_fg_color')
        if bg is None:
            bg = ColorManager.get_ui_color('view_bg_color')
        return fg, bg

    def _get_current_line_bg(self):
        scheme = self.source_buffer.get_style_scheme()
        style = scheme.get_style('current-line') if scheme else None

        def _parse_hex(s):
            if not s:
                return None
            s = s.strip().lstrip('#')
            if len(s) == 6:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0, alpha=1.0)
            elif len(s) == 8:
                return Gdk.RGBA(red=int(s[0:2], 16)/255.0,
                                green=int(s[2:4], 16)/255.0,
                                blue=int(s[4:6], 16)/255.0,
                                alpha=int(s[6:8], 16)/255.0)
            return None

        cl_bg = _parse_hex(style.props.background) if style else None
        if cl_bg is None:
            cl_bg = ColorManager.get_ui_color('line_highlighting_color')
        return cl_bg

    def draw_line(self, ctx, line, is_current, offset, line_height):
        if self.line_numbers_visible:
            self.draw_line_number(ctx, line, is_current, offset, line_height)

        if self.code_folding_visible:
            self.draw_folding_region(ctx, line, is_current, offset, line_height)

    def draw_line_number(self, ctx, line, is_current, offset, line_height):
        fg, bg = self._get_scheme_colors()

        if is_current:
            text = '<b>' + str(line + 1) + '</b>'
        else:
            text = str(line + 1)

        # 行高亮（背景色）仅在无文本选区时绘制，与 GtkSourceView 的
        # current-line-highlighting 在选区时自动取消的行为保持一致。
        # 行号加粗（上面的 <b>）不受此影响，光标行始终加粗。
        if is_current and self.highlight_current_line and not self.source_buffer.get_has_selection():
            cl_bg = self._get_current_line_bg()
            Gdk.cairo_set_source_rgba(ctx, cl_bg)
            ctx.rectangle(0, offset, self.total_width, line_height)
            ctx.fill()
            ctx.rectangle(self.total_width + 1, offset, self.char_width, line_height)
            ctx.fill()
            Gdk.cairo_set_source_rgba(ctx, fg)

        # 非当前行用普通 Layout + set_text；当前行用独立的 layout_current +
        # set_markup('<b>')。两个 Layout 互不复用，避免加粗属性泄漏到邻近行。
        if is_current:
            self.layout_current.set_markup(text)
            layout = self.layout_current
        else:
            self.layout.set_text(text, -1)
            layout = self.layout

        # 行号颜色：与源码同用 scheme 的 text foreground 色。
        Gdk.cairo_set_source_rgba(ctx, fg)

        # 真正的行内垂直居中：用 layout 自身的 logical_rect 度量来居中，
        # 而非混用 font metrics 的 line_height，避免两套 Pango 计算路径
        # 的亚像素差异导致的偏移。text_height 与 line_height 都来自同一
        # layout 渲染路径，居中结果稳定。
        text_rect = layout.get_extents().logical_rect
        text_height = text_rect.height / Pango.SCALE
        vertical_offset = (line_height - text_height) / 2
        ctx.move_to(0, offset + vertical_offset)

        PangoCairo.show_layout(ctx, layout)

    def _get_folding_icon_node(self, icon_name, size):
        # 把系统图标（symbolic）渲染成 Gsk.RenderNode 并缓存，避免每帧重复
        # lookup + snapshot。size 跟随字符宽度变化，按 (名称, 尺寸) 缓存即可。
        key = (icon_name, size)
        node = self._folding_icon_nodes.get(key)
        if node is not None:
            return node
        theme = Gtk.IconTheme.get_for_display(self.source_view.get_display())
        paintable = theme.lookup_icon(icon_name, None, size, 1,
                                      Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0))
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, size, size)
        node = snapshot.to_node()
        self._folding_icon_nodes[key] = node
        return node

    def draw_folding_region(self, ctx, line, is_current, offset, line_height):
        folding_region = self.document.code_folding.get_region_by_line(line)
        if folding_region == None: return

        cw = self.char_width
        lnw = self.line_numbers_width
        # 用 draw() 主循环传来的真实行高（含行距，来自 get_line_yrange），
        # 而非 self.line_height（ascent+descent，不含行距）。两者不等时
        # 用后者做居中会把图标算得偏下。
        lh = line_height

        # 用系统自带 symbolic 箭头替换原先手绘的三角形：
        #   is_folded=True  → 区域被折叠、"可展开"，显示右指箭头 pan-end-symbolic
        #   is_folded=False → 区域已展开，显示下指箭头 pan-down-symbolic
        # 颜色随主题（symbolic 默认前景色），与系统其它控件风格一致。
        icon_name = 'pan-end-symbolic' if folding_region['is_folded'] else 'pan-down-symbolic'
        size = max(8, round(cw * 1.5))
        node = self._get_folding_icon_node(icon_name, size)
        if node is not None:
            # 行高方向居中：图标中心对齐到行中心（offset + lh/2）。
            # 用 round 对齐到整数像素，避免半像素 translate 被 cairo 四舍五入
            # 到像素网格，导致图标视觉上偏上/偏下、看起来不居中。
            x = round(lnw + (self.code_folding_width - size) / 2)
            y = round(offset + (lh - size) / 2)
            ctx.save()
            ctx.translate(x, y)
            node.draw(ctx)
            ctx.restore()

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



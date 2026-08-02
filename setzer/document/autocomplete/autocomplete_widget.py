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
from gi.repository import Gtk, Gdk

import setzer.document.autocomplete.autocomplete_widget_viewgtk as autocomplete_view
from setzer.app.service_locator import ServiceLocator
from setzer.app.font_manager import FontManager


class AutocompleteWidget(object):

    def __init__(self, model):
        self.main_window = ServiceLocator.get_main_window()
        self.model = model
        self.document = self.model.document
        self.source_view = self.document.view.source_view
        self.source_buffer = self.model.document.source_buffer

        self.view = autocomplete_view.AutocompleteWidgetView(self)

        self.line_height = FontManager.get_line_height(self.source_view)
        self.char_width = FontManager.get_char_width(self.source_view)
        # 字体度量缓存：line_height / char_width 仅在 FontManager.font_string
        # 变化（字体/缩放改变）时重算，与 gutter.py 的缓存范式一致。
        self._last_font_string = FontManager.font_string
        self.height = None
        self.shortcutsbar_height = None
        self.x_position, self.y_position = (None, None)
        # 初始 False：widget 构造时 source_view 可能尚未获焦（如刚创建文档），
        # has_focus() 返回 False 导致 focus_hide=True，首次按键时 will_show=False
        # 不显示。改为 False，等 on_focus_out 真正失焦时才隐藏。
        self.focus_hide = False
        # max_chars 缓存：queue_draw 在每次滚动/光标移动时调用 update_size →
        # get_max_chars，后者遍历全部 items（可能上百项）。但 max_chars 仅在
        # items 内容变化时改变，用 items 对象身份做缓存键避免重复遍历。
        self._max_chars_items = None
        self._max_chars_cache = 0

        self.focus_controller = Gtk.EventControllerFocus()
        self.focus_controller.connect('enter', self.on_focus_in)
        self.focus_controller.connect('leave', self.on_focus_out)
        self.model.document.source_view.add_controller(self.focus_controller)

        self.queue_draw()

    def on_focus_out(self, widget):
        self.focus_hide = True
        self.queue_draw()

    def on_focus_in(self, widget):
        self.focus_hide = False
        self.queue_draw()

    def queue_draw(self):
        self.update_size()
        self.update_position()
        self.update_margins()

        pos_visible = self.position_is_visible()
        will_show = self.model.is_active and pos_visible and not self.focus_hide
        self.view.set_visible(will_show)
        self.view.populate()

    def update_size(self):
        font_string = FontManager.font_string
        if font_string != self._last_font_string:
            self._last_font_string = font_string
            self.line_height = FontManager.get_line_height(self.source_view)
            self.char_width = FontManager.get_char_width(self.source_view)
        self.shortcutsbar_height = self.main_window.shortcutsbar.get_allocated_height()

        if self.model.items != None:
            # db_error 时列表底部追加 1 行不可选中的"标签数据库不可用"提示行，
            # 匹配项 > 5 时追加 1 行"选中/总数"计数行（两者均在卡片内、非可选）。
            # 这些额外行高度须计入，否则 widget 高度偏矮导致行被裁切或
            # update_margins 翻转定位（self.height）计算错误。
            item_count = min(len(self.model.items), 5)
            row_count = item_count + (1 if self.model.db_error else 0)
            if len(self.model.items) > 5:
                row_count += 1
            self.height = row_count * self.line_height
            # 宽度 = 命令渲染宽度（含详情列参数名） + 图标列字符等价物 + 边距。
            # get_max_chars 已把最长行的总字符数算出；clamp 上限放宽到 60 以容纳
            # \begin{alignat}[•]{•} 这类长命令 + 详情列。
            self.width = (5 + min(max(self.get_max_chars(), 25), 60)) * self.char_width
            self.view.set_size_request(self.width, self.height)

    def get_max_chars(self):
        items = self.model.items
        if items is None or len(items) == 0:
            return 0
        # items 在 update_suggestions 中被整体替换（self.items = ...），不就地修改。
        # 用 is（身份比较）做缓存键：同一对象直接返回缓存，新对象才重新遍历。
        # queue_draw 每次滚动都跑，但 items 仅在补全词变化时才换。
        if items is self._max_chars_items:
            return self._max_chars_cache
        self._max_chars_items = items
        # 每行字符数 = 命令渲染宽度（•→dotlabel 占位替换）+ 详情列参数名
        # （caption 小字号，近似按一半宽计）+ 图标列字符等价物（16px 图标
        # + 间距 ≈ 4 个等宽字符）。
        max_chars = 0
        for item in items:
            cmd_chars = len(item['command']) + len(item['dotlabels']) - 4 * item['dotlabels'].count('###')
            detail_chars = len(autocomplete_view._get_detail_text(item)) // 2
            max_chars = max(max_chars, cmd_chars + detail_chars)
        self._max_chars_cache = max_chars + 4
        return self._max_chars_cache

    def update_position(self):
        start_iter = self.source_buffer.get_iter_at_mark(self.source_buffer.get_insert())
        if self.model.current_word != None:
            start_iter.backward_chars(len(self.model.current_word))

        iter_location = self.source_view.get_iter_location(start_iter)
        x_offset = - self.document.view.scrolled_window.get_hadjustment().get_value()
        x_offset += self.document.view.margin.get_allocated_width()
        y_offset = - self.document.view.scrolled_window.get_vadjustment().get_value()
        self.x_position = x_offset + iter_location.x
        # 用 iter_location.y + iter_location.height（当前行实际底部）替代
        # + self.line_height。iter_location.y 可能为负（GtkSourceView 文本区域
        # 偏移），且 self.line_height 与实际行高有 2px 偏差，导致 y_position < 行底部，
        # popover 顶部跑到当前行中间遮挡文字。用 rect 的 y+height 是精确行底部。
        self.y_position = y_offset + iter_location.y + iter_location.height

    def update_margins(self):
        vertical_cutoff = self.document.view.scrolled_window.get_allocated_height() - self.height - self.line_height
        horizontal_cutoff = self.main_window.preview_split.get_allocated_width() - self.view.get_allocated_width()

        # 用 translate_coordinates 获取 source_view 顶部相对 overlay 的 y 偏移。
        # 这包含 document_stack_wrapper 的 margin_top（headerbar 高度，46px）等
        # 所有嵌套偏移。原先用 shortcutsbar_height（34px）是错的——shortcutsbar
        # 不在 overlay 内，overlay 顶部已在 shortcutsbar 下方，source_view 顶部
        # 还在 overlay 下方 46px（document_stack_wrapper margin_top），导致
        # popover 偏上 12px，跑到当前行中间遮挡文字。
        # GTK4 PyGObject：成功返回 (x, y) 二元组，失败（widget 未 realize、
        # 不在同一 widget 树，如失焦/文档切换过程中）返回 None。
        coords = self.source_view.translate_coordinates(self.main_window.preview_paned_overlay, 0, 0)
        if coords is not None:
            y_adjust = coords[1]
        else:
            y_adjust = self.shortcutsbar_height or 0

        # 下方空间充足：popover 显示在光标下方。
        if self.y_position <= vertical_cutoff:
            self.view.set_margin_top(self.y_position + y_adjust)
        else:
            # 下方空间不足：翻转上方
            self.view.set_margin_top(self.y_position + y_adjust - self.height - self.line_height)

        if self.x_position >= 0 and self.x_position <= horizontal_cutoff:
            self.view.set_margin_start(self.x_position)
        else:
            self.view.set_margin_start(self.main_window.preview_split.get_allocated_width() - self.view.get_allocated_width())

    def position_is_visible(self):
        # 放宽 y 下限：不再要求 y_position >= line_height（第一行 y_position 略小
        # 会导致 False，popover 不显示）。只要 y_position >= 0（在编辑器内）即可。
        return ((self.y_position >= 0) and
            (self.y_position <= self.document.view.scrolled_window.get_allocated_height()) and
            (self.x_position >= 0) and
            (self.x_position < self.main_window.preview_split.get_allocated_width()))

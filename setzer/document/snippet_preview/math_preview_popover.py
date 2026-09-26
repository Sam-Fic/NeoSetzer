#!/usr/bin/env python3
# coding: utf-8

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

'''公式预览弹窗视图：懒创建、非 autohide 的 Popover（仿
symbols_page/symbol_preview.py 的 hover 气泡模式）。

关键点（与 symbol_preview.py 相同的理由）：不开启 autohide——autohide
Popover 会抢占 seat grab，与编辑器上其它抓取型浮层（补全 widget、右键
上下文菜单）同时出现时互相打架；非 autohide 由控制器在 leave/点击/编辑
时显式 popdown。

内容两态：加载中（Spinner + 文案，编译 1–3 秒期间）与就绪（Gtk.Picture
显示渲染贴图）。贴图按 2x 密度渲染，以 50% 尺寸显示最清晰；超出上限
再按比例缩小（can_shrink 保持纵横比），防止大 align 块占满半屏。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk

# 预览贴图的最大显示尺寸（逻辑像素）。
_MAX_DISPLAY_WIDTH = 460
_MAX_DISPLAY_HEIGHT = 240


class MathPreviewPopover(object):

    def __init__(self, source_view):
        self.source_view = source_view
        self.popover = None       # 懒创建
        self._loading_box = None
        self._spinner = None
        self._picture = None
        self._pointing_rect = None

    # ---------- 构造 ----------

    def _ensure(self):
        if self.popover is not None:
            return
        popover = Gtk.Popover()
        popover.set_autohide(False)
        popover.set_has_arrow(True)

        self._loading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._loading_box.set_margin_top(12)
        self._loading_box.set_margin_bottom(12)
        self._loading_box.set_margin_start(14)
        self._loading_box.set_margin_end(14)
        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._loading_box.append(self._spinner)
        loading_label = Gtk.Label(label=_('Rendering formula…'))
        loading_label.add_css_class('dim-label')
        self._loading_box.append(loading_label)

        # Gtk.Frame 给贴图一圈内边距与描边，白底公式贴图在深色主题下
        # 不至于糊进编辑器背景。
        self._picture = Gtk.Picture()
        self._picture.set_can_shrink(True)
        picture_frame = Gtk.Frame()
        picture_frame.set_margin_top(4)
        picture_frame.set_margin_bottom(4)
        picture_frame.set_margin_start(4)
        picture_frame.set_margin_end(4)
        picture_frame.set_child(self._picture)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._loading_box)
        box.append(picture_frame)
        popover.set_child(box)

        self.popover = popover
        self._picture_frame = picture_frame

    # ---------- 显示 ----------

    def show_loading_at(self, x, y, line_height):
        '''在（source_view 部件坐标的）区域起点行弹出加载态。'''
        self._ensure()
        self._pointing_rect = Gdk.Rectangle()
        self._pointing_rect.x = int(x)
        self._pointing_rect.y = int(y)
        self._pointing_rect.width = 1
        self._pointing_rect.height = max(1, int(line_height))
        self._set_state_loading(True)
        if self.popover.get_parent() is None:
            self.popover.set_parent(self.source_view)
        self.popover.set_pointing_to(self._pointing_rect)
        if not self.popover.get_visible():
            self.popover.popup()

    def show_texture(self, texture):
        '''编译/渲染完成：显示贴图。位置沿用 show_loading_at 的指向。'''
        if self.popover is None:
            return
        texture_width = texture.get_width()
        texture_height = texture.get_height()
        if texture_width <= 0 or texture_height <= 0:
            self.popdown()
            return
        # 2x 渲染按 50% 显示最清晰；仍超上限再缩。
        display_width = texture_width / 2.0
        display_height = texture_height / 2.0
        scale = min(
            1.0,
            _MAX_DISPLAY_WIDTH / display_width,
            _MAX_DISPLAY_HEIGHT / display_height,
        )
        self._picture.set_paintable(texture)
        self._picture.set_size_request(
            int(display_width * scale), int(display_height * scale))
        self._set_state_loading(False)
        # 高度从加载态（~40px）变为贴图高度后，让 GTK 以原指向重新定位。
        if self._pointing_rect is not None:
            self.popover.set_pointing_to(self._pointing_rect)

    def popdown(self):
        if self.popover is not None and self.popover.get_visible():
            self.popover.popdown()
        self._pointing_rect = None
        if self._picture is not None:
            self._picture.set_paintable(None)

    def is_visible(self):
        return self.popover is not None and self.popover.get_visible()

    def _set_state_loading(self, loading):
        self._loading_box.set_visible(loading)
        self._picture_frame.set_visible(not loading)
        if loading:
            self._spinner.start()
        else:
            self._spinner.stop()

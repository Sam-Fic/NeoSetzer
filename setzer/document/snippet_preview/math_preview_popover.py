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

同心圆角设计（nested corners，外内圆角半径之差 = 组件间距）：
- libadwaita 的 popover > contents 为 border-radius 15px / padding 8px /
  border 1px。本文件的 CSS 把半径与内边距显式钉住作为计算基准——即使
  未来主题变更，链条仍然自洽。
- 贴图是 contents 的唯一直接可见子件：与 popover 可见边缘的间距 =
  padding 8 + contents 边框 1 = 9px，按同心规则贴图 border-radius =
  15 − 9 = 6px（_PICTURE_RADIUS 由常量推导而非手写）。
- 贴图内容（白色公式位图）的圆角裁剪用 Gtk.Overflow.HIDDEN：GTK 会把
  内容裁进 border-radius 圆角矩形，四角透出 popover 底色。
- 不套 Gtk.Frame：少一层嵌套少一层半径换算，链路只有 popover → 贴图
  两级；贴图的 1px 描边走 CSS outline（负偏移内缩，outline 不参与盒
  模型，不会让测量尺寸偏离显示尺寸）。

防闪烁设计（与控制器 document_controller 的 leave 处理配合）：
- 弹窗锚定**整个公式区域**的矩形（set_pointing_to）——GTK 保证弹窗只
  会摆在矩形上方或下方、绝不覆盖它，因此既不会盖住公式代码，也不会盖住
  指针（指针必然在公式区域内）。此前锚定起始行、GTK 默认向下摆放，弹窗
  恰好压在多行公式本体和指针上：指针焦点进入弹窗表面 → 编辑器收到合成
  leave → 收起 → 指针仍在公式上 → 重新驻留 → 再弹出 → 再盖住……
  450ms 一次死循环，表现为持续闪烁。
- 编辑器侧 motion 控制器的 leave 在弹窗展开期间被忽略（指针移入紧邻的
  弹窗是合成 crossing，不是真正离开公式）；真正的收起由两条路径接管：
  指针移出弹窗表面（本类在内容盒上挂的 motion leave），或指针移到公式
  区域外的文本（编辑器 motion 判定区域为 None）。

内容两态：加载中（Spinner + 文案，编译 1–3 秒期间）与就绪（Gtk.Picture
显示渲染贴图）。贴图以 4x 密度渲染（本前端传给引擎的 HOVER_RENDER_
DENSITY），按 50% 显示 = 公式以文档字号的 2 倍大小弹出，hidpi 下依旧
清晰；超出上限再按比例缩小（can_shrink 保持纵横比），防止大 align 块
占满半屏。
'''

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk

# 预览贴图的最大显示尺寸（逻辑像素）。贴图以 4x 密度渲染、按 50% 显示
# （= 文档字号 2 倍），此处上限即「文档尺寸 2 倍」下的弹窗上限。
_MAX_DISPLAY_WIDTH = 640
_MAX_DISPLAY_HEIGHT = 400

# ---- 同心圆角基准（取自 libadwaita popover > contents，见模块 docstring）----
_POPOVER_RADIUS = 15
_POPOVER_CONTENTS_PADDING = 8
_POPOVER_CONTENTS_BORDER = 1
# 同心规则：外内圆角半径之差 = 组件间距。
_PICTURE_RADIUS = _POPOVER_RADIUS - _POPOVER_CONTENTS_PADDING - _POPOVER_CONTENTS_BORDER

_CSS = (
    'popover.math-preview-popover > contents {{'
    ' border-radius: {radius}px; padding: {padding}px; }}'
    '.math-preview-picture {{'
    ' border-radius: {picture_radius}px;'
    ' outline: 1px solid var(--border-color); outline-offset: -1px; }}'
).format(radius=_POPOVER_RADIUS, padding=_POPOVER_CONTENTS_PADDING,
         picture_radius=_PICTURE_RADIUS)

_css_provider = None


def _ensure_css():
    '''样式只注册一次（display 级），模式与 autocomplete_widget 一致。'''
    global _css_provider
    if _css_provider is not None:
        return
    provider = Gtk.CssProvider()
    if hasattr(provider, 'load_from_string'):  # GTK 4.12+，无弃用警告
        provider.load_from_string(_CSS)
    else:
        provider.load_from_data(_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    _css_provider = provider


class _FormulaPicture(Gtk.Picture):
    '''固定逻辑尺寸的公式贴图控件。

    Gtk.Picture 的自然尺寸 = 贴图像素尺寸，而 popover 链路按子件自然
    尺寸分配——2x 密度渲染的贴图会以 2 倍逻辑尺寸显示（size_request 只
    是最小值，压不住 natural）。这里把 measure 钉在 set_display_size
    指定的逻辑尺寸上：高分辨率数据仅在 hidpi 下换取 1:1 物理像素采样
    （设备缩放 2x 时 300 物理像素恰好铺满 150 逻辑像素）。
    '''

    def __init__(self):
        super().__init__()
        self._display_size = (0, 0)
        self.set_can_shrink(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        # FILL 把贴图拉伸铺满分配区。分配尺寸由贴图尺寸整型推导（截断 +
        # 上限缩放各有 <1px 的长宽比偏差），默认 CONTAIN 按长宽比留白，
        # 会让内容比分配区窄 1–2px：左右各露出一条底色细缝（深色主题下
        # 白底公式尤其明显）。拉伸比例 ≤1%，肉眼不可见，缝隙归零。
        self.set_content_fit(Gtk.ContentFit.FILL)

    def set_display_size(self, width, height):
        self._display_size = (int(width), int(height))
        self.set_size_request(int(width), int(height))
        self.queue_resize()

    def do_measure(self, orientation, for_size):
        width, height = self._display_size
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (width, width, -1, -1)
        return (height, height, -1, -1)


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
        _ensure_css()
        popover = Gtk.Popover()
        popover.set_autohide(False)
        popover.set_has_arrow(True)
        popover.add_css_class('math-preview-popover')

        self._loading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._loading_box.set_margin_top(4)
        self._loading_box.set_margin_bottom(4)
        self._loading_box.set_margin_start(6)
        self._loading_box.set_margin_end(6)
        self._spinner = Gtk.Spinner()
        self._spinner.start()
        self._loading_box.append(self._spinner)
        loading_label = Gtk.Label(label=_('Rendering formula…'))
        loading_label.add_css_class('dim-label')
        self._loading_box.append(loading_label)

        self._picture = _FormulaPicture()
        self._picture.add_css_class('math-preview-picture')

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._loading_box)
        box.append(self._picture)
        popover.set_child(box)

        # 指针移出弹窗表面 → 收起。这是弹窗展开期间真正的「离开」判定：
        # 此时编辑器侧 source_view 的 leave 已被控制器忽略（见模块 docstring
        # 的防闪烁设计）。popdown 触发的 unmap 也会合成 leave，用 visible
        # 守卫避免在已收起状态下重复动作。
        motion = Gtk.EventControllerMotion()
        motion.connect('leave', self._on_popover_leave)
        box.add_controller(motion)

        self.popover = popover

    def _on_popover_leave(self, controller):
        if self.popover is not None and self.popover.get_visible():
            self.popdown()

    # ---------- 显示 ----------

    def show_loading_at(self, x, y, width, height):
        '''弹出加载态。锚定矩形为公式区域在 source_view 部件坐标中的
        可见包围盒——GTK 只会把它摆在矩形上/下方，不覆盖公式与指针。'''
        self._ensure()
        self._pointing_rect = Gdk.Rectangle()
        self._pointing_rect.x = int(x)
        self._pointing_rect.y = int(y)
        self._pointing_rect.width = max(1, int(width))
        self._pointing_rect.height = max(1, int(height))
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
        # 4x 渲染按 50% 显示（= 文档字号 2 倍），hidpi 下仍有余量；仍超
        # 上限再缩。
        display_width = texture_width / 2.0
        display_height = texture_height / 2.0
        scale = min(
            1.0,
            _MAX_DISPLAY_WIDTH / display_width,
            _MAX_DISPLAY_HEIGHT / display_height,
        )
        self._picture.set_paintable(texture)
        self._picture.set_display_size(
            round(display_width * scale), round(display_height * scale))
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
        self._picture.set_visible(not loading)
        if loading:
            self._spinner.start()
        else:
            self._spinner.stop()

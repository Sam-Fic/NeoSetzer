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
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib
from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Adw

from setzer.widgets.scrolling_widget.scrolling_widget import ScrollingWidget


class PageIndicatorButton(Gtk.Button):
    '''页码徽章按钮：内嵌数字的小型按钮，整体作为一个 Gtk widget，
    数字自动居中（不再像之前那样分别用 cairo 画圆 + Pango 算位置放数字，
    数字容易偏离圆心）。

    主题集成：背景 / 前景色由 CSS .page-indicator-button 按主题给固定灰底
    （浅色淡灰、深色深灰，无阴影），hover / active 等交互态由 GTK 处理，
    Python 不需要查 ColorManager。

    位置与显隐：实际放上画布 overlay 的是外层 Gtk.Revealer（fade in/out
    容器），按钮是 revealer 的子元素。view 在滚动时实时更新 revealer 的
    margin_top / margin_end + set_reveal_child()，按钮本身不参与定位/
    显隐。

    点击：view 把 'clicked' 信号统一接走，从 button.get_page_number()
    读出 1-based 页码，回调给 PreviewPresenter 滚动到该页顶部。'''

    def __init__(self):
        super().__init__()
        self.add_css_class('page-indicator-button')
        # 16 个按钮不参与 Tab 焦点循环（用户不需要键盘 focus 它们）。
        self.set_can_focus(False)
        self.set_tooltip_text(_('Scroll this page to the top'))
        self._label = Gtk.Label()
        self._label.set_label('1')
        self.set_child(self._label)
        self._page_number = None

    def set_page_number(self, page_number_1based):
        '''更新页码。同步设置内部缓存和 label 文本。

        注意要按这个顺序：先改 _page_number 再 set_text。万一 set_text
        触发了重新布局导致 draw 在老 _page_number 上画了错误数字，
        设置 _page_number 在前可避免这种情况。'''
        self._page_number = page_number_1based
        self._label.set_text(str(page_number_1based))

    def get_page_number(self):
        return self._page_number


class PreviewView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.add_css_class('preview')

        self.content = ScrollingWidget()
        self.drawing_area = self.content.content

        self.blank_slate = BlankSlateView()

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.add_named(self.blank_slate, 'blank_slate')
        self.stack.add_named(self.content.view, 'pdf')

        self.overlay = Gtk.Overlay()
        self.overlay.set_vexpand(True)
        self.overlay.set_child(self.stack)

        # 预览卡片：圆角矩形包裹 PDF 内容
        self.card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card_box.set_hexpand(True)
        self.card_box.set_vexpand(True)
        self.card_box.add_css_class('preview-card')
        self.card_box.set_overflow(Gtk.Overflow.HIDDEN)
        self.card_box.append(self.overlay)

        # 构建失败回退到旧 PDF 时，预览角落显示错误图标（右上角）。
        self.error_badge = Gtk.Image(icon_name='dialog-warning-symbolic')
        self.error_badge.set_halign(Gtk.Align.END)
        self.error_badge.set_valign(Gtk.Align.START)
        self.error_badge.set_margin_top(8)
        self.error_badge.set_margin_end(8)
        self.error_badge.set_can_target(False)
        self.error_badge.set_tooltip_text(_('PDF build failed, showing previous version'))
        self.error_badge.add_css_class('error-badge')
        self.error_badge.set_visible(False)
        self.overlay.add_overlay(self.error_badge)

        # 滚动时显示的页码徽章：现在是真正的 Gtk.Button（见 PageIndicatorButton），
        # 每个按钮放在画布 overlay 里、跟着对应页的纸张一起随滚动平移。
        # 滚动期间显示，静止 1500ms 后自动隐藏（由 _page_indicator_hide_id 控制）。
        # 旧实现是在 PreviewPresenter.draw() 用 cairo 现画圆 + Pango 摆数字，
        # 数字与圆分开绘制，主题色需手动从 ColorManager 取，现在改成原生按钮后
        # 这些问题都不存在了。
        self._page_indicator_visible = False
        self._page_indicator_hide_id = None
        # 1500ms 是「过几秒」与「不打扰」的折衷:
        # 800ms 偏短(数字未看清就消失),3000ms 偏长(长期挡视觉焦点)。
        self._page_indicator_hide_delay_ms = 1500
        # 预创建按钮池：每个可见页用 1 个。最大并发可见页数一般 3-7,
        # 但用户缩小缩放时可能更多;16 足够覆盖大多数屏幕,内存开销可忽略。
        # 池的好处是滚动时不用反复 add/remove widget（GTK widget 构造有成本）。
        self._max_indicator_buttons = 16
        # 淡入淡出动画：每个按钮外层套一个 Gtk.Revealer，revealer 是画布
        # overlay 的子（负责定位 + 显隐动画），按钮是 revealer 的子（负责
        # 数字 / 主题色 / 点击）。set_reveal_child(True/False) 触发交叉淡
        # 入淡出过渡，无需手动做定时器 / opacity 计算。
        self._page_indicator_fade_ms = 200
        self._page_indicator_buttons = []
        self._page_indicator_revealers = []
        # 注意:循环变量不能用 `_`!Python 编译器只要在函数体内看到
        # `for _ in ...`(或任何给 `_` 赋值的语句)就把整个函数的 `_` 都
        # 标成局部变量,导致本函数前面所有 `_('msgid')` gettext 调用
        # 报 UnboundLocalError。这里用 `_i` 显式避开冲突。
        for _i in range(self._max_indicator_buttons):
            btn = PageIndicatorButton()
            revealer = Gtk.Revealer()
            # CROSSFADE:透明度过渡,比 slide 系列更适合"圆角徽章浮在页面
            # 上"这种小元素(滑动方向感不强,容易让人误以为在重排)。
            revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
            revealer.set_transition_duration(self._page_indicator_fade_ms)
            # 定位：revealer 自身作为画布 overlay 子,halign/valign 决定
            # 在 overlay 内的对齐基准（START=距顶 / 距左；END=距右）,
            # margin_top / margin_end 给具体偏移。
            revealer.set_halign(Gtk.Align.END)
            revealer.set_valign(Gtk.Align.START)
            revealer.set_can_focus(False)
            revealer.set_child(btn)
            # 初始为不可见,等首次 show_page_indicator 时再 reveal。
            revealer.set_reveal_child(False)
            self._page_indicator_buttons.append(btn)
            self._page_indicator_revealers.append(revealer)
            self.content.add_overlay_widget(revealer)
            btn.connect('clicked', self._on_indicator_button_clicked)
        # 页码徽章配色跟随应用实际深色状态（Adw.StyleManager），而非系统
        # prefers-color-scheme。初始化一次并监听切换。
        style_manager = Adw.StyleManager.get_default()
        if style_manager is not None:
            style_manager.connect('notify::dark', self._apply_indicator_theme)
            self._apply_indicator_theme()
        # 外部（presenter）注册的点击回调：收到 (page_number_1based,)
        self._on_page_indicator_clicked = None

        # ToastOverlay 包裹内容区，用于构建失败回退时弹出提示。
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)

        # 内容容器：卡片 + 提示语上下排列。
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.set_vexpand(True)
        self.content_box.append(self.card_box)

        # 链接目标提示：位于卡片下方（与编辑器状态栏同款设计）。
        # 注意：revealer 不加入 self 的 widget tree，而是由 PreviewPanelPresenter
        # 在文档切换时挂到 PreviewPanelView 层级（stack 之外），避免被 stack 的
        # overflow:HIDDEN 裁剪到圆角内。
        self.target_label_revealer = Gtk.Revealer()
        self.target_label_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.target_label_revealer.set_transition_duration(150)
        self.target_label_revealer.add_css_class('preview-target-bar')

        self.target_label = Gtk.Label()
        self.target_label.add_css_class('caption')
        self.target_label.add_css_class('dim-label')
        self.target_label.set_halign(Gtk.Align.START)
        self.target_label.set_can_target(False)
        self.target_label.add_css_class('preview-target-label')
        self.target_label_revealer.set_child(self.target_label)
        self.target_label_revealer.set_reveal_child(False)

        self.toast_overlay.set_child(self.content_box)
        self.append(self.toast_overlay)

        self._current_link_target = None
        self._link_target_at_top = False
        self.set_link_target_string('')

    def show_pdf_load_failed(self):
        self.error_badge.set_visible(True)
        toast = Adw.Toast.new(_('PDF build failed, showing previous version'))
        toast.set_timeout(4)
        self.toast_overlay.add_toast(toast)

    def hide_pdf_load_failed(self):
        self.error_badge.set_visible(False)

    def set_layout_data(self, layout_data):
        self.layout_data = layout_data

    def set_link_target_string(self, target_string):
        if target_string != self._current_link_target:
            self._current_link_target = target_string
            has_target = target_string != ''
            self.target_label.set_text(target_string)
            self.target_label_revealer.set_reveal_child(has_target)
            if has_target:
                self.card_box.add_css_class('target-visible')
            else:
                self.card_box.remove_css_class('target-visible')

    def set_link_target_at_top(self, at_top):
        '''链接目标提示已在卡片下方，不再需要上下翻转。保留接口兼容。'''
        pass

    def set_page_indicator_click_handler(self, callback):
        '''注册点击徽章的回调。callback 签名: callback(page_number_1based)'''
        self._on_page_indicator_clicked = callback

    def show_page_indicator(self, visible_pages=None, layout=None):
        '''请求显示页码徽章,启动(重置)自动隐藏定时器。

        visible_pages: 当前可见页的 1-based 页码列表（如 [3, 4, 5]）。
                       每次滚动都重传，所以按钮位置 / 标签 / 显隐都跟得上。
        layout: PreviewLayout，用于计算按钮在画布坐标系内的位置。
                传 None 时只重启定时器、不重排按钮（适用于「可见状态没变、
                但希望徽章重新显示」的场景，目前没有调用方用到，留作扩展）。

        每次滚动回调都调用:可见标志位无变化时也不早退（因为 visible_pages
        可能变化），定时器始终重置。'''
        if visible_pages is not None and layout is not None:
            self._update_indicator_button_positions(visible_pages, layout)
        self._page_indicator_visible = True
        if self._page_indicator_hide_id is not None:
            GLib.source_remove(self._page_indicator_hide_id)
        self._page_indicator_hide_id = GLib.timeout_add(
            self._page_indicator_hide_delay_ms, self._hide_page_indicator)

    def _update_indicator_button_positions(self, visible_pages, layout):
        '''把按钮池中的前 N 个按 visible_pages 摆到对应页右上角，其余淡出。

        定位（基于画布坐标系，作用在外层 revealer 上）：
        - revealer.halign=END, valign=START（构造时已设）
        - margin_top = page_y_starts[page] + 10
        - margin_end = horizontal_margin + 10
        margin_end 推导：canvas_width = 2*h_margin + page_width（页面居中），
        按钮右边缘要落在 page_right - 10 = (h_margin + page_width) - 10，
        所以 margin_end = canvas_width - (page_right - 10) = h_margin + 10。
        不需要知道按钮宽度。

        显隐用 set_reveal_child：True 触发 crossfade 淡入（200ms）,
        False 触发淡出。对当前不在 visible_pages 范围内的 revealer 也
        调 False（滚动时那些页滑出视口,自动淡出）。'''
        window_width = self.get_allocated_width()
        h_margin = layout.get_horizontal_margin(window_width)
        for i, (btn, revealer) in enumerate(zip(self._page_indicator_buttons, self._page_indicator_revealers)):
            if i < len(visible_pages):
                page_number = visible_pages[i]
                page_top = layout.get_page_top(page_number - 1)
                if page_top is None:
                    revealer.set_reveal_child(False)
                    continue
                btn.set_page_number(page_number)
                revealer.set_margin_top(page_top + 10)
                revealer.set_margin_end(h_margin + 10)
                revealer.set_reveal_child(True)
            else:
                # 池中多余的按钮淡出（被本次可见页列表压缩掉的部分）
                revealer.set_reveal_child(False)

    def _apply_indicator_theme(self, *args):
        '''按应用实际深色状态给所有页码徽章按钮加 theme-dark / theme-light
        类，从而切换到 CSS 里对应的灰底配色。'''
        style_manager = Adw.StyleManager.get_default()
        dark = bool(style_manager is not None and style_manager.get_dark())
        for btn in self._page_indicator_buttons:
            if dark:
                btn.add_css_class('theme-dark')
                btn.remove_css_class('theme-light')
            else:
                btn.add_css_class('theme-light')
                btn.remove_css_class('theme-dark')

    def _on_indicator_button_clicked(self, button):
        '''按钮 clicked 信号统一处理：读出该按钮代表的页码，回调给外部。'''
        page_number = button.get_page_number()
        if page_number is None:
            return
        if self._on_page_indicator_clicked is not None:
            self._on_page_indicator_clicked(page_number)

    def _hide_page_indicator(self):
        '''定时器回调(也供外部强制隐藏):所有 revealer 触发淡出，标志位复位。

        调 set_reveal_child(False) 让 GTK 自己做 200ms 淡出动画;动画结束
        后 revealer 内部 child (button) 自动变 invisible,无需手动 set_visible。
        多次调 _hide 是幂等的：revealer 已经在淡出 / 已隐藏,再调 False
        是 no-op。

        返回 False 让 GLib.timeout_add one-shot 注销本回调。'''
        self._page_indicator_hide_id = None
        if self._page_indicator_visible:
            self._page_indicator_visible = False
            for revealer in self._page_indicator_revealers:
                revealer.set_reveal_child(False)
        return False

    def is_page_indicator_visible(self):
        return self._page_indicator_visible

    def cancel_page_indicator_timer(self):
        '''取消挂起的隐藏定时器(用于文档切换 / widget 销毁)。
        同时把所有 revealer 淡出,避免定时器未到就残留徽章。'''
        if self._page_indicator_hide_id is not None:
            GLib.source_remove(self._page_indicator_hide_id)
            self._page_indicator_hide_id = None
        if self._page_indicator_visible:
            self._page_indicator_visible = False
            for revealer in self._page_indicator_revealers:
                revealer.set_reveal_child(False)


class BlankSlateView(Gtk.Box):

    def __init__(self):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)

        self.building_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.building_box.set_halign(Gtk.Align.CENTER)
        self.building_box.set_valign(Gtk.Align.CENTER)
        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(32, 32)
        self.building_label = Gtk.Label(label=_('Building\u2026'))
        self.building_label.add_css_class('heading')
        self.building_box.append(self.spinner)
        self.building_box.append(self.building_label)

        self.status_page = Adw.StatusPage()
        self.status_page.add_css_class('compact')
        self.status_page.set_icon_name('document-properties-symbolic')

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(150)
        self.stack.add_named(self.building_box, 'building')
        self.stack.add_named(self.status_page, 'status')
        self.append(self.stack)

        # 初始化为 None 而非 'never_built'：Gtk.Stack 默认显示第一个添加的
        # 子项（'building'），但初始状态应为 'never_built'。若 _current_state
        # 初始化为 'never_built'，则 show_blank_slate → set_state('never_built')
        # 因状态"未变"而提前 return，stack 永远停在 'building' 页面，导致
        # 新建文档（未编译）的预览区显示 "Building…" 而非 "No preview available"。
        # 初始化为 None 保证首次 set_state('never_built') 真正切换到 'status'。
        self._current_state = None

    def set_state(self, state):
        if state == self._current_state:
            return
        self._current_state = state

        if state == 'building':
            self.stack.set_visible_child_name('building')
            # Adw.Spinner 没有 start()/stop()（那是 Gtk.Spinner 的 API）。
            # Adw.Spinner 是常驻动画 widget，可见时自动旋转。stack 切到
            # 'building' 时 building_box（含 spinner）可见，切到 'status'
            # 时自动隐藏，无需显式 start/stop。
        else:
            self.stack.set_visible_child_name('status')
            if state == 'never_built':
                self.status_page.set_title(_('No preview available'))
                self.status_page.set_description(_('To show a .pdf preview of your document, click the build button in the headerbar.'))
                self.status_page.set_icon_name('document-properties-symbolic')
            elif state == 'build_failed':
                self.status_page.set_title(_('Build failed'))
                self.status_page.set_description(_('Check the build log for errors.'))
                self.status_page.set_icon_name('dialog-error-symbolic')



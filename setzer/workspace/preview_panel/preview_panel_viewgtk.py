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
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from setzer.popovers.popover_manager import PopoverManager
import setzer.workspace.preview_panel.snippet_preview_view as snippet_preview_view


class PreviewPanelView(Gtk.Box):
    '''PDF 预览面板的视图层。

    Pass-12 重构：与左侧栏（Symbols / Document Structure）保持一致的
    "内嵌工具栏 + 内容区" 结构：
      - 顶部 .sidebar-toolbar 工具栏：左侧 page spinner + of N 标签，
        右侧 zoom_out / zoom_level / zoom_in / recolor / external 按钮。
      - 下方 .preview-card 内容区：PDF stack，带边距与圆角，呈"卡片"外观。
    工具栏样式与左侧栏统一（.sidebar-toolbar CSS class），不再使用 Gtk.ActionBar
    （原 ActionBar 在面板顶部绘制了一条 inset 分隔线，与左侧栏样式不一致）。
    标题栏不再覆盖预览面板，由 workspace_viewgtk 将 headerbar overlay 移到
    document_stack_wrapper 上，预览侧栏整体（含工具栏）与左侧栏行为一致。
    '''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_size_request(300, -1)
        self.add_css_class('preview')

        # ---- 顶部内嵌工具栏（与左侧栏 .sidebar-toolbar 统一外观）----
        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.toolbar.add_css_class('sidebar-toolbar')
        self.toolbar.set_valign(Gtk.Align.START)
        self.toolbar.set_halign(Gtk.Align.FILL)

        # 模式/面板切换（整篇 PDF / 片段图 / 帮助面板）：Adw.ToggleGroup 分段
        # 控件（libadwaita 的 toggle group，图标档）。三档纯图标、无文字，
        # tooltip 兼作无障碍名。presenter 按**档位名**裁决（不按索引）：
        # help 档切到帮助面板，其余是预览内容模式。恒有一档亮着。
        #
        # 用 name 而非索引是硬要求：「AI diff 排版」档是按需增删的（没有 diff
        # 结果时不该出现一个点开无内容的死档），索引会随它漂移。Adw.Toggle 也
        # 不是 Widget、没有 set_visible，隐藏只能 remove（同侧栏 AI 面板档位）。
        self.mode_switch = Adw.ToggleGroup()
        self.mode_switch.set_valign(Gtk.Align.CENTER)
        self.mode_switch.add(Adw.Toggle(
            name='pdf', icon_name='view-paged-symbolic',
            tooltip=_('Full PDF preview')))
        self.mode_switch.add(Adw.Toggle(
            name='snippet', icon_name='image-x-generic-symbolic',
            tooltip=_('TikZ snippet preview')))
        # 第三档：切到帮助面板（合并了原先独立的 switch to help 按钮）。
        # 弹出预览独立窗口时此档被禁用（见 presenter 的
        # on_preview_pop_state_changed 与 workspace.pop_out / pop_in）。
        self.help_toggle = Adw.Toggle(
            name='help', icon_name='help-browser-symbolic',
            tooltip=_('Switch to Help'))
        self.mode_switch.add(self.help_toggle)
        self.mode_switch.set_active(0)

        # 页码指示器（可输入跳转）— SpinButton + "of N" 标签
        self.page_spin = Gtk.SpinButton()
        self.page_spin.set_range(1, 1)
        self.page_spin.set_increments(1, 1)
        self.page_spin.set_digits(0)
        self.page_spin.set_halign(Gtk.Align.START)
        self.page_spin.set_hexpand(False)
        self.page_spin.set_editable(True)
        self.page_spin.set_can_focus(True)
        # 数字居中（GTK CSS 不支持 text-align，需用 Editable 接口设置）。
        self.page_spin.set_alignment(0.5)
        for child in self.page_spin:
            if isinstance(child, Gtk.Button):
                child.set_visible(False)
        self.page_spin.add_css_class('preview-page-entry')
        self.toolbar.append(self.page_spin)

        self.paging_of_label = Gtk.Label()
        self.paging_of_label.set_xalign(0)
        self.paging_of_label.set_hexpand(False)
        self.paging_of_label.add_css_class('dim-label')
        self.toolbar.append(self.paging_of_label)

        # 构建失败提示：居中显示在页码区与右侧按钮之间的空白区，红色突出严重性。
        # 该标签始终占位（hexpand）以把右侧按钮推到最右；无提示时文本为空。
        self.stale_label = Gtk.Label()
        self.stale_label.set_xalign(0.5)
        self.stale_label.set_halign(Gtk.Align.CENTER)
        self.stale_label.set_hexpand(True)
        self.stale_label.add_css_class('preview-stale-label')
        self.toolbar.append(self.stale_label)

        # 右侧：zoom_out / fit_width / zoom_level / zoom_in / recolor / external
        self.zoom_out_button = Gtk.Button(icon_name='zoom-out-symbolic')
        self.zoom_out_button.set_tooltip_text(_('Zoom out'))
        self.zoom_out_button.add_css_class('flat')
        self.toolbar.append(self.zoom_out_button)

        # ToggleButton：处于 fit_to_width 模式时呈按下（checked）态，状态由
        # presenter 依 zoom_manager.zoom_mode 同步（见 _sync_fit_width_button）。
        self.fit_width_button = Gtk.ToggleButton()
        self.fit_width_button.set_icon_name('double-ended-arrows-horizontal-symbolic')
        self.fit_width_button.set_tooltip_text(_('Fit to Width'))
        self.fit_width_button.add_css_class('flat')
        self.toolbar.append(self.fit_width_button)

        self.zoom_level_label = Gtk.Label()
        self.zoom_level_label.set_xalign(0.5)
        self.zoom_level_label.set_halign(Gtk.Align.CENTER)
        self.zoom_level_button = Gtk.MenuButton()
        self.zoom_level_button.set_popover(PopoverManager.create_popover('preview_zoom_level').view)
        self.zoom_level_button.set_tooltip_text(_('Set zoom level'))
        self.zoom_level_button.add_css_class('flat')
        self.zoom_level_button.set_child(self.zoom_level_label)
        self.toolbar.append(self.zoom_level_button)

        self.zoom_in_button = Gtk.Button(icon_name='zoom-in-symbolic')
        self.zoom_in_button.set_tooltip_text(_('Zoom in'))
        self.zoom_in_button.add_css_class('flat')
        self.toolbar.append(self.zoom_in_button)

        # 放大镜开关：开启时按住左键显示放大镜；关闭则左键不触发放大镜
        # （悬停光标也恢复普通箭头），便于将来按住拖动选择 PDF 文字等交互。
        self.magnifier_toggle = Gtk.ToggleButton()
        self.magnifier_toggle.set_icon_name('edit-find-symbolic')
        self.magnifier_toggle.set_tooltip_text(
            _('Magnifier: hold left mouse button on the PDF to zoom'))
        self.magnifier_toggle.add_css_class('flat')
        self.toolbar.append(self.magnifier_toggle)

        self.recolor_pdf_toggle = Gtk.ToggleButton()
        self.recolor_pdf_toggle.set_icon_name('color-symbolic')
        self.recolor_pdf_toggle.set_tooltip_text(_('Match theme colors'))
        self.recolor_pdf_toggle.add_css_class('flat')
        self.toolbar.append(self.recolor_pdf_toggle)

        self.external_viewer_button = Gtk.Button(icon_name='web-browser-symbolic')
        self.external_viewer_button.set_tooltip_text(_('Open in external PDF viewer'))
        self.external_viewer_button.add_css_class('flat')
        self.toolbar.append(self.external_viewer_button)

        # 弹出为独立窗口：多显示器场景下把预览拖到另一块屏。图标 window-new-symbolic
        # 是 GNOME「新建/弹出窗口」惯用图标。popped_out 状态下此按钮由 presenter 隐藏
        # （独立窗口已 detached，收回走窗口关闭按钮）。
        self.detach_button = Gtk.Button(icon_name='window-new-symbolic')
        self.detach_button.set_tooltip_text(_('Detach preview to separate window'))
        self.detach_button.add_css_class('flat')
        self.toolbar.append(self.detach_button)

        # 结束 AI 审阅：清 diff 结果、档位随之消失。与 diff 档同生命周期
        # （presenter 按 diff_result 是否存在显隐），平时不可见不占位。
        # 语义与"切走档位"区分：切走是暂时离开（结果保留），这是明确说
        # "这轮我看完了"。window-close-symbolic 对应 GNOME 的 dismiss 惯例。
        self.diff_end_button = Gtk.Button(icon_name='window-close-symbolic')
        self.diff_end_button.set_tooltip_text(_('End AI review'))
        self.diff_end_button.add_css_class('flat')
        self.diff_end_button.set_visible(False)
        self._on_diff_end_requested = None
        self.diff_end_button.connect('clicked', self._on_diff_end_clicked)
        self.toolbar.append(self.diff_end_button)

        # diff 改动导航器（diff 档专用）：上一处 / 计数 / 下一处。多处不连续
        # 的改动靠它逐处走，不用在长文档里肉眼扫高亮。与 diff 档同生灭；
        # 显示时 presenter 会收起 magnifier / recolor / external / detach 四个
        # 按钮腾出位置（见 PreviewPanelPresenter.update_buttons）。
        # 计数标签用 "2/9" 而非整句（数字自明，免去 i18n 复数）。
        self.diff_nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.diff_nav.add_css_class('linked')
        self.diff_nav_prev_button = Gtk.Button(icon_name='go-previous-symbolic')
        self.diff_nav_prev_button.set_tooltip_text(_('Previous change'))
        self.diff_nav.append(self.diff_nav_prev_button)
        self.diff_nav_label = Gtk.Label()
        self.diff_nav_label.add_css_class('dim-label')
        self.diff_nav_label.set_width_chars(6)
        self.diff_nav.append(self.diff_nav_label)
        self.diff_nav_next_button = Gtk.Button(icon_name='go-next-symbolic')
        self.diff_nav_next_button.set_tooltip_text(_('Next change'))
        self.diff_nav.append(self.diff_nav_next_button)
        self.diff_nav.set_visible(False)
        self._on_diff_nav_previous = None
        self._on_diff_nav_next = None
        self.diff_nav_prev_button.connect('clicked', self._on_diff_nav_prev_clicked)
        self.diff_nav_next_button.connect('clicked', self._on_diff_nav_next_clicked)
        self.toolbar.append(self.diff_nav)

        # 分段控件最后 append：工具栏最右端（detach 之后）。
        self.toolbar.append(self.mode_switch)

        self.append(self.toolbar)

        # ---- PDF 内容区：带边距 + 圆角的 card ----
        # set_overflow(HIDDEN) 让 PDF drawingarea 被裁剪到 border-radius 圆角内。
        # GTK4 CSS 不支持 overflow 属性，必须用 widget API。
        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.empty_placeholder = Gtk.Box()
        self.stack.add_named(self.empty_placeholder, 'empty')

        # 片段图视图：全应用共享一个（与每文档一份的 PDF 视图不同），
        # 只有当前可见文档驱动它——后台标签页不编译。
        self.snippet_view = snippet_preview_view.SnippetPreviewView()
        self.stack.add_named(self.snippet_view, 'snippet')

        self.append(self.stack)

        # 链接目标提示栏：由 PreviewPanelPresenter 在文档切换时将当前
        # PreviewView 的 target_label_revealer 挂到此处（stack 之外），
        # 避免被 stack 的 overflow:HIDDEN 裁剪到圆角内。
        self.target_bar_placeholder = Gtk.Box()
        self.target_bar_placeholder.set_hexpand(True)
        self.append(self.target_bar_placeholder)


    def set_diff_toggle_visible(self, visible):
        '''显示/隐藏「AI diff 排版」档位。

        只有存在 diff 结果时才有这个内容可切，所以档位跟着结果出现/消失
        ——不留一个点开什么都没有的死档位（同侧栏 AI 面板档位的取舍）。
        Adw.Toggle 是纯 GObject、没有 set_visible，隐藏只能 remove；而
        Adw.ToggleGroup 没有 insert，所以重新出现时只能追加到末尾：顺序
        变成 pdf / snippet / help / diff。位置在「切到帮助」之后纯属外观，
        档位本身靠 name 裁决，不影响行为。
        '''
        toggle = self.mode_switch.get_toggle_by_name('diff')
        if visible and toggle is None:
            # view-dual-symbolic 是 Adwaita symbolic/actions 里确有的一张
            # （双栏对照的语感）；别用 changes-prevent-symbolic——那名字只在
            # 第三方主题（breeze/HighContrast）里，Adwaita 下会退化成破图标。
            self.mode_switch.add(Adw.Toggle(
                name='diff', icon_name='view-dual-symbolic',
                tooltip=_('Typeset diff of AI edits')))
        elif not visible and toggle is not None:
            self.mode_switch.remove(toggle)

    def set_diff_end_visible(self, visible):
        '''结束审阅按钮的显隐：只要 diff 结果存在就给（包括排版失败的
        ——用户可能想手动清掉一个失败状态），没有结果才收。'''
        self.diff_end_button.set_visible(visible)

    def set_diff_end_handler(self, callback):
        self._on_diff_end_requested = callback

    def _on_diff_end_clicked(self, button):
        if self._on_diff_end_requested is not None:
            self._on_diff_end_requested()

    def set_diff_nav_visible(self, visible):
        '''导航器跟着 diff 档走：只有正看着 diff PDF 且确有改动时才给
        （否则一排 0/0 的死按钮只是占位）。'''
        self.diff_nav.set_visible(visible)

    def set_diff_nav_state(self, index, total):
        '''index 0-based、total 为改动处数：更新 "2/9" 与两端禁用。

        两端不循环：审阅是有终点的线性过程，最后一处再点"下一处"跳回
        第一处会让人分不清"看完了"还是"又开始了"。'''
        if total <= 0:
            self.diff_nav_label.set_text('')
            self.diff_nav_prev_button.set_sensitive(False)
            self.diff_nav_next_button.set_sensitive(False)
            return
        self.diff_nav_label.set_text('{}/{}'.format(index + 1, total))
        self.diff_nav_prev_button.set_sensitive(index > 0)
        self.diff_nav_next_button.set_sensitive(index < total - 1)

    def set_diff_nav_handlers(self, previous, next_):
        self._on_diff_nav_previous = previous
        self._on_diff_nav_next = next_

    def _on_diff_nav_prev_clicked(self, button):
        if self._on_diff_nav_previous is not None:
            self._on_diff_nav_previous()

    def _on_diff_nav_next_clicked(self, button):
        if self._on_diff_nav_next is not None:
            self._on_diff_nav_next()

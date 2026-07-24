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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
from gi.repository import GLib
from gi.repository import Gio

from setzer.app.service_locator import ServiceLocator
from setzer.popovers.popover_manager import PopoverManager
from setzer.popovers.shortcutsbar.document_menu import DocumentMenu
from setzer.popovers.shortcutsbar.beamer_menu import BeamerMenu
from setzer.popovers.shortcutsbar.bibliography_menu import BibliographyMenu
from setzer.popovers.shortcutsbar.text_menu import TextMenu
from setzer.popovers.shortcutsbar.quotes_menu import QuotesMenu
from setzer.popovers.shortcutsbar.math_menu import MathMenu
from setzer.popovers.shortcutsbar.object_menu import ObjectMenu


class _NoNaturalWidthLayout(Gtk.BoxLayout):
    '''Gtk.BoxLayout subclass that reports natural width 0 horizontally.

    必要原因：内容列（编辑器）宽度由外层 split 容器分配。若 shortcutsbar
    自然宽度 = left_box 按钮数之和，则 reflow 把按钮移到 overflow 时自然宽度变小
    → 内容列可用宽度随之变化 → sb_width 变小 → reflow 算出更大 target → 移走
    更多按钮 → 自然宽度更小 …… 正反馈级联，target 在两个值之间震荡不收敛，
    按钮被裁切或反复跳动。

    返回 0 后：编辑器列宽度由 document_stack（源码编辑器）的自然宽度驱动，
    不随按钮数变化；shortcutsbar 通过 hexpand 的 _spacer 填充实际分配宽度，
    reflow 按该实际宽度收起按钮。feedback loop 断开。

    实现注意：GTK4 中 Gtk.Box 的 measure 由 layout manager 处理，覆写 widget
    的 do_measure 不生效（PyGObject 3.56.2 + GTK 4.22 验证：Gtk.Box 子类的
    do_measure 从未被调用）。必须覆写 Gtk.BoxLayout.do_measure 才能改变
    Gtk.Box 的测量结果。'''

    def do_measure(self, widget, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            # 横向返回 (0, 0)：min=0, nat=0，让 shortcutsbar 不参与父级宽度分配
            return (0, 0, -1, -1)
        else:
            # 纵向用默认测量（按内容决定高度）
            return Gtk.BoxLayout.do_measure(self, widget, orientation, for_size)


class Shortcutsbar(Gtk.Box):
    '''Icon bar above the editor. Uses libadwaita-style overflow menu
    (GNOME Builder/Text Editor pattern) instead of fixed-width wrapping:
    when the window is narrow, the rightmost left-side buttons are hidden
    from the bar and added to a `view-more-symbolic` popover instead.
    Public method `set_overflow_count(n)` moves the rightmost n left
    buttons into the popover; n=0 shows them all inline.

    `do_size_allocate` is overridden to **continuously** reflow based on
    the actual allocated width (instead of discrete Adw.Breakpoints that
    would only flip at fixed thresholds). The number of overflowed buttons
    matches the available space on every layout pass.'''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.set_can_focus(False)
        # 关键：用自定义 layout manager 让横向自然宽度 = 0，断开 reflow 反馈环
        # （详见 _NoNaturalWidthLayout 文档）。必须在添加子部件之前设置。
        self.set_layout_manager(_NoNaturalWidthLayout())
        # 容器留白，强化"悬浮一排按钮"的视觉归属（无底板横条）
        self.set_margin_top(6)
        self.set_margin_bottom(6)
        self._margin_horizontal = 6
        self.set_margin_start(self._margin_horizontal)
        self.set_margin_end(self._margin_horizontal)

        self.current_popover = None # popover being processed
        self.current_page = 'main' # page being processed

        # 可见 left 按钮容器（普通 Gtk.Box，不是 FlowBox——避免 FlowBox
        # 内部布局对 children 拉伸/换行的副作用）。收起时从 left_box
        # remove 并加入 overflow menu model。
        self.left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.left_box.set_spacing(6)
        self.left_box.set_can_focus(False)

        # 右侧 4 个固定按钮容器
        self.right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.right_box.set_spacing(6)
        self.right_box.set_can_focus(False)

        # 中间弹簧：把 right_box 推到最右端（overflow 在 spacer 左侧，紧跟 left_box）
        self._spacer = Gtk.Box()
        self._spacer.set_hexpand(True)

        # Overflow button (三点) + popover (native Gtk.PopoverMenu via Gio.Menu)
        # 位置：紧跟 left_box（左半边按钮组的最右端）——因为收起的是左侧按钮，
        # 三点按钮应出现在左侧按钮群的末尾，而非整条工具栏的最右。
        self.overflow_button = Gtk.MenuButton()
        self.overflow_button.set_icon_name('view-more-symbolic')
        self.overflow_button.set_tooltip_text(_('More'))
        self.overflow_button.set_visible(False)  # 宽时隐藏
        self.overflow_button.set_margin_start(6)

        self._overflow_model = Gio.Menu()
        self.overflow_button.set_menu_model(self._overflow_model)
        overflow_popover = self.overflow_button.get_popover()
        if overflow_popover is not None:
            overflow_popover.add_css_class('menu')

        # 按钮元信息：icon_name, tooltip, menu_model (仅 MenuButton 有)
        self._button_meta = {}

        # 组装：left | overflow | spacer(hexpand) | right
        # overflow 紧跟 left_box；spacer 把 right_box 推到最右端。
        self.append(self.left_box)
        self.append(self.overflow_button)
        self.append(self._spacer)
        self.append(self.right_box)

        # 创建所有 left button（10 个，顺序：先建 = 最左，后建 = 最右）
        # self.left_buttons[0] = 最左, self.left_buttons[-1] = 最右
        self.left_buttons = []
        self.insert_wizard_button()        # 0
        self.insert_document_button()      # 1
        self.insert_beamer_button()        # 2
        self.insert_bibliography_button()  # 3
        self.insert_object_button()        # 4
        self.insert_text_button()          # 5
        self.insert_math_button()          # 6
        self.insert_quotes_button()        # 7
        self.insert_bold_button()          # 8
        self.insert_italic_button()        # 9 (最右)

        # 创建所有 right button（4 个）
        self.insert_search_button()
        self.insert_replace_button()
        self.insert_more_button()  # F12 context menu
        self.insert_build_log_button()

        # Overflow state: 0 = 全部 visible
        self._overflow_count = 0
        self._last_allocated_width = -1
        # 按钮自然宽度缓存。避免在 reflow 热路径中调用 measure()。
        self._button_widths = None
        # Gio.Menu 重建延迟到 idle：overflow 菜单只在用户点击三点按钮时显示，
        # 不需要在 do_size_allocate 中同步重建（会触发 PopoverMenu items-changed
        # 信号处理，造成 threshold 切换时的卡顿）。
        self._menu_refresh_pending = False
        self._pending_overflow_buttons = []
        self.connect('realize', self._on_realize)

    def _on_realize(self, widget):
        # realize 后 CSS 已应用，按钮宽度可能与 realize 前不同。
        # 在 realize 回调中（allocate 之外）重建缓存，measure() 返回准确值。
        self._button_widths = None
        self._ensure_width_cache()

    # ------- continuous reflow: 父级 size 变化时自动计算 overflow -------

    def do_size_allocate(self, width, height, baseline):
        # 同步 reflow：在 Gtk.Box.do_size_allocate 之前用 set_visible() 改按钮可见性。
        # 之前用 idle 异步 reflow 有 ~0.x 秒延迟——因为 idle (PRIORITY_HIGH_IDLE=100)
        # 优先级低于 motion 事件 (PRIORITY_DEFAULT=0)，连续拖拽时 idle 被事件饿死，
        # 直到用户停拖才执行。
        #
        # 之前用 remove/append 同步 reflow 会破坏 GTK4 布局周期（structural change
        # during allocate），导致后续 do_size_allocate 不再触发。
        #
        # 现在：set_visible() 不是 structural change（只设 flag），在 allocate 中安全。
        # 在 Gtk.Box.do_size_allocate 之前调用，layout manager 会按新可见性在同一帧
        # 分配空间——零延迟、零帧丢失。
        target = self._compute_overflow_target(width)
        if target != self._overflow_count:
            self.set_overflow_count(target)
        Gtk.Box.do_size_allocate(self, width, height, baseline)
        self._last_allocated_width = width

    def request_reflow(self):
        '''Re-run reflow with the current allocated width.

        正常 reflow 只在 shortcutsbar 分配宽度变化时触发（do_size_allocate）。
        但按钮宽度可能因内容变化而改变，且不会引起 shortcutsbar 分配宽度变化
        （_NoNaturalWidthLayout 横向返回 0，paned 不会因此重分配）。这类情况包括：
          - update_buttons 切换 latex 专属按钮的可见性
        此时需主动触发一次 reflow，按新的按钮宽度重新计算 overflow 数量。
        失效宽度缓存（按钮可见性变了，缓存的宽度不再有效），用 idle 延迟确保
        GTK 已处理 set_visible 后的新首选宽度。'''
        self._button_widths = None
        width = self.get_allocated_width()
        if width > 1:
            GLib.idle_add(self.reflow_for_width, width)

    def _measure_width(self, widget):
        try:
            _min, nat, _b1, _b2 = widget.measure(Gtk.Orientation.HORIZONTAL, -1)
        except Exception:
            nat = 36
        return max(0, nat)

    def _ensure_width_cache(self):
        '''一次性测量所有按钮自然宽度并缓存。仅在缓存为空时执行，
        后续 reflow 直接读缓存，不再调用 measure()。'''
        if self._button_widths is not None:
            return
        cache = {}
        for btn in self.left_buttons:
            cache[id(btn)] = self._measure_width(btn)
        for btn in [self.button_search, self.button_replace, self.button_more, self.button_build_log]:
            cache[id(btn)] = self._measure_width(btn)
        cache[id(self.overflow_button)] = self._measure_width(self.overflow_button)
        self._button_widths = cache

    def _is_base_visible(self, btn):
        '''检查按钮的"基础可见性"——由 update_buttons 设置（latex 专属按钮在非
        latex 文档时隐藏）。reflow 只在 base-visible 的按钮中决定哪些 overflow。'''
        base = getattr(btn, '_base_visible', None)
        if base is None:
            return True  # 未设置 = 始终可见（如 wizard 按钮）
        return base

    def _compute_overflow_target(self, available_width):
        '''纯计算：返回应该 overflow 的左侧按钮数量。无副作用。'''
        if available_width <= 1:
            return self._overflow_count
        self._ensure_width_cache()
        cache = self._button_widths

        # 只统计 base-visible 的左侧按钮
        left_widths = []
        for btn in self.left_buttons:
            if not self._is_base_visible(btn):
                continue
            left_widths.append(cache.get(id(btn), 36))
        # 右侧按钮用 get_visible（right 按钮不会被 reflow 隐藏）
        right_widths = []
        for btn in [self.button_search, self.button_replace, self.button_more, self.button_build_log]:
            if not btn.get_visible():
                continue
            right_widths.append(cache.get(id(btn), 36))
        overflow_nat = cache.get(id(self.overflow_button), 36)

        spacing = 6
        n_left = len(left_widths)
        n_right = len(right_widths)

        left_total = sum(left_widths) + spacing * max(0, n_left - 1)
        right_total = sum(right_widths) + spacing * max(0, n_right - 1)
        fixed = right_total + 2 * self._margin_horizontal + overflow_nat + spacing

        # 安全余量：让"下一级收起"判定更积极，避免 measure() 未计入的
        # 边框/内边距/子像素取整导致按钮被轻微裁切。8-12px 足够覆盖 GTK4
        # 默认按钮的 box-shadow/border 未完全包含在 natural width 中的情况。
        safety_margin = 30
        avail = available_width - fixed - safety_margin

        if left_total <= avail:
            target = 0
        else:
            excess = left_total - avail
            target = 0
            accumulated = 0
            for w in reversed(left_widths):
                accumulated += w + spacing
                target += 1
                if accumulated >= excess:
                    break
            target = min(target, n_left)

        # target == 1 没意义：隐藏 1 个按钮但三点按钮占同等空间，净省 0。
        # 直接显示全部按钮，跳过这个中间态。
        if target == 1:
            target = 0

        return max(0, target)

    def reflow_for_width(self, available_width):
        '''Compute target and apply. Called by request_reflow (via idle) and
        the safety net poll.'''
        target = self._compute_overflow_target(available_width)
        if target != self._overflow_count:
            self.set_overflow_count(target)

    # ------- overflow API -------

    def set_overflow_count(self, n):
        '''Hide the rightmost n base-visible left buttons via set_visible().
        0 = show all inline. Idempotent.

        用 set_visible() 而非 remove/append：
        - set_visible() 是 flag 设置，在 do_size_allocate 中安全（不改变 widget tree 结构）
        - remove/append 是 structural change，在 allocate 中会破坏 GTK4 布局周期
        - 配合在 Gtk.Box.do_size_allocate 之前调用，layout manager 按新可见性同帧分配

        用 base-visible data 区分两种隐藏来源：
        - update_buttons 设 base-visible=False（非 latex 文档隐藏 latex 专属按钮）
        - reflow 设 reflow_visible=False（宽度不够 overflow 到菜单）
        实际可见 = base_visible and reflow_visible'''
        n = max(0, min(n, len(self.left_buttons)))
        if n == self._overflow_count:
            return
        # 计算前 n 个 base-visible 按钮中应 overflow 的（从右到左）
        base_visible_indices = [i for i, btn in enumerate(self.left_buttons) if self._is_base_visible(btn)]
        overflow_count = min(n, len(base_visible_indices))
        overflow_indices = set(base_visible_indices[-overflow_count:]) if overflow_count > 0 else set()

        for i, btn in enumerate(self.left_buttons):
            base = self._is_base_visible(btn)
            reflow_visible = i not in overflow_indices
            btn.set_visible(base and reflow_visible)

        # Gio.Menu 重建延迟到 idle：overflow 菜单只在用户点击时显示，不需要
        # 在 do_size_allocate 中同步重建。连续拖拽时 target 可能快速变化
        # （0→1→2→3），_menu_refresh_pending 确保只重建一次（用最终状态）。
        overflow_buttons = [self.left_buttons[i] for i in sorted(overflow_indices)]
        self._pending_overflow_buttons = overflow_buttons
        if not self._menu_refresh_pending:
            self._menu_refresh_pending = True
            GLib.idle_add(self._deferred_refresh_overflow_list)

        self._overflow_count = n
        self.overflow_button.set_visible(n > 0)

    def _deferred_refresh_overflow_list(self):
        self._menu_refresh_pending = False
        buttons = self._pending_overflow_buttons
        self._overflow_model.remove_all()
        for btn in buttons:
            meta = self._button_meta.get(id(btn))
            if meta is None:
                continue
            icon_name = meta['icon_name']
            tooltip = meta['tooltip']
            label = tooltip.split(' (', 1)[0]
            menu_model = meta.get('menu_model')
            if menu_model is not None:
                self._overflow_model.append_submenu(label, menu_model)
            else:
                item = Gio.MenuItem.new(label, meta.get('action_name'))
                target = meta.get('action_target')
                if target is not None:
                    item.set_action_and_target_value(meta['action_name'], target)
                if icon_name:
                    item.set_icon(Gio.ThemedIcon.new(icon_name))
                self._overflow_model.append_item(item)
        return False

    # ------- left button construction helpers -------

    def _add_left_button(self, button):
        '''Append a new left button to left_box and record it in left_buttons.'''
        self.left_box.append(button)
        self.left_buttons.append(button)

    def insert_wizard_button(self):
        self.wizard_button = Gtk.Button()
        self.wizard_button.set_icon_name('document-new-symbolic')
        self.wizard_button.set_tooltip_text(_('Create a template document'))
        self.wizard_button.set_can_focus(False)
        self.wizard_button.set_action_name('win.show-document-wizard')
        self._button_meta[id(self.wizard_button)] = {
            'icon_name': 'document-new-symbolic',
            'tooltip': _('Create a template document'),
            'menu_model': None,
            'action_name': 'win.show-document-wizard',
            'action_target': None,
        }

        self._add_left_button(self.wizard_button)

    def insert_document_button(self):
        self.document_button = Gtk.MenuButton()
        self.document_button.set_icon_name('text-x-generic-symbolic')
        self.document_button.set_tooltip_text(_('Document'))
        self._setup_menu_button(self.document_button, DocumentMenu())
        self._add_left_button(self.document_button)

    def insert_beamer_button(self):
        self.beamer_button = Gtk.MenuButton()
        self.beamer_button.set_icon_name('view-list-bullet-symbolic')
        self.beamer_button.set_tooltip_text(_('Beamer'))
        self._setup_menu_button(self.beamer_button, BeamerMenu())
        self._add_left_button(self.beamer_button)

    def insert_bibliography_button(self):
        self.bibliography_button = Gtk.MenuButton()
        self.bibliography_button.set_icon_name('library-symbolic')
        self.bibliography_button.set_tooltip_text(_('Bibliography'))
        self._setup_menu_button(self.bibliography_button, BibliographyMenu())
        self._add_left_button(self.bibliography_button)

    def insert_text_button(self):
        self.text_button = Gtk.MenuButton()
        self.text_button.set_icon_name('format-text-rich-symbolic')
        self.text_button.set_tooltip_text(_('Text'))
        self._setup_menu_button(self.text_button, TextMenu())
        self._add_left_button(self.text_button)

    def insert_quotes_button(self):
        self.quotes_button = Gtk.MenuButton()
        self.quotes_button.set_icon_name('own-quotes-symbolic')
        self.quotes_button.set_tooltip_text(_('Quotes') + ' (' + _('Ctrl') + '+")')
        self._setup_menu_button(self.quotes_button, QuotesMenu())
        self._add_left_button(self.quotes_button)

    def insert_math_button(self):
        self.math_button = Gtk.MenuButton()
        self.math_button.set_icon_name('own-math-menu-symbolic')
        self.math_button.set_tooltip_text(_('Math'))
        self._setup_menu_button(self.math_button, MathMenu())
        self._add_left_button(self.math_button)

    def insert_object_button(self):
        self.object_button = Gtk.MenuButton()
        self.object_button.set_icon_name('insert-object-symbolic')
        self.object_button.set_tooltip_text(_('Objects'))
        self._setup_menu_button(self.object_button, ObjectMenu())
        self._add_left_button(self.object_button)

    def insert_bold_button(self):
        self.bold_button = Gtk.Button()
        self.bold_button.set_icon_name('format-text-bold-symbolic')
        self.bold_button.set_action_name('win.insert-before-after')
        self.bold_button.set_action_target_value(GLib.Variant('as', ['\\textbf{', '}']))
        self.bold_button.set_tooltip_text(_('Bold') + ' (' + _('Ctrl') + '+B)')
        self._button_meta[id(self.bold_button)] = {
            'icon_name': 'format-text-bold-symbolic',
            'tooltip': _('Bold') + ' (' + _('Ctrl') + '+B)',
            'menu_model': None,
            'action_name': 'win.insert-before-after',
            'action_target': GLib.Variant('as', ['\\textbf{', '}']),
        }
        self._add_left_button(self.bold_button)

    def insert_italic_button(self):
        self.italic_button = Gtk.Button()
        self.italic_button.set_icon_name('format-text-italic-symbolic')
        self.italic_button.set_action_name('win.insert-before-after')
        self.italic_button.set_action_target_value(GLib.Variant('as', ['\\textit{', '}']))
        self.italic_button.set_tooltip_text(_('Italic') + ' (' + _('Ctrl') + '+I)')
        self._button_meta[id(self.italic_button)] = {
            'icon_name': 'format-text-italic-symbolic',
            'tooltip': _('Italic') + ' (' + _('Ctrl') + '+I)',
            'menu_model': None,
            'action_name': 'win.insert-before-after',
            'action_target': GLib.Variant('as', ['\\textit{', '}']),
        }
        self._add_left_button(self.italic_button)

    # ------- right button construction helpers -------

    def _add_right_button(self, button):
        self.right_box.append(button)

    def insert_search_button(self):
        self.button_search = Gtk.ToggleButton()
        self.button_search.set_icon_name('edit-find-symbolic')
        self.button_search.set_tooltip_text(_('Find') + ' (' + _('Ctrl') + '+F)')
        self._add_right_button(self.button_search)

    def insert_replace_button(self):
        self.button_replace = Gtk.ToggleButton()
        self.button_replace.set_icon_name('edit-find-replace-symbolic')
        self.button_replace.set_tooltip_text(_('Find and Replace') + ' (' + _('Ctrl') + '+H)')
        self._add_right_button(self.button_replace)

    def insert_more_button(self):
        self.button_more = Gtk.MenuButton()
        self.button_more.set_icon_name('open-menu-symbolic')
        self.button_more.set_popover(PopoverManager.create_popover('context_menu').view)
        self.button_more.set_tooltip_text(_('Context Menu') + ' (F12)')
        self._add_right_button(self.button_more)

    def insert_build_log_button(self):
        self.button_build_log = Gtk.ToggleButton()
        self.button_build_log.set_icon_name('build-log-symbolic')
        self.button_build_log.set_tooltip_text(_('Build log') + ' (F8)')
        self._add_right_button(self.button_build_log)

    def _setup_menu_button(self, button, menu_builder):
        '''Wire a Gio.Menu model to a Gtk.MenuButton and add the standard
        .menu CSS class to the auto-created Gtk.PopoverMenu — identical to
        how the hamburger menu is set up.'''
        menu_builder.finalize()
        button.set_menu_model(menu_builder.model)
        popover = button.get_popover()
        if popover is not None:
            popover.add_css_class('menu')
        self._button_meta[id(button)] = {
            'icon_name': button.get_icon_name(),
            'tooltip': button.get_tooltip_text() or '',
            'menu_model': menu_builder.model,
        }

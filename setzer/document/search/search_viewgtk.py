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
from gi.repository import Gtk, GLib, Gio

from setzer.helpers import animation_prefs
from setzer.keyboard_shortcuts import shortcut_tooltips
from setzer.widgets.search_entry.search_entry import SearchEntry
from setzer.document.search.search_history_popup import SearchHistoryPopup


class SearchBar(Gtk.SearchBar):
    ''' Find / replace bar for the document editor.

    Built on the native ``Gtk.SearchBar``: it provides the standard reveal
    animation and search-bar chrome via ``set_search_mode``. The
    content is a vertical box holding the find row (entry + prev/next/close)
    and an optional replace row. The match counter is overlaid on the right
    side of the entry (a fixed margin, no dynamic width math).
    '''

    def __init__(self):
        Gtk.SearchBar.__init__(self)
        self.add_css_class('document-searchbar')
        self.set_show_close_button(False)
        self.set_search_mode(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # 间距改为 0：find/replace 两行之间的间距由 find_row 动态下边距控制，
        # 否则 replace_revealer 未展开时仍会占据 content 的 6px 间距，导致搜索
        # 栏上下外边距不对称。
        content.set_spacing(0)
        content.set_margin_top(6)
        content.set_margin_bottom(6)
        content.set_margin_start(6)
        content.set_margin_end(6)

        # --- find row ---
        self.find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.find_row.set_spacing(6)
        self.find_row.set_margin_bottom(0)

        # 左侧「搜索/替换」模式切换按钮（替代原来的固定箭头占位）。
        # 点击展开/收起 replace 行；图标与 replace 行左侧箭头一致，按下时表示
        # 当前处于替换模式。
        self.replace_mode_button = Gtk.ToggleButton()
        self.replace_mode_button.set_child(Gtk.Image(icon_name='go-next-symbolic'))
        self.replace_mode_button.set_can_focus(False)
        self.replace_mode_button.add_css_class('flat')
        self.replace_mode_button.set_tooltip_text(_('Search and replace'))
        self.replace_mode_button.connect('notify::active', self._on_replace_mode_active_notify)
        self.find_row.append(self.replace_mode_button)

        self.entry = SearchEntry()
        self.entry.set_hexpand(True)

        # Overlay the match counter at the right inside the entry.
        self.entry_overlay = Gtk.Overlay()
        self.entry_overlay.set_child(self.entry)
        self.match_counter = Gtk.Label()
        self.match_counter.set_halign(Gtk.Align.END)
        self.match_counter.set_valign(Gtk.Align.CENTER)
        # 默认仅靠右不贴边；当 entry 有内容、Gtk.SearchEntry 显示「清空」按钮时，
        # 由 _update_counter_margin 调大右边距避开它（见下方连接）。
        self.match_counter.set_margin_end(12)
        self.match_counter.add_css_class('dim-label')
        self.entry_overlay.add_overlay(self.match_counter)
        self.entry.connect('notify::text', self._update_counter_margin)
        self._update_counter_margin()
        self.find_row.append(self.entry_overlay)

        # 查找历史弹窗：锚定到搜索输入框（原来挂在 find_history_button 上，
        # 该按钮已收进 ⋮ 菜单；MenuButton 的 menu_model 与自定义 popover 互斥，
        # 故 popup 改挂 entry_overlay，由菜单里的「Search history」项触发）。
        self.find_history_popup = SearchHistoryPopup()
        self.find_history_popup.set_parent(self.entry_overlay)

        # --- match-option toggles (case / regex / whole-word) ---
        # 与 gedit / GNOME Text Editor 一致的右侧一排开关。
        # 「保留大小写 / 在选区中搜索 / 搜索历史」三个低频控件收进 ⋮ 菜单
        # （Gio.Menu 勾选项，见下方 _init_more_menu）。
        options_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        options_box.set_spacing(6)
        options_box.set_valign(Gtk.Align.CENTER)

        self.case_toggle = Gtk.ToggleButton()
        self.case_toggle.set_child(Gtk.Image(icon_name='uppercase-symbolic'))
        self.case_toggle.set_can_focus(False)
        self.case_toggle.set_hexpand(False)
        self.case_toggle.add_css_class('flat')
        self.case_toggle.add_css_class('search-toggle')
        self.case_toggle.set_tooltip_text(_('Match case'))
        options_box.append(self.case_toggle)

        self.regex_toggle = Gtk.ToggleButton()
        self.regex_toggle.set_child(Gtk.Image(icon_name='regex-symbolic'))
        self.regex_toggle.set_can_focus(False)
        self.regex_toggle.set_hexpand(False)
        self.regex_toggle.add_css_class('flat')
        self.regex_toggle.add_css_class('search-toggle')
        self.regex_toggle.set_tooltip_text(_('Use regular expressions'))
        options_box.append(self.regex_toggle)

        self.word_toggle = Gtk.ToggleButton()
        self.word_toggle.set_child(Gtk.Image(icon_name='completion-word-symbolic'))
        self.word_toggle.set_can_focus(False)
        self.word_toggle.set_hexpand(False)
        self.word_toggle.add_css_class('flat')
        self.word_toggle.add_css_class('search-toggle')
        self.word_toggle.set_tooltip_text(_('Match entire word'))
        options_box.append(self.word_toggle)

        # --- overflow menu (⋮): preserve-case / search-in-selection / history ---
        # 低频选项收进标准 Gio.Menu 勾选菜单（布尔 stateful action 渲染为勾选项），
        # 与 document_wizard / document_structure 的 MenuButton + ActionGroup 同款。
        # 位置紧跟 3 个内联开关（归属「选项组」），在 ↑/↓ 导航之前。
        # 「在选区中搜索」原为常驻占位按钮（显隐会改变查找行 min 宽度、挤压预览
        # 侧栏份额）；改为菜单 action 后完全不参与布局，约束简化为 set_enabled。
        self.more_button = Gtk.MenuButton()
        self.more_button.set_icon_name('view-more-symbolic')
        self.more_button.set_can_focus(False)
        self.more_button.add_css_class('flat')
        self.more_button.set_tooltip_text(_('More options'))
        self._init_more_menu()
        options_box.append(self.more_button)

        self.find_row.append(options_box)

        self.prev_button = Gtk.Button(icon_name='go-up-symbolic')
        self.prev_button.set_can_focus(False)
        shortcut_tooltips.set_tooltip(self.prev_button, _('Previous result'), 'find_previous')
        self.find_row.append(self.prev_button)

        self.next_button = Gtk.Button(icon_name='go-down-symbolic')
        self.next_button.set_can_focus(False)
        shortcut_tooltips.set_tooltip(self.next_button, _('Next result'), 'find_next')
        self.find_row.append(self.next_button)

        self.close_button = Gtk.Button(icon_name='window-close-symbolic')
        self.close_button.add_css_class('flat')
        self.close_button.set_can_focus(False)
        self.close_button.set_tooltip_text(_('Close search') + ' (Esc)')
        self.find_row.append(self.close_button)

        content.append(self.find_row)

        # --- replace row (revealed only in replace mode) ---
        # 用 Revealer 包住替换行，使其在切换 replace 模式时随 SearchBar
        # 一起平滑滑出/收起，而不是生硬地显隐。
        self.replace_revealer = Gtk.Revealer()
        animation_prefs.set_transition(
            self.replace_revealer, Gtk.RevealerTransitionType.SLIDE_DOWN, 200)

        self.replace_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.replace_wrapper.set_spacing(6)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_width_chars(4)
        self.replace_entry.set_hexpand(True)
        self.replace_entry.set_placeholder_text(_('Type replacement text…'))
        self.replace_wrapper.append(self.replace_entry)

        self.replace_history_button = Gtk.Button()
        self.replace_history_button.set_icon_name('document-open-recent-symbolic')
        self.replace_history_button.set_can_focus(False)
        self.replace_history_button.add_css_class('flat')
        self.replace_history_button.set_tooltip_text(_('Replacement history'))
        self.replace_history_popup = SearchHistoryPopup()
        self.replace_history_popup.set_parent(self.replace_history_button)
        self.replace_history_button.connect('clicked', self._on_replace_history_button_clicked)
        self.replace_wrapper.append(self.replace_history_button)

        self.replace_button = Gtk.Button.new_with_label(_('Replace'))
        self.replace_button.set_can_focus(False)
        self.replace_button.set_sensitive(False)
        self.replace_wrapper.append(self.replace_button)

        self.replace_all_button = Gtk.Button.new_with_label(_('Replace All'))
        self.replace_all_button.set_can_focus(False)
        self.replace_all_button.set_sensitive(False)
        self.replace_wrapper.append(self.replace_all_button)

        self.replace_revealer.set_child(self.replace_wrapper)
        content.append(self.replace_revealer)

        self.replace_revealer.connect('notify::reveal-child', self._on_replace_revealed)
        self._on_replace_revealed()

        self.set_child(content)

    def _on_replace_mode_active_notify(self, button, gparam):
        '''切换按钮图标：未展开时显示向右箭头，展开后显示向下箭头。'''
        icon_name = 'go-down-symbolic' if button.get_active() else 'go-next-symbolic'
        button.get_child().set_from_icon_name(icon_name)

    def _update_counter_margin(self, *args):
        '''根据 entry 是否有内容动态调整计数提示的右外边距。

        Gtk.SearchEntry 仅在内容非空时显示右侧「清空」按钮；有按钮时把计数
        提示右移避开重叠，无按钮（空内容）时仅保持 12px 不贴边。'''
        if len(self.entry.get_text()) > 0:
            self.match_counter.set_margin_end(36)
        else:
            self.match_counter.set_margin_end(12)

    def _on_replace_revealed(self, *args):
        '''Replace 行展开/收起时动态控制 find_row 的下边距。

        收起时设为 0，避免 content 内部在 find_row 与隐藏的 replace_revealer 之间
        留下多余间距，保证搜索栏上下绿框区域等高；展开时恢复 6px 间距。'''
        if self.replace_revealer.get_reveal_child():
            self.find_row.set_margin_bottom(6)
        else:
            self.find_row.set_margin_bottom(0)

    def _init_more_menu(self):
        '''构建 ⋮ 按钮的 Gio.Menu 与 action 组。

        actions 暴露给 search.Search：
        - ``preserve-case`` / ``selection``：布尔 stateful，菜单渲染为勾选项；
          search.py 读 get_state()、监听 notify::state。
        - ``selection`` 可 set_enabled（跨行选区判断），菜单项原生置灰。
        - ``history``：激活后在搜索框下方弹出查找历史 popup。
          菜单项激活会先关闭菜单 popover，故用 idle 延迟弹出，避免同周期
          两个 popover 冲突。
        '''
        self.search_actions = Gio.SimpleActionGroup()

        self.preserve_case_action = Gio.SimpleAction.new_stateful(
            'preserve-case', GLib.VariantType.new('b'),
            GLib.Variant.new_boolean(False))
        self.search_actions.add_action(self.preserve_case_action)

        self.selection_action = Gio.SimpleAction.new_stateful(
            'selection', GLib.VariantType.new('b'),
            GLib.Variant.new_boolean(False))
        # 初始置灰：沿用原「选区开关未选中跨行选区时不可用」的语义——只有
        # 跨行选区时 update_selection_toggle_sensitivity 才会启用它。
        self.selection_action.set_enabled(False)
        self.search_actions.add_action(self.selection_action)

        self.history_action = Gio.SimpleAction.new('history', None)
        self.history_action.connect('activate', self._on_history_action_activate)
        self.search_actions.add_action(self.history_action)

        def make_item(title, action_name, icon_name):
            item = Gio.MenuItem.new(title, 'search.' + action_name)
            item.set_icon(Gio.ThemedIcon.new(icon_name))
            return item

        section_options = Gio.Menu()
        section_options.append_item(make_item(
            _('Preserve case'), 'preserve-case', 'keyboard-caps-lock-wide-symbolic'))
        section_options.append_item(make_item(
            _('Search in selection'), 'selection', 'edit-select-all-symbolic'))

        section_history = Gio.Menu()
        section_history.append_item(make_item(
            _('Search history'), 'history', 'document-open-recent-symbolic'))

        menu = Gio.Menu()
        menu.append_section(None, section_options)
        menu.append_section(None, section_history)

        self.more_button.insert_action_group('search', self.search_actions)
        self.more_button.set_menu_model(menu)
        popover = self.more_button.get_popover()
        if popover is not None:
            popover.add_css_class('menu')

    def _on_history_action_activate(self, action, parameter):
        GLib.idle_add(self._popup_find_history)

    def _popup_find_history(self):
        self.find_history_popup.popup()
        return GLib.SOURCE_REMOVE

    def _on_replace_history_button_clicked(self, button):
        self._toggle_popover(self.replace_history_popup)

    def _toggle_popover(self, popover):
        if popover.get_visible():
            popover.popdown()
        else:
            popover.popup()

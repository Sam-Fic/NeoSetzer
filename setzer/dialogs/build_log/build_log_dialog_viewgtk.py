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
from gi.repository import Gtk, Adw, Gdk, Gio, GObject
import os.path

from setzer.dialogs.helpers.dialog_viewgtk import DialogView


# build-log item 类型 → 图标名（与 Pass-7 旧 BuildLogList 保持一致）。
ICON_MAP = {
    'Error': 'dialog-error-symbolic',
    'Warning': 'dialog-warning-symbolic',
    'Badbox': 'own-badbox-symbolic',
}

# 弹窗内 group 的显示顺序：错误置顶，警告居中，badbox 居底（符合用户设计）。
TYPE_ORDER = ['Error', 'Warning', 'Badbox']

# TYPE_LABELS 的 _() 调用延迟到 __init__ 内求值：入口脚本在 activate() 才注入
# builtins._，模块顶层求值会 NameError。其他模块（如 preferences_viewgtk）也
# 遵循此惯例——所有 _() 调用都在运行时（__init__ / 方法内），不在模块顶层。


class BuildLogDialogView(DialogView):
    '''build_log 弹窗视图（Pass-10）。

    形态：`Adw.Dialog`（继承自 `DialogView`）+ content 为 `Adw.PreferencesPage`。
    HeaderBar 右侧放 Copy All 按钮；page 内按 TYPE_ORDER 顺序放 3 个
    `Adw.PreferencesGroup`（Errors / Warnings / Badboxes），每个 group 内是
    一个 `BuildLogList`（`Gtk.ListBox` + `Adw.ActionRow`，复用 Pass-7/Pass-9 的
    boxed-list + compact-rows 范式）。
    '''

    def __init__(self, main_window):
        DialogView.__init__(self, main_window)
        self.set_title(_('Build Log'))
        self.set_content_width(640)
        self.set_content_height(480)

        # HeaderBar 标题
        self.title_widget = Adw.WindowTitle()
        self.title_widget.set_title(_('Build Log'))
        self.title_widget.set_subtitle('')
        self.headerbar.set_title_widget(self.title_widget)

        # Copy All 按钮
        self.copy_all_button = Gtk.Button(icon_name='edit-copy-symbolic')
        self.copy_all_button.set_tooltip_text(_('Copy all log entries'))
        self.copy_all_button.add_css_class('flat')
        self.copy_all_button.set_can_focus(False)
        self.headerbar.pack_end(self.copy_all_button)

        # Save Log As 按钮
        self.save_log_button = Gtk.Button(icon_name='document-save-symbolic')
        self.save_log_button.set_tooltip_text(_('Save log to file'))
        self.save_log_button.add_css_class('flat')
        self.save_log_button.set_can_focus(False)
        self.headerbar.pack_end(self.save_log_button)

        # Filter 按钮 + 弹出菜单
        # 使用 Gtk.ToggleButton + 手动 Popover 控制，替代 Gtk.MenuButton。
        # GTK4 中 Gtk.MenuButton 与 Adw.Dialog 内的 Popover 偶现无法通过再次
        # 点击按钮或点击空白处关闭的问题（只能 Esc），手动 popup/popdown 可规避。
        self.filter_button = Gtk.ToggleButton()
        self.filter_button.set_child(Gtk.Image(icon_name='edit-select-all-symbolic'))
        self.filter_button.set_tooltip_text(_('Filter log entries'))
        self.filter_button.add_css_class('flat')
        self.filter_button.set_can_focus(False)
        self.headerbar.pack_end(self.filter_button)

        self.filter_popover = Gtk.Popover()
        self.filter_popover.set_autohide(True)
        self.filter_popover.set_parent(self.filter_button)
        self.filter_button.connect('notify::active', self._on_filter_button_active)
        self.filter_popover.connect('closed', self._on_filter_popover_closed)
        filter_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        filter_box.set_margin_top(8)
        filter_box.set_margin_bottom(8)
        filter_box.set_margin_start(8)
        filter_box.set_margin_end(8)

        # 文件过滤（使用 Gtk.ComboBoxText，GTK4 中仍可用且无 DropDown 的测量问题）
        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        file_box.append(Gtk.Label(label=_('File:')))
        self.file_filter_combo = Gtk.ComboBoxText()
        self.file_filter_combo.append_text(_('All'))
        self.file_filter_combo.set_active(0)
        file_box.append(self.file_filter_combo)
        filter_box.append(file_box)

        # 错误类型过滤
        type_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        type_box.append(Gtk.Label(label=_('Type:')))
        self.type_filter_combo = Gtk.ComboBoxText()
        self.type_filter_combo.append_text(_('All'))
        self.type_filter_combo.set_active(0)
        type_box.append(self.type_filter_combo)
        filter_box.append(type_box)

        # 行号范围过滤
        line_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line_box.append(Gtk.Label(label=_('Lines:')))
        self.line_min_spin = Gtk.SpinButton.new_with_range(0, 999999, 1)
        line_box.append(self.line_min_spin)
        line_box.append(Gtk.Label(label=_('–')))
        self.line_max_spin = Gtk.SpinButton.new_with_range(0, 999999, 1)
        self.line_max_spin.set_value(999999)
        line_box.append(self.line_max_spin)
        filter_box.append(line_box)

        self.filter_popover.set_child(filter_box)

        # 搜索按钮 + 搜索栏（点击按钮展开/收起）
        self.search_button = Gtk.ToggleButton(icon_name='edit-find-symbolic')
        self.search_button.set_tooltip_text(_('Search build log'))
        self.search_button.add_css_class('flat')
        self.search_button.set_can_focus(False)
        self.headerbar.pack_start(self.search_button)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_revealer.set_transition_duration(150)

        search_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar_box.set_margin_top(4)
        search_bar_box.set_margin_bottom(4)
        search_bar_box.set_margin_start(6)
        search_bar_box.set_margin_end(6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        search_bar_box.append(self.search_entry)
        self.search_revealer.set_child(search_bar_box)

        self.search_button.bind_property('active', self.search_revealer, 'reveal-child',
                                         GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        # content
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)

        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.toast_overlay.set_child(self.page)
        self.topbox.append(self.search_revealer)
        self.topbox.append(self.toast_overlay)

        # 3 个 group（按 TYPE_ORDER 顺序）。group 内嵌 BuildLogList。
        # 显隐由 presenter.populate 按 settings.autoshow_build_log 控制。
        # TYPE_LABELS 在此运行时构建（_() 需在 gettext.install 后才可用）。
        type_labels = {
            'Error': _('Errors'),
            'Warning': _('Warnings'),
            'Badbox': _('Badboxes'),
        }
        self.groups = {}
        self.lists = {}
        for item_type in TYPE_ORDER:
            group = Adw.PreferencesGroup()
            group.set_title(type_labels[item_type])
            self.page.add(group)
            self.groups[item_type] = group

            lst = BuildLogList()
            group.add(lst)
            self.lists[item_type] = lst

        # 空状态占位：全部 group 都为空时显示。
        self.empty_state = Adw.StatusPage()
        self.empty_state.set_icon_name('object-select-symbolic')
        self.empty_state.set_title(_('Build Log'))
        self.empty_state.set_description(_('No build log items to show.'))
        self.empty_state.set_vexpand(True)
        self.empty_state.set_visible(False)
        self.topbox.append(self.empty_state)

    def _on_filter_button_active(self, button, gparam):
        '''手动控制 filter popover 的显示/隐藏：按钮按下时弹出，抬起时收起。'''
        if button.get_active():
            self.filter_popover.popup()
        else:
            self.filter_popover.popdown()

    def _on_filter_popover_closed(self, popover):
        '''点击空白处或按 Esc 关闭 popover 后，同步取消按钮的按下状态。'''
        self.filter_button.set_active(False)

    def clear_all(self):
        '''清空所有 group 的行（用于 presenter 重建前）。'''
        for lst in self.lists.values():
            lst.clear_rows()

    def add_item(self, item_type, filename, line_number, description):
        '''向对应类型的 group 追加一条 row。未知类型忽略。'''
        lst = self.lists.get(item_type)
        if lst is None:
            return
        lst.append(lst.make_row(item_type, filename, line_number, description))

    def set_header_title(self, title, subtitle=''):
        '''更新 HeaderBar 的标题/副标题（构建状态信息）。'''
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)

    def update_file_filter(self, filenames):
        '''更新文件过滤下拉框的选项列表。'''
        if hasattr(self, '_file_filter_handler_id'):
            self.file_filter_combo.handler_block(self._file_filter_handler_id)
        self.file_filter_combo.remove_all()
        for name in filenames:
            self.file_filter_combo.append_text(name)
        self.file_filter_combo.set_active(0)
        if hasattr(self, '_file_filter_handler_id'):
            self.file_filter_combo.handler_unblock(self._file_filter_handler_id)

    def update_type_filter(self, error_types):
        '''更新错误类型过滤下拉框的选项列表。'''
        if hasattr(self, '_type_filter_handler_id'):
            self.type_filter_combo.handler_block(self._type_filter_handler_id)
        self.type_filter_combo.remove_all()
        for name in error_types:
            self.type_filter_combo.append_text(name)
        self.type_filter_combo.set_active(0)
        if hasattr(self, '_type_filter_handler_id'):
            self.type_filter_combo.handler_unblock(self._type_filter_handler_id)


class BuildLogList(Gtk.ListBox):
    '''原生 Gtk.ListBox + Adw.ActionRow，复用 Pass-7/Pass-9 设计。

    每行：[类型 icon] 标题(描述) / 副标题(文件:行号)。单击激活由 controller
    的 row-activated 处理（跳转报错行）；右键直接 copy 单行（GestureClick
    监听 SECONDARY button）。

    boxed-list CSS class 提供 libadwaita 标准列表外观（圆角 + 行间细线分隔）；
    compact-rows 收紧行距（与 Pass-9 一致）。
    '''

    def __init__(self):
        Gtk.ListBox.__init__(self)
        # SINGLE 选择模式 + 可聚焦：方向键即可在构建日志条目间导航（可访问性）。
        # 单击激活（跳转报错行）仍由 controller 的 row-activated 处理，不受影响。
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_activate_on_single_click(True)
        self.add_css_class('boxed-list')
        self.add_css_class('compact-rows')

    def make_row(self, item_type, filename, line_number, description):
        '''构造一条 Adw.ActionRow。

        filename/line_number/description 作为 Python 动态属性附加在 row 上，
        供 controller 的 on_row_activated 与 on_right_click 直接读取。
        行尾放置复制按钮，点击可复制该单条内容（含行号）。
        '''
        row = Adw.ActionRow()
        # selectable=True 配合列表的 SINGLE 选择模式，使方向键能选中并高亮当前行。
        row.set_selectable(True)
        row.set_activatable(True)
        row.add_prefix(Gtk.Image(icon_name=ICON_MAP.get(item_type, 'dialog-warning-symbolic')))
        row.set_title(description if description else '')
        if filename:
            subtitle = os.path.basename(filename)
            if line_number >= 0:
                subtitle += ':' + str(line_number)
            row.set_subtitle(subtitle)
        row.filename = filename
        row.line_number = line_number
        row.description = description
        row.item_type = item_type

        # 行尾复制按钮：点击复制当前单行。
        copy_button = Gtk.Button(icon_name='edit-copy-symbolic')
        copy_button.set_tooltip_text(_('Copy to clipboard'))
        copy_button.add_css_class('flat')
        copy_button.set_valign(Gtk.Align.CENTER)
        copy_button.set_can_focus(False)
        copy_button.connect('clicked', self.on_copy_row_clicked, row)
        row.add_suffix(copy_button)

        # 右键 Copy 单行：GestureClick 监听 SECONDARY button。
        # pressed 回调直接 copy 单行文本，不弹 popover（少一步点击）。
        gesture = Gtk.GestureClick()
        gesture.set_button(Gdk.BUTTON_SECONDARY)
        gesture.connect('pressed', self.on_right_click, row)
        row.add_controller(gesture)
        return row

    def on_copy_row_clicked(self, button, row):
        '''点击行尾复制按钮：复制该单条文本（含行号）。'''
        text = self._format_row_text(row)
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast_overlay.add_toast(Adw.Toast.new(_('Copied to clipboard')))

    def on_right_click(self, gesture, n_press, x, y, row):
        '''右键直接 copy 单行，格式与 Copy All 一致：file:line: description。'''
        text = self._format_row_text(row)
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast_overlay.add_toast(Adw.Toast.new(_('Copied to clipboard')))

    @staticmethod
    def _format_row_text(row):
        '''单行文本格式：file:line: description（无 file 时退化为 :description / description）。'''
        parts = []
        if row.filename:
            parts.append(row.filename)
            if row.line_number >= 0:
                parts.append(str(row.line_number))
        text = ':'.join(parts)
        if row.description:
            text = (text + ': ' + row.description) if text else row.description
        return text

    def clear_rows(self):
        '''清空所有子行。GTK 4.6+ 的 Gtk.ListBox.remove_all 内部批量释放，
        替代原手动 get_first_child + remove 循环（n 次 remove 各 O(n) → O(n²)）。'''
        self.remove_all()

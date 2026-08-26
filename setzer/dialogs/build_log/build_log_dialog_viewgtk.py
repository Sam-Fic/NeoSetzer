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

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gdk, Gio, GObject
from setzer.widgets.search_highlight import highlight
from setzer.dialogs.build_log.build_log_dialog_presenter import classify_warning_type
from setzer.dialogs.build_log.build_log_filter_popover import BuildLogFilterPopover
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

        # AI Fix All 按钮：批量把当前可见错误发给 Agent CLI 修复。
        # 放在 HeaderBar 最左端（pack_end 后入先出，会排在 copy_all/save_log 左侧）。
        # icon 用 applications-science-symbolic（魔法/科学隐喻），与行内按钮一致。
        # 仅 Errors 类型才显示（presenter.populate 后由 controller 控制 sensitive）。
        self.ai_fix_all_button = Gtk.Button(icon_name='applications-science-symbolic')
        self.ai_fix_all_button.set_tooltip_text(_('Fix all errors with AI'))
        self.ai_fix_all_button.add_css_class('flat')
        self.ai_fix_all_button.set_can_focus(False)
        self.headerbar.pack_end(self.ai_fix_all_button)

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

        # 「恢复忽略的警告」按钮：仅当存在被忽略的 warning 类型时显示，
        # 避免误忽略后无法撤回（右键的 Undo toast 仅短暂存在）。
        self.restore_button = Gtk.Button()
        self.restore_button.set_child(Gtk.Image(icon_name='edit-undo-symbolic'))
        self.restore_button.set_tooltip_text(_('Restore ignored warnings'))
        self.restore_button.add_css_class('flat')
        self.restore_button.set_can_focus(False)
        self.restore_button.set_visible(False)
        self.restore_button.connect('clicked', self._on_restore_ignored_clicked)
        self.headerbar.pack_end(self.restore_button)
        self.on_restore_ignored_callback = None

        # Filter 弹层：包成独立类 BuildLogFilterPopover，内部用
        #   Adw.ComboRow（外包 Adw.PreferencesGroup）  替代 Gtk.ComboBoxText
        #   Adw.PreferencesGroup + Adw.SwitchRow×3      替代 Gtk.CheckButton×3
        # 行号范围的两个 Gtk.SpinButton 归 popover 拥有，view 仍以同名
        # 属性（line_min_spin / line_max_spin）暴露。
        # 整体 toggle 行为：仍用 Gtk.ToggleButton + 手动 Popover，原因是
        # Gtk.MenuButton 与 Adw.Dialog 内的 Popover 偶现无法通过再次点击
        # 按钮或点击空白处关闭（只能 Esc），手动 popup/popdown 可规避。
        self.filter_popover = BuildLogFilterPopover()
        self.filter_popover.set_parent(self.filter_button)
        self.filter_button.connect('notify::active', self._on_filter_button_active)
        self.filter_popover.connect('closed', self._on_filter_popover_closed)
        # 暴露 popover 内常用控件：controller 用 file_combo / type_combo
        # （Adw.ComboRow）的 notify::selected，error_switch / warning_switch /
        # badbox_switch（Adw.SwitchRow）的 notify::active。spin button 同名
        # 暴露（仍是 Gtk.SpinButton，value-changed 信号不变）。
        self.file_combo = self.filter_popover.file_combo
        self.type_combo = self.filter_popover.type_combo
        self.error_switch = self.filter_popover.error_switch
        self.warning_switch = self.filter_popover.warning_switch
        self.badbox_switch = self.filter_popover.badbox_switch
        self.line_min_spin = self.filter_popover.line_min_spin
        self.line_max_spin = self.filter_popover.line_max_spin

        # 搜索按钮（toggle 控制搜索栏显隐）
        self.search_button = Gtk.ToggleButton(icon_name='edit-find-symbolic')
        self.search_button.set_tooltip_text(_('Search build log'))
        self.search_button.add_css_class('flat')
        self.search_button.set_can_focus(False)
        self.headerbar.pack_start(self.search_button)

        # 搜索栏：用 Gtk.Revealer 包裹 Gtk.SearchEntry（不放 Gtk.SearchBar）。
        # 原因：Gtk.SearchBar 会在顶层挂 key controller 处理 Escape 以退出搜索
        # 模式，可能干扰 Adw.Dialog 自身的 Esc 关闭。Revealer + SearchEntry
        # 不挂任何顶层 key controller，Esc 始终交给 Adw.Dialog 原生处理。
        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.search_revealer.set_transition_duration(150)

        search_clamp = Adw.Clamp()
        search_clamp.set_maximum_size(600)
        search_clamp.set_tightening_threshold(500)

        search_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_bar_box.set_margin_top(4)
        search_bar_box.set_margin_bottom(4)
        search_bar_box.set_margin_start(6)
        search_bar_box.set_margin_end(6)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        search_bar_box.append(self.search_entry)
        search_clamp.set_child(search_bar_box)
        self.search_revealer.set_child(search_clamp)
        self.topbox.append(self.search_revealer)

        self.search_button.bind_property('active', self.search_revealer, 'reveal-child',
                                          GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        # content
        # toast_overlay 需要覆盖弹窗的整个 content 区域，这样 toast 才会贴在窗口底部。
        # 用 content_box 包裹 page 和 empty_state，再作为 overlay 的 child。
        self.topbox.set_vexpand(True)

        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.set_vexpand(True)

        self.page = Adw.PreferencesPage()
        self.page.set_vexpand(True)
        self.content_box.append(self.page)
        self.topbox.append(self.toast_overlay)

        # group 折叠/展开切换回调（由 controller 注入）
        self.on_group_toggle_callback = None

        # 3 个 group（按 TYPE_ORDER 顺序）。group 内嵌 BuildLogList。
        # 显隐由 presenter.populate 按 settings.autoshow_build_log 控制。
        # 每个 group 的内容用 Gtk.Revealer 包裹，支持折叠/展开。
        # TYPE_LABELS 在此运行时构建（_() 需在 gettext.install 后才可用）。
        type_labels = {
            'Error': _('Errors'),
            'Warning': _('Warnings'),
            'Badbox': _('Badboxes'),
        }
        self.groups = {}
        self.lists = {}
        self.revealers = {}
        self.toggle_buttons = {}
        for item_type in TYPE_ORDER:
            group = Adw.PreferencesGroup()
            group.set_title(type_labels[item_type])
            self.page.add(group)
            self.groups[item_type] = group

            # 折叠/展开切换按钮（放在 group header 右侧）
            toggle_btn = Gtk.Button()
            toggle_btn.set_icon_name('pan-down-symbolic')
            toggle_btn.add_css_class('flat')
            toggle_btn.set_can_focus(False)
            toggle_btn.connect('clicked', self._on_group_toggle_clicked, item_type)
            group.set_header_suffix(toggle_btn)
            self.toggle_buttons[item_type] = toggle_btn

            # 用 Gtk.Revealer 包裹 BuildLogList，支持折叠动画
            revealer = Gtk.Revealer()
            revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
            revealer.set_transition_duration(150)
            lst = BuildLogList(self.toast_overlay)
            revealer.set_child(lst)
            group.add(revealer)
            self.revealers[item_type] = revealer
            self.lists[item_type] = lst

        # 空状态占位：全部 group 都为空时显示。
        self.empty_state = Adw.StatusPage()
        self.empty_state.set_icon_name('object-select-symbolic')
        self.empty_state.set_title(_('Build Log'))
        self.empty_state.set_description(_('No build log items to show.'))
        self.empty_state.set_vexpand(True)
        self.empty_state.set_visible(False)
        self.content_box.append(self.empty_state)

        self.toast_overlay.set_child(self.content_box)

    def _on_filter_button_active(self, button, gparam):
        '''手动控制 filter popover 的显示/隐藏：按钮按下时弹出，抬起时收起。'''
        if button.get_active():
            self.filter_popover.popup()
        else:
            self.filter_popover.popdown()

    def _on_filter_popover_closed(self, popover):
        '''点击空白处或按 Esc 关闭 popover 后，同步取消按钮的按下状态。'''
        self.filter_button.set_active(False)

    def _on_group_toggle_clicked(self, button, item_type):
        '''点击 group header 的折叠按钮：切换展开/折叠状态。'''
        revealer = self.revealers.get(item_type)
        if revealer is None:
            return
        expanded = revealer.get_child_revealed()
        new_expanded = not expanded
        self.set_group_expanded(item_type, new_expanded)
        if self.on_group_toggle_callback is not None:
            self.on_group_toggle_callback(item_type, new_expanded)

    def set_group_expanded(self, item_type, expanded):
        '''设置指定 group 的展开/折叠状态。'''
        revealer = self.revealers.get(item_type)
        toggle_btn = self.toggle_buttons.get(item_type)
        if revealer is None or toggle_btn is None:
            return
        revealer.set_reveal_child(expanded)
        # 切换箭头方向：展开时朝下（pan-down），折叠时朝右（pan-end）
        toggle_btn.set_icon_name('pan-down-symbolic' if expanded else 'pan-end-symbolic')

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

    def add_stage_header(self, item_type, stage):
        '''在对应类型的列表中插入一个阶段分隔标题行。'''
        lst = self.lists.get(item_type)
        if lst is None:
            return
        lst.add_stage_header(stage)

    def set_header_title(self, title, subtitle=''):
        '''更新 HeaderBar 的标题/副标题（构建状态信息）。'''
        self.title_widget.set_title(title)
        self.title_widget.set_subtitle(subtitle)

    def set_restore_visible(self, visible, count=0):
        '''更新「恢复忽略的警告」按钮的显隐与文案。'''
        if visible and count > 0:
            self.restore_button.set_tooltip_text(
                _('Restore {count} ignored warning type(s)').format(count=count))
        self.restore_button.set_visible(visible and count > 0)

    def _on_restore_ignored_clicked(self, button):
        '''头部「恢复忽略的警告」按钮：转发给 controller 注入的回调。'''
        if self.on_restore_ignored_callback is not None:
            self.on_restore_ignored_callback()

    def update_file_filter(self, filenames):
        '''更新文件过滤下拉框的选项列表。

        presenter 把 "All" 显式塞入首项；popover.set_file_options 容忍这种
        输入（会自动 dedup 重复的 "All"）。

        信号抑制：presenter 在调用本方法前已置 ``_updating_filters=True``，
        controller 的 ``on_filter_changed`` 即使收到 ``notify::selected``
        也会在 ``set_filter_values`` 早退，不需要额外的 handler_block。
        '''
        self.filter_popover.set_file_options(filenames)

    def update_type_filter(self, error_types):
        '''更新错误类型过滤下拉框的选项列表。语义同 update_file_filter。'''
        self.filter_popover.set_type_options(error_types)

    def get_selected_types(self):
        '''获取当前选中的日志类型集合。委托给 popover。'''
        return self.filter_popover.get_visible_types()

    def set_selected_types(self, selected_types):
        '''设置选中的日志类型（用于恢复过滤器状态）。委托给 popover。'''
        self.filter_popover.set_visible_types(selected_types)


class BuildLogList(Gtk.ListBox):
    '''原生 Gtk.ListBox + Adw.ActionRow，复用 Pass-7/Pass-9 设计。

    每行：[类型 icon] 标题(描述) / 副标题(文件:行号)。单击激活由 controller
    的 row-activated 处理（跳转报错行）；右键直接 copy 单行（GestureClick
    监听 SECONDARY button）。

    boxed-list CSS class 提供 libadwaita 标准列表外观（圆角 + 行间细线分隔）；
    compact-rows 收紧行距（与 Pass-9 一致）。
    '''

    def __init__(self, toast_overlay=None):
        Gtk.ListBox.__init__(self)
        # SINGLE 选择模式 + 可聚焦：方向键即可在构建日志条目间导航（可访问性）。
        # 单击激活（跳转报错行）仍由 controller 的 row-activated 处理，不受影响。
        self.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.set_activate_on_single_click(True)
        self.add_css_class('boxed-list')
        self.add_css_class('compact-rows')
        # AI 修复行内按钮的回调注入点。controller 在 init 时设置：
        #   lst.ai_fix_row_callback = self.on_ai_fix_row_clicked
        # BuildLogList 不直接依赖 ai_fix 服务，仅把 row 转发给 controller，
        # 避免把 service 耦合进纯视图层（与 copy 按钮范式一致）。
        self.ai_fix_row_callback = None
        # 右键上下文菜单「忽略此类 warning」的回调注入点（由 controller 设置）。
        self.ignore_row_callback = None
        # 当前搜索文本，供 make_row 对标题/副标题做命中加粗（空串即不高亮）。
        self.search_text = ''
        self.toast_overlay = toast_overlay

        # 右键上下文菜单：复制单行 + 「忽略 <类型> 类警告」。整个列表共享一个
        # Gtk.Popover，每次右键只更新文案与可见性，避免逐项建 popover 的开销。
        self._row_menu = Gtk.Popover()
        self._row_menu.set_autohide(True)
        self._row_menu.set_has_arrow(True)
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        menu_box.set_margin_top(4)
        menu_box.set_margin_bottom(4)
        menu_box.set_margin_start(4)
        menu_box.set_margin_end(4)
        self._menu_copy_button = Gtk.Button()
        self._menu_copy_button.set_hexpand(True)
        self._menu_copy_button.set_has_frame(False)
        self._menu_copy_button.set_halign(Gtk.Align.FILL)
        self._menu_copy_button.connect('clicked', self._on_menu_copy_clicked)
        menu_box.append(self._menu_copy_button)
        self._menu_ignore_button = Gtk.Button()
        self._menu_ignore_button.set_hexpand(True)
        self._menu_ignore_button.set_has_frame(False)
        self._menu_ignore_button.set_halign(Gtk.Align.FILL)
        self._menu_ignore_button.connect('clicked', self._on_menu_ignore_clicked)
        menu_box.append(self._menu_ignore_button)
        self._row_menu.set_child(menu_box)
        self._row_menu_row = None

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
        # use-markup 让标题/副标题渲染 Pango markup，供搜索命中加粗使用。
        row.set_use_markup(True)
        row.add_prefix(Gtk.Image(icon_name=ICON_MAP.get(item_type, 'dialog-warning-symbolic')))
        # 搜索命中高亮：标题(描述)/副标题(文件:行号)中匹配子串加粗。
        title_text = description if description else ''
        row.set_title(highlight(title_text, self.search_text))
        # 长消息（多行 description）默认截断，tooltip 展示完整信息。
        if description:
            row._full_description = description
            row.set_tooltip_text(description)
        if filename:
            subtitle = os.path.basename(filename)
            if line_number >= 0:
                subtitle += ':' + str(line_number)
            row.set_subtitle(highlight(subtitle, self.search_text) if subtitle else '')
            row._full_subtitle = subtitle
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

        # 行尾 AI 修复按钮：点击把此错误（+ 上下文）发给 Agent CLI。
        # 复用与 copy 按钮一致的 flat/center 样式；点击通过
        # ai_fix_row_callback 转发给 controller（不直接依赖 service）。
        # icon 用 applications-science-symbolic，与顶栏 Fix All 一致。
        ai_fix_button = Gtk.Button(icon_name='applications-science-symbolic')
        ai_fix_button.set_tooltip_text(_('Fix this error with AI'))
        ai_fix_button.add_css_class('flat')
        ai_fix_button.set_valign(Gtk.Align.CENTER)
        ai_fix_button.set_can_focus(False)
        ai_fix_button.connect('clicked', self.on_ai_fix_row_clicked, row)
        row.add_suffix(ai_fix_button)

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

    def on_ai_fix_row_clicked(self, button, row):
        '''行尾 AI 修复按钮点击：把 row 转发给 controller 注入的回调。

        row.filename / line_number / description / item_type 由 make_row 设置。
        BuildLogList 不直接调用 ai_fix 服务，避免把 service 耦合进纯视图层
        （与 copy 按钮通过 self.toast_overlay 的范式一致）。
        '''
        if self.ai_fix_row_callback is not None:
            self.ai_fix_row_callback(row)

    def on_right_click(self, gesture, n_press, x, y, row):
        '''右键弹出上下文菜单：复制单行 / 忽略此类 warning。

        错误（Error）默认不提供「忽略」（误忽略会掩盖真实编译失败），
        Warning / Badbox 才展示忽略项。
        '''
        key, label = classify_warning_type(row.item_type, row.description)
        self._row_menu_row = row
        self._menu_copy_button.set_label(_('Copy to clipboard'))
        can_ignore = row.item_type in ('Warning', 'Badbox')
        if can_ignore:
            self._menu_ignore_button.set_visible(True)
            self._menu_ignore_button.set_label(_('Ignore “{label}” warnings').format(label=label))
        else:
            self._menu_ignore_button.set_visible(False)
        self._row_menu.set_parent(row)
        rect = GdkRectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self._row_menu.set_pointing_to(rect)
        self._row_menu.popup()

    def _on_menu_copy_clicked(self, button):
        '''菜单「复制」：复用行尾复制按钮行为。'''
        if self._row_menu_row is not None:
            self.on_copy_row_clicked(button, self._row_menu_row)
        self._row_menu.popdown()
        self._row_menu.unparent()

    def _on_menu_ignore_clicked(self, button):
        '''菜单「忽略此类 warning」：把 row 转发给 controller 注入的回调。'''
        row = self._row_menu_row
        self._row_menu.popdown()
        self._row_menu.unparent()
        if row is not None and self.ignore_row_callback is not None:
            self.ignore_row_callback(row)

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

    def add_stage_header(self, stage):
        '''追加一个阶段分隔行（不可选中、不可激活）。

        用裸 Gtk.ListBoxRow + Gtk.Label，区分度靠 CSS 字重 + 文字色
        （见 .build-log-stage-header），不引入 accent 边框，从根上避免
        强调色泄漏。
        '''
        row = Gtk.ListBoxRow()
        row.add_css_class('build-log-stage-header')
        label = Gtk.Label(label=stage)
        label.set_xalign(0)
        row.set_child(label)
        row.set_activatable(False)
        row.set_selectable(False)
        self.append(row)

    def clear_rows(self):
        '''清空所有子行。GTK 4.6+ 的 Gtk.ListBox.remove_all 内部批量释放，
        替代原手动 get_first_child + remove 循环（n 次 remove 各 O(n) → O(n²)）。'''
        self.remove_all()

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
# along with this program. If not, see <http://www.gnu.org/licenses/>.

'''Build log 弹窗的过滤 popover。

历史：本组件最初是手拼的 ``Gtk.Box`` + ``Gtk.ComboBoxText`` +
``Gtk.CheckButton``。本 Pass 重构为标准 libadwaita 组件：

- 文件 / 错误类型下拉框 → ``Adw.ComboRow``，外包在 ``Adw.PreferencesGroup``
  （无标题）里，与下方 SwitchRow 同画风（boxed list 圆角 + 行间细线）。
- 类型开关（Errors / Warnings / Badboxes）→ 三个 ``Adw.SwitchRow``，
  外包在 ``Adw.PreferencesGroup("Show types")`` 里。

为什么 ComboRow 而不是 ``Gtk.DropDown``：把 ``Adw.ActionRow + Gtk.DropDown``
作 suffix 放进普通 Box，DropDown 自带的灰色圆角胶囊背景与 boxed list
内行的"无背景、单行高亮"画风割裂。ComboRow 本身是 PreferencesRow 子类，
与 SwitchRow 共享同一套行视觉，整段放进 PreferencesGroup 后形成"无缝
列表"。这是 libadwaita 在过滤弹层里的标准做法（GNOME Builder 等
应用均如此）。

为什么 ComboRow 必须放在 PreferencesGroup 里：libadwaita 文档明确
``Adw.ComboRow`` 是 ``Adw.PreferencesRow`` 子类，标准用法是
``group.add(combo_row)``。放进普通 Box 会丢失标题/副标题/激活区域
的 row 视觉（这是 Pass-1 选 DropDown 时的折中理由，但视觉上不可接受）。

对外接口：
    popover.file_combo / popover.type_combo      → Adw.ComboRow
    popover.error_switch / popover.warning_switch / popover.badbox_switch
    popover.line_min_spin / popover.line_max_spin
    popover.get_file_label()  → 当前选中的字符串（"All" 或文件名）
    popover.get_type_label()  → 当前选中的字符串（"All" 等）
    popover.get_visible_types() → {'Error', 'Warning', 'Badbox'} 子集
    popover.set_file_options(names) / set_type_options(names)  重建 model
    popover.set_visible_types(set)

ComboRow 的 ``set_model`` 会把 ``selected`` 重置为 0（GTK 文档原话），
因此 ``set_file_options`` / ``set_type_options`` 总是先把 selected 重置
为 0；如需保留上次选择，由 presenter 在调用前后手动 set_selected。

信号抑制策略：``set_model`` / ``set_active`` 触发的 ``notify::selected``
/ ``notify::active`` 会被 controller 的 ``on_filter_changed`` 接收，再
调 ``presenter.set_filter_values``。后者在 ``_updating_filters=True`` 时
早退（presenter 在重建 model 前已设该标志），所以中间状态不会触发重建
循环——无需额外的 ``handler_block``。
'''

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw


class BuildLogFilterPopover(Gtk.Popover):
    '''Build log 过滤弹层：File / Type ComboRow + Errors/Warnings/Badboxes 开关 + 行号范围。'''

    def __init__(self):
        Gtk.Popover.__init__(self)
        self.set_autohide(True)
        # 弹层最小宽度 280px：与 build log 主体行宽对齐，三个 SwitchRow 不会被
        # 压到 100px 以下。高度由子节点自然尺寸决定，不设上限。
        self.set_size_request(280, -1)

        # 根容器：垂直 Box，spacing 留给 PreferencesGroup 之间的视觉间隔
        # (boxed list 自身有内部 padding，多个 group 紧密相邻即可)。
        outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer_box.set_margin_top(8)
        outer_box.set_margin_bottom(8)
        outer_box.set_margin_start(8)
        outer_box.set_margin_end(8)

        # ---- 段 1：File / Type 两个 Adw.ComboRow 在同一个 PreferencesGroup（无标题）----
        # 无标题 PreferencesGroup：set_title('') 隐藏标题，group 仍展示为
        # boxed list。GNOME Builder 的过滤弹层即此模式。
        filters_group = Adw.PreferencesGroup()
        filters_group.set_title('')
        self.file_combo = Adw.ComboRow()
        self.file_combo.set_title(_('File'))
        self.file_combo.set_model(Gtk.StringList.new([_('All')]))
        filters_group.add(self.file_combo)
        self.type_combo = Adw.ComboRow()
        self.type_combo.set_title(_('Type'))
        self.type_combo.set_model(Gtk.StringList.new([_('All')]))
        filters_group.add(self.type_combo)
        outer_box.append(filters_group)

        # ---- 段 2：行号范围（不在本 Pass 改造范围内，保留原 Box 形态）----
        # 两个 SpinButton 仍用普通 Gtk.Box 横排。它们与上方 ComboRow
        # 视觉不一致是已有设计（行号输入需要两个并排 + "–" 分隔），
        # 与本次 ComboRow 化正交。
        line_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        line_box.set_margin_top(8)
        line_box.set_margin_bottom(8)
        line_label = Gtk.Label(label=_('Lines:'))
        line_label.set_halign(Gtk.Align.START)
        line_box.append(line_label)
        self.line_min_spin = Gtk.SpinButton.new_with_range(0, 999999, 1)
        line_box.append(self.line_min_spin)
        line_box.append(Gtk.Label(label=_('–')))
        self.line_max_spin = Gtk.SpinButton.new_with_range(0, 999999, 1)
        self.line_max_spin.set_value(999999)
        line_box.append(self.line_max_spin)
        outer_box.append(line_box)

        # ---- 段 3：Show types——PreferencesGroup("Show types") + 3×Adw.SwitchRow ----
        types_group = Adw.PreferencesGroup()
        types_group.set_title(_('Show types'))
        self.error_switch = Adw.SwitchRow(title=_('Errors'))
        self.error_switch.set_active(True)
        types_group.add(self.error_switch)
        self.warning_switch = Adw.SwitchRow(title=_('Warnings'))
        self.warning_switch.set_active(True)
        types_group.add(self.warning_switch)
        self.badbox_switch = Adw.SwitchRow(title=_('Badboxes'))
        self.badbox_switch.set_active(True)
        types_group.add(self.badbox_switch)
        outer_box.append(types_group)

        self.set_child(outer_box)

    # ---- 取值 ----

    def get_file_label(self):
        '''返回当前选中的文件标签（"All" 或某文件名）。与原 ComboBoxText 接口一致。'''
        item = self.file_combo.get_selected_item()
        if item is None:
            return None
        return item.get_string()

    def get_type_label(self):
        '''返回当前选中的错误类型标签。'''
        item = self.type_combo.get_selected_item()
        if item is None:
            return None
        return item.get_string()

    def get_visible_types(self):
        '''返回用户勾选的可见类型集合（与原 get_selected_types 等价）。'''
        selected = set()
        if self.error_switch.get_active():
            selected.add('Error')
        if self.warning_switch.get_active():
            selected.add('Warning')
        if self.badbox_switch.get_active():
            selected.add('Badbox')
        return selected

    # ---- 设值 ----

    def set_file_options(self, names):
        '''重建 File ComboRow 的选项列表，首项固定为 "All"。

        ``names`` 可以包含或不包含 "All"：
        - 若首项就是 "All"（presenter 把 "All" 显式塞入的情形），原样使用；
        - 否则由本方法 prepend "All"。

        容忍两种输入是出于"popover 对调用方友好"的设计——presenter 不必
        知道 popover 内部是否会自动 prepend。重复的 "All" 会被 dedup。

        ComboRow 的 ``set_model`` 行为（GTK 4 文档）：selected 会被重置为 0。
        这是想要的行为（model 变化后默认 "All"），presenter 如果要保留
        用户之前的选择，需要在调用本方法后用 ``set_selected(idx)`` 重新
        设置。

        信号抑制：``set_model`` 会触发 ``notify::selected``，进而调起 controller
        的 ``on_filter_changed`` → ``presenter.set_filter_values``。后者已检查
        ``_updating_filters`` 标志（在 ``_update_filter_options`` 调用本方法前
        已置 True），所以中间状态会被早退，不会触发重建循环——无需额外的
        ``handler_block``。
        '''
        all_label = _('All')
        if not names or names[0] != all_label:
            names = [all_label] + list(names)
        else:
            # 首项已是 "All"，剥掉并 prepend 一次以 dedup
            rest = [n for n in names[1:] if n != all_label]
            names = [all_label] + rest
        self.file_combo.set_model(Gtk.StringList.new(names))
        self.file_combo.set_selected(0)

    def set_type_options(self, names):
        '''重建 Type ComboRow 的选项列表，首项固定为 "All"。语义同 set_file_options。'''
        all_label = _('All')
        if not names or names[0] != all_label:
            names = [all_label] + list(names)
        else:
            rest = [n for n in names[1:] if n != all_label]
            names = [all_label] + rest
        self.type_combo.set_model(Gtk.StringList.new(names))
        self.type_combo.set_selected(0)

    def set_visible_types(self, selected_types):
        '''根据 selected_types 集合同步三个 SwitchRow 的状态。

        ``Adw.SwitchRow`` 继承自 ``Gtk.Switch``，``set_active`` 触发 ``notify::active``。
        与 ComboRow 同理，presenter 的 ``_updating_filters`` 标志会早退 controller 的
        ``on_filter_changed``，所以不需要额外的 signal block。
        '''
        self.error_switch.set_active('Error' in selected_types)
        self.warning_switch.set_active('Warning' in selected_types)
        self.badbox_switch.set_active('Badbox' in selected_types)

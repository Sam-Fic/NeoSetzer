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

import os.path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Adw

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget


class StructureSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'document-properties-symbolic',
            _('No document structure'),
            _('Add \\section{}, \\chapter{}, or other sectioning commands to outline your document.')
        )

    def populate(self):
        # 签名含 (level, icon, title) 的深度优先序列 + id(document) + 折叠集合。
        # 按键在正文时结构不变 → 签名命中 → 跳过 clear_rows + 重建。
        # 折叠集合变化（展开/收起）也改变签名 → 触发重建，反映可见子树变化。
        doc = self.model.data_provider.document
        acc = []
        self._collect_signature(self.model.nodes, 0, acc)
        signature = (id(doc), tuple(acc), tuple(sorted(self.model.collapsed)))
        if not self.populate_if_changed(signature):
            return
        # 重建前记下当前过滤查询（clear_rows 会清空它），重建后对新建行重新应用，
        # 保证展开/收起时新出现的行也遵循活跃搜索过滤。
        filter_query = self._last_filter_query
        self.clear_rows()
        self.add_nodes(self.model.nodes, 0)
        self.set_empty_state_visible(len(self.model.nodes) == 0)
        if filter_query:
            self.filter_rows(filter_query)

    def _collect_signature(self, nodes, level, acc):
        for node in nodes:
            item = node['item']
            acc.append((level, item[2], item[3]))
            self._collect_signature(node['children'], level + 1, acc)

    def add_nodes(self, nodes, level):
        for node in nodes:
            item = node['item']
            icon_name = item[2]
            if icon_name == 'file-symbolic':
                text = os.path.basename(item[3])
            else:
                text = item[3]
            has_children = len(node['children']) > 0
            expanded = node['offset'] not in self.model.collapsed
            row = self.make_row(icon_name, text, level, node, has_children, expanded)
            row.item_data = node
            self.model.register_row(row, node)
            self.append_row(row)
            # 仅当节点有子节点且处于展开状态时才递归其子节点；
            # 折叠节点不渲染子树，实现真正的收起而非仅视觉缩进。
            if has_children and expanded:
                self.add_nodes(node['children'], level + 1)

    def make_row(self, icon_name, text, level, node, has_children, expanded):
        row = Adw.ActionRow()
        row.set_selectable(False)
        # 保留行可激活：点击行（非展开器）仍跳转至对应节。
        row.set_activatable(True)
        prefix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        # 纯缩进表达嵌套层级：按层级递增的左缩进，不绘制竖线占位，更简洁、
        # 不依赖字体字形宽度。每层 INDENT 像素。
        INDENT = 16
        if level > 0:
            prefix_box.set_margin_start(level * INDENT)
        if has_children:
            # 可点击的展开器：三角形指示展开/收起。使用 CAPTURE 阶段 GestureClick
            # 在 pressed 时 claim 事件序列，阻止事件冒泡到 ListBox 行触发 row-activated
            # （即跳转），仅在展开器上收起/展开而不导航；released 时执行切换。
            expander = Gtk.Box()
            # 点击命中区放大到 22×22，箭头仍保持 14px 居中，便于点击/触控。
            expander.set_size_request(22, 22)
            expander.set_valign(Gtk.Align.CENTER)
            expander.add_css_class('structure-expander')
            arrow = Gtk.Image()
            arrow.set_from_icon_name('pan-down-symbolic' if expanded else 'pan-end-symbolic')
            arrow.set_pixel_size(14)
            expander.append(arrow)
            gesture = Gtk.GestureClick()
            gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            gesture.connect('pressed', self._on_expander_pressed, node)
            gesture.connect('released', self._on_expander_released, node)
            expander.add_controller(gesture)
            try:
                expander.set_cursor(Gdk.Cursor.new_from_name('pointer'))
            except Exception:
                pass
            prefix_box.append(expander)
        else:
            # 无子节点：占位以与有展开器的同级节点对齐图标（同宽 22 保证对齐）。
            spacer = Gtk.Box()
            spacer.set_size_request(22, -1)
            prefix_box.append(spacer)
        prefix_box.append(Gtk.Image(icon_name=icon_name))
        row.add_prefix(prefix_box)
        row.set_title(text)
        row._title_text = text
        self._capture_row_labels(row, text)
        # 标题可能被容器宽度截断（ellipsize），hover 时给出完整文本。
        row.set_tooltip_text(text)
        # 无障碍：outline 行本质是文档结构的树形项，设为 tree-item 角色
        # 并暴露层级，使屏幕阅读器能朗读「level N」而非仅标题文本。
        role = getattr(Gtk.AccessibleRole, 'TREE_ITEM', None)
        if role is not None:
            row.set_accessible_role(role)
        level_prop = getattr(Gtk.AccessibleProperty, 'LEVEL', None)
        if level_prop is not None:
            try:
                row.update_property(level_prop, level + 1)
            except TypeError:
                pass
        # 暴露展开/收起状态给屏幕阅读器（仅对有子节点的行有意义）。
        expanded_prop = getattr(Gtk.AccessibleProperty, 'EXPANDED', None)
        if expanded_prop is not None and has_children:
            try:
                row.update_property(expanded_prop, expanded)
            except (TypeError, ValueError):
                pass
        return row

    def _on_expander_pressed(self, gesture, n_press, x, y, node):
        # 截断事件传播，避免点击展开器时激活行（跳转）。
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_expander_released(self, gesture, n_press, x, y, node):
        self.model.toggle_node(node['offset'])

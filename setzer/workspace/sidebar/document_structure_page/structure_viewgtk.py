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

import os.path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Adw, Gio

import setzer.workspace.sidebar.document_structure_page.structure_widget as structure_widget
from setzer.app.service_locator import ServiceLocator


class StructureSectionView(structure_widget.StructureWidget):

    def __init__(self, model):
        structure_widget.StructureWidget.__init__(self, model)
        self.set_empty_state(
            'document-properties-symbolic',
            _('No document structure'),
            _('Add \\section{}, \\chapter{}, or other sectioning commands to outline your document.')
        )
        # 右键菜单上下文：被右键的节点，win.outline-ctx-* action 激活时读取。
        self._ctx_node = None
        # 把上下文 action 注册到主窗口而非 PopoverMenu（见 _register_context_actions）。
        self._register_context_actions()

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
        # After rebuilding rows, sync the keyboard selection.
        # The _sync_selection_to_accent_row() is inherited from StructureWidget
        # and will select the row matching the cursor position (accent class)
        # or fall back to the first visible row.
        self._sync_selection_to_accent_row()

    def _collect_signature(self, nodes, level, acc):
        for node in nodes:
            item = node['item']
            acc.append((level, item[2], item[3]))
            self._collect_signature(node['children'], level + 1, acc)

    def add_nodes(self, nodes, level):
        for node in nodes:
            item = node['item']
            icon_name = item[2]
            if icon_name == 'text-x-generic-symbolic':
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
        row.set_selectable(True)
        # 保留行可激活：点击行（非展开器）仍跳转至对应节。
        row.set_activatable(True)
        # Store tree navigation info on the row for keyboard handling.
        row.has_children = has_children
        row.is_expanded = expanded
        row.node_offset = node['offset']
        row.tree_level = level
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
        # Right-click context menu: attach GestureClick with button=3 (secondary)
        row.right_click_gesture = Gtk.GestureClick()
        row.right_click_gesture.set_button(3)
        row.right_click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        row.right_click_gesture.connect('pressed', self._on_row_right_click_pressed, row)
        row.right_click_gesture.connect('released', self._on_row_right_click_released, row)
        row.add_controller(row.right_click_gesture)
        return row

    def _on_row_right_click_pressed(self, gesture, n_press, x, y, row):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_row_right_click_released(self, gesture, n_press, x, y, row):
        node = getattr(row, 'item_data', None)
        if node is None:
            return
        self._show_context_menu(row, node, x, y)

    def _build_menu_model(self, node):
        """Build a Gio.Menu model for the context menu."""
        block = node.get('block', None)
        if block is None:
            return None

        section_type = block[4] if len(block) > 4 else None
        is_file = section_type == 'file'
        is_frame = section_type == 'frame'
        is_editable_section = not is_file and not is_frame

        menu = Gio.Menu()

        # Copy section
        copy_section = Gio.Menu()
        copy_section.append_item(Gio.MenuItem.new(_('Copy Title'), 'win.outline-ctx-copy-title'))
        menu.append_section(None, copy_section)

        if not is_file:
            label_name = self.model.find_label_for_node(node)
            if label_name is not None:
                copy_section.append_item(
                    Gio.MenuItem.new(_('Copy Reference') + f'  \\ref{{{label_name}}}', 'win.outline-ctx-copy-ref'))
                copy_section.append_item(
                    Gio.MenuItem.new(_('Copy Label') + f'  \\label{{{label_name}}}', 'win.outline-ctx-copy-label'))

            if is_editable_section:
                # Edit section
                edit_section = Gio.Menu()
                edit_section.append_item(Gio.MenuItem.new(_('Rename Section…'), 'win.outline-ctx-rename'))
                edit_section.append_item(Gio.MenuItem.new(_('Delete Section'), 'win.outline-ctx-delete'))
                menu.append_section(None, edit_section)

                # Level section
                can_promote = section_type in self.model.levels and self.model.levels[section_type] > 0
                can_demote = section_type in self.model.levels and self.model.levels[section_type] < len(self.model.levels) - 1

                level_section = Gio.Menu()
                promote_item = Gio.MenuItem.new(_('Promote'), 'win.outline-ctx-promote')
                demote_item = Gio.MenuItem.new(_('Demote'), 'win.outline-ctx-demote')
                level_section.append_item(promote_item)
                level_section.append_item(demote_item)
                menu.append_section(None, level_section)

                # Store capabilities for action enable/disable
                self._can_promote = can_promote
                self._can_demote = can_demote
            else:
                self._can_promote = False
                self._can_demote = False

        return menu

    def _register_context_actions(self):
        '''在 main_window 上注册带 win. 前缀的上下文 action。

        把 action 注册到窗口而非 PopoverMenu，可避免 Gtk.PopoverMenu 基于
        menu model 渲染的菜单项在点击时无法解析到 action group 的问题
        （与 files_viewgtk 的修复相同，见 commit 9c8e0c23）。
        被右键的节点存于 self._ctx_node，激活时由各 handler 读取。
        '''
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return
        if main_window.lookup_action('outline-ctx-copy-title') is not None:
            return  # 已经注册过

        def add(name, callback):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            main_window.add_action(action)

        add('outline-ctx-copy-title', self._on_action_copy_title)
        add('outline-ctx-copy-ref', self._on_action_copy_ref)
        add('outline-ctx-copy-label', self._on_action_copy_label)
        add('outline-ctx-rename', self._on_action_rename)
        add('outline-ctx-delete', self._on_action_delete)
        add('outline-ctx-promote', self._on_action_promote)
        add('outline-ctx-demote', self._on_action_demote)

    def _show_context_menu(self, row, node, x, y):
        block = node.get('block', None)
        if block is None:
            return

        # 惰性补注册：万一初始化顺序导致 __init__ 时 main_window 尚未就绪，
        # 这里再尝试一次（_register_context_actions 内部幂等）。
        self._register_context_actions()
        main_window = ServiceLocator.get_main_window()
        if main_window is None:
            return

        section_type = block[4] if len(block) > 4 else None
        is_file = section_type == 'file'
        is_frame = section_type == 'frame'
        is_editable_section = not is_file and not is_frame

        # Build the menu model
        menu_model = self._build_menu_model(node)
        if menu_model is None:
            return

        # 记录被右键的节点，win.outline-ctx-* action 激活时使用。
        self._ctx_node = node

        # Set action enable states based on capabilities
        has_label = self.model.find_label_for_node(node) is not None
        main_window.lookup_action('outline-ctx-copy-title').set_enabled(True)
        main_window.lookup_action('outline-ctx-copy-ref').set_enabled(
            not is_file and has_label)
        main_window.lookup_action('outline-ctx-copy-label').set_enabled(
            not is_file and has_label)
        main_window.lookup_action('outline-ctx-rename').set_enabled(is_editable_section)
        main_window.lookup_action('outline-ctx-delete').set_enabled(is_editable_section)
        main_window.lookup_action('outline-ctx-promote').set_enabled(
            is_editable_section and getattr(self, '_can_promote', False))
        main_window.lookup_action('outline-ctx-demote').set_enabled(
            is_editable_section and getattr(self, '_can_demote', False))

        # Create popover menu
        popover = Gtk.PopoverMenu()
        popover.set_parent(row)
        popover.set_has_arrow(False)
        popover.set_size_request(288, -1)
        popover.set_menu_model(menu_model)

        # Set up positioning
        # Standard context menu positioning: cursor lands at a corner of the popover,
        # not at the top center. By default, GTK centers the popover horizontally
        # on the pointing-to rect. We offset right by half the popover width (288/2=144)
        # so the cursor aligns with the popover's left edge.
        popover.set_offset(144, 0)

        rect = Gdk.Rectangle()
        rect.x = x
        rect.y = y
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _on_action_copy_title(self, action, parameter):
        self.model.copy_title(self._ctx_node)

    def _on_action_copy_ref(self, action, parameter):
        self.model.copy_ref(self._ctx_node)

    def _on_action_copy_label(self, action, parameter):
        self.model.copy_label(self._ctx_node)

    def _on_action_rename(self, action, parameter):
        self.model.rename_section(self._ctx_node)

    def _on_action_delete(self, action, parameter):
        self.model.delete_section(self._ctx_node)

    def _on_action_promote(self, action, parameter):
        self.model.promote_section(self._ctx_node)

    def _on_action_demote(self, action, parameter):
        self.model.demote_section(self._ctx_node)

    def _on_expander_pressed(self, gesture, n_press, x, y, node):
        # 截断事件传播，避免点击展开器时激活行（跳转）。
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_expander_released(self, gesture, n_press, x, y, node):
        self.model.toggle_node(node['offset'])

    def _on_list_box_key_pressed(self, controller, keyval, keycode, state):
        """Extend base handler with Left/Right for tree expand/collapse.

        Right: expand a collapsed node (if it has children).
        Left: collapse an expanded node, or move focus to the parent node
              if the current node is already collapsed or has no children.
        Otherwise, delegate to the base handler for Tab/Shift+Tab.
        """
        selected = self.list_box.get_selected_row()
        if selected is not None:
            if keyval == Gdk.KEY_Right:
                if getattr(selected, 'has_children', False) and not getattr(selected, 'is_expanded', False):
                    offset = selected.node_offset
                    self.model.toggle_node(offset)
                    self._select_row_by_offset(offset)
                    return True
            elif keyval == Gdk.KEY_Left:
                if getattr(selected, 'has_children', False) and getattr(selected, 'is_expanded', False):
                    offset = selected.node_offset
                    self.model.toggle_node(offset)
                    self._select_row_by_offset(offset)
                    return True
                else:
                    # Move focus to the parent node (previous node at a lower level)
                    parent_row = self._find_parent_row(selected)
                    if parent_row is not None:
                        self.list_box.select_row(parent_row)
                        return True
        # Fallback to base handler for Tab/Shift+Tab
        return super()._on_list_box_key_pressed(controller, keyval, keycode, state)

    def _select_row_by_offset(self, offset):
        """Find and select a visible row by its node_offset.

        After toggle_node → populate, all rows are recreated with new
        Python objects but the same node_offset. This method finds the
        new row matching the offset and selects it so keyboard focus
        stays on the toggled node rather than jumping to the cursor row.
        """
        child = self.list_box.get_first_child()
        while child is not None:
            if isinstance(child, Adw.ActionRow) and child.get_visible():
                if getattr(child, 'node_offset', None) == offset:
                    self.list_box.select_row(child)
                    return
            child = child.get_next_sibling()

    def _find_parent_row(self, row):
        """Find the parent row of the given row by walking backwards
        through visible rows and finding one with a lower tree_level."""
        current_level = getattr(row, 'tree_level', 0)

        # Collect all visible rows
        child = self.list_box.get_first_child()
        rows = []
        while child is not None:
            if isinstance(child, Adw.ActionRow) and child.get_visible():
                rows.append(child)
            child = child.get_next_sibling()

        try:
            idx = rows.index(row)
        except ValueError:
            return None

        for i in range(idx - 1, -1, -1):
            r = rows[i]
            r_level = getattr(r, 'tree_level', 0)
            if r_level < current_level:
                return r
        return None

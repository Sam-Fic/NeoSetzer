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

from setzer.app.service_locator import ServiceLocator
from gi.repository import GLib, Adw


class PreviewPanelPresenter(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.view = self.main_window.preview_panel
        self.stack = self.main_window.preview_panel.stack
        self.document = None
        self._label_update_timeout_id = None

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_active_document', self.on_new_active_document)
        self.workspace.connect('root_state_change', self.on_root_state_change)
        # Preview 可能晚于 new_document 才挂接（会话恢复的非活跃文档在激活
        # 时才建工具链）：latex_toolchain_ready 时补做 stack 挂载。
        self.workspace.connect('latex_toolchain_ready', self.on_latex_toolchain_ready)
        # 弹出/收回时刷新 detach 按钮可见性（popped_out 时隐藏——独立窗口已 detached）。
        self.workspace.connect('preview_pop_state_changed', self.on_preview_pop_state_changed)

        self.view.page_spin.connect('activate', self._on_page_spin_activate)
        self.view.fit_width_button.connect('clicked', self._on_fit_width_clicked)

        self.view.switch_button.connect('clicked', self._on_switch_clicked)
        self.main_window.help_panel.switch_button.connect('clicked', self._on_switch_clicked)

        # 反向挂到 view，便于其它地方（如 workspace_presenter 切换同步）
        # 通过 main_window.preview_panel.presenter 访问。
        self.view.presenter = self

        # 按实际显示的面板同步一次按钮图标（图标始终展示"目标面板"）。
        self._sync_switch_icons()
        self.update_label()
        self.update_buttons()

        self.view.recolor_pdf_toggle.set_active(self.workspace.settings.get_value('preferences', 'recolor_pdf'))
        self.view.magnifier_toggle.set_active(self.workspace.settings.get_value('preferences', 'use_magnifier'))
        self.workspace.settings.connect('settings_changed', self.on_settings_changed)

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter

        if item == 'recolor_pdf':
            self.view.recolor_pdf_toggle.set_active(value)
        elif item == 'use_magnifier':
            self.view.magnifier_toggle.set_active(value)

    def on_new_document(self, workspace, document):
        if document.is_latex_document():
            # 工具链可能尚未挂接（会话恢复的非活跃轻量文档）：无 preview 则
            # 跳过，等 latex_toolchain_ready 再补挂 stack。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is not None:
                self.stack.add_child(doc_preview.view)

    def on_latex_toolchain_ready(self, workspace, document):
        '''延迟挂接的工具链就绪：补做 new_document 时被跳过的 stack 挂载。

        若该文档正是当前展示目标（如会话恢复时根文档在 set_active_document
        中补挂），还需刷新可见 child 与信号连接——set_preview_document 统一
        处理。'''
        if not document.is_latex_document():
            return
        doc_preview = getattr(document, 'preview', None)
        if doc_preview is not None and doc_preview.view.get_parent() is None:
            self.stack.add_child(doc_preview.view)
        if self.document is document:
            self.set_preview_document()

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            # preview 可能不存在（从未激活过的轻量文档）或 view 从未加入
            # stack：两种情况都无需（也不能）从 stack 移除。
            doc_preview = getattr(document, 'preview', None)
            if doc_preview is not None and doc_preview.view.get_parent() is not None:
                self.stack.remove(doc_preview.view)

    def on_new_active_document(self, workspace, document):
        self.set_preview_document()

    def on_root_state_change(self, workspace, root_state):
        self.set_preview_document()

    def on_preview_pop_state_changed(self, workspace, popped_out):
        # 弹出后 preview_panel 搬进独立窗口，detach 按钮在那里无意义（收回走窗口 X）；
        # 收回后恢复可见。update_buttons 也会顺带刷新按钮敏感状态。
        self.view.detach_button.set_visible(not popped_out)
        # 弹出时隐藏帮助面板的 switch 按钮：popped_out 下侧栏只有 help，
        # 点 switch 会尝试切到已搬走的 preview，无意义且带来问题。收回后恢复。
        self.main_window.help_panel.switch_button.set_visible(not popped_out)
        self.update_buttons()

    def set_preview_document(self):
        if self.document != None:
            # 旧文档的 preview 理应存在（本方法 connect 过才会成为展示目标），
            # 但 self.document 也可能被 update_buttons 直接重赋值，此处仍守卫。
            old_preview = getattr(self.document, 'preview', None)
            if old_preview is not None:
                old_preview.disconnect('pdf_changed', self.on_pdf_changed)
                old_preview.disconnect('position_changed', self.on_position_changed)
                old_preview.disconnect('layout_changed', self.on_layout_changed)
                old_preview.disconnect('pdf_stale_changed', self.on_pdf_stale_changed)
                old_preview.zoom_manager.disconnect('zoom_level_changed', self.on_zoom_level_changed)
                old_preview.zoom_manager.disconnect('zoom_clamped', self.on_zoom_clamped)

        self.document = self.workspace.get_root_or_active_latex_document()
        # 工具链未挂接的文档视同无预览（正常流程不会发生：激活/根文档都会
        # 先挂工具链，这里仅防御）。
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        if self.document == None or doc_preview is None:
            self.stack.set_visible_child(self.view.empty_placeholder)
            self.update_label()
            self.update_buttons()
            self._detach_target_bar()
        else:
            self.stack.set_visible_child(self.document.preview.view)
            self.update_label()
            self.update_buttons()
            self.update_zoom_level()
            self._sync_zoom_action_state()
            self.document.preview.connect('pdf_changed', self.on_pdf_changed)
            self.document.preview.connect('position_changed', self.on_position_changed)
            self.document.preview.connect('layout_changed', self.on_layout_changed)
            self.document.preview.connect('pdf_stale_changed', self.on_pdf_stale_changed)
            self.document.preview.zoom_manager.connect('zoom_level_changed', self.on_zoom_level_changed)
            self.document.preview.zoom_manager.connect('zoom_clamped', self.on_zoom_clamped)
            self._attach_target_bar(self.document.preview.view)

    def on_pdf_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self._sync_zoom_action_state()

    def on_position_changed(self, preview):
        self.update_label_debounced()

    def on_layout_changed(self, preview):
        self.update_label()

    def on_pdf_stale_changed(self, preview):
        # 构建失败未产出 PDF 时在页码区显示红色提示；下次构建成功（set_pdf_filename
        # 清除 stale）时隐藏。由 preview.set_pdf_is_stale 触发。
        self.update_label()
        self.update_buttons()

    def on_zoom_level_changed(self, preview):
        self.update_label()
        self.update_buttons()
        self.update_zoom_level()
        self._sync_zoom_action_state()

    def _sync_zoom_action_state(self):
        '''同步两个有状态 action 的 state，使弹窗里「当前缩放档位」或「当前 fit
        模式」前自动显示对钩（GTK 标准做法），二者互斥：
        - fit 模式激活时，fit 项高亮、数值项不亮；
        - 手动缩放（数值档位）时，对应数值项高亮、fit 项不亮。'''
        zoom_action = self.workspace.actions.actions.get('preview-set-zoom-level')
        fit_action = self.workspace.actions.actions.get('preview-fit-mode')
        if self.document is None:
            return
        zoom_manager = getattr(self.document, 'preview', None)
        if zoom_manager is None:
            return
        zoom_manager = zoom_manager.zoom_manager
        zoom_level = zoom_manager.get_zoom_level()
        mode = zoom_manager.zoom_mode

        # 与弹窗菜单一致的离散缩放档位。
        levels = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
        mode_map = {
            'fit_to_width': 'width',
            'fit_to_text_width': 'text-width',
            'fit_to_height': 'height',
        }

        if mode in mode_map:
            if fit_action is not None:
                fit_action.set_state(GLib.Variant('s', mode_map[mode]))
            # fit 模式下不亮任何数值项：把数值 state 设为不在档位中的哨兵值。
            if zoom_action is not None and zoom_level is not None:
                zoom_action.set_state(GLib.Variant('d', -1.0))
        else:
            if fit_action is not None:
                fit_action.set_state(GLib.Variant('s', 'none'))
            current = -1.0
            if zoom_level is not None:
                for level in levels:
                    if abs(zoom_level - level) < 1e-6:
                        current = level
                        break
            if zoom_action is not None:
                zoom_action.set_state(GLib.Variant('d', current))

    def on_zoom_clamped(self, zoom_manager, direction):
        if direction == 'in':
            message = _('Maximum zoom level reached')
        else:
            message = _('Minimum zoom level reached')
        main_window = ServiceLocator.get_main_window()
        if main_window is not None and hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(message)
            toast.set_timeout(2)
            main_window.toast_overlay.add_toast(toast)

    def update_label_debounced(self):
        '''滚动时 position_changed 高频触发（每帧）。页码标签只需秒级精度，
        用 150ms debounce 合并连续滚动事件，避免对 500 页 PDF 频繁调用
        get_n_pages / get_page_by_offset。'''
        if self._label_update_timeout_id is not None:
            GLib.source_remove(self._label_update_timeout_id)
        self._label_update_timeout_id = GLib.timeout_add(150, self._do_update_label)

    def _do_update_label(self):
        self._label_update_timeout_id = None
        self.update_label()
        return False

    def update_label(self):
        if self.document == None:
            self.view.page_spin.set_visible(False)
            self.view.paging_of_label.set_visible(False)
            self.view.stale_label.set_text('')
        else:
            preview = getattr(self.document, 'preview', None)
            if preview is None:
                # 工具链未挂接：与「无文档」同样显示占位。
                self.view.page_spin.set_visible(False)
                self.view.paging_of_label.set_visible(False)
                self.view.stale_label.set_text('')
                return
            if preview.pdf_is_stale:
                self.view.stale_label.set_text(_('Build failed — showing the previous PDF'))
            else:
                self.view.stale_label.set_text('')
            self.view.page_spin.set_visible(True)
            self.view.paging_of_label.set_visible(True)
            if preview.poppler_document != None:
                total = preview.poppler_document.get_n_pages()
                if preview.layout != None:
                    offset = preview.view.content.scrolling_offset_y
                    current = preview.layout.get_page_by_offset(offset)
                else:
                    current = 1
                self.view.page_spin.set_range(1, max(total, 1))
                self.view.page_spin.set_value(current)
                self.view.paging_of_label.set_text(_('of ') + str(total))
            else:
                self.view.paging_of_label.set_text(_('No preview'))

    def update_buttons(self):
        self.document = self.workspace.get_root_or_active_latex_document()
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        has_pdf = doc_preview is not None and doc_preview.poppler_document != None

        self.view.toolbar.set_visible(True)
        self.view.external_viewer_button.set_visible(True)
        self.view.recolor_pdf_toggle.set_visible(True)
        self.view.magnifier_toggle.set_visible(True)
        self.view.zoom_out_button.set_visible(True)
        self.view.fit_width_button.set_visible(True)
        self.view.zoom_level_button.set_visible(True)
        self.view.zoom_in_button.set_visible(True)
        # 弹出状态下隐藏 detach 按钮（preview_panel 已在独立窗口内，收回走窗口 X）。
        self.view.detach_button.set_visible(not self.workspace.is_preview_popped_out())
        # 同步隐藏帮助面板的 switch 按钮：popped_out 时无法切到 preview。
        self.main_window.help_panel.switch_button.set_visible(not self.workspace.is_preview_popped_out())

        self.view.external_viewer_button.set_sensitive(has_pdf)
        self.view.recolor_pdf_toggle.set_sensitive(has_pdf)
        self.view.magnifier_toggle.set_sensitive(has_pdf)
        self.view.zoom_out_button.set_sensitive(has_pdf)
        self.view.fit_width_button.set_sensitive(has_pdf)
        self.view.zoom_level_button.set_sensitive(has_pdf)
        self.view.zoom_in_button.set_sensitive(has_pdf)
        self.view.page_spin.set_sensitive(has_pdf)

        if has_pdf:
            zoom_level = doc_preview.zoom_manager.get_zoom_level()
            self.view.zoom_in_button.set_sensitive(zoom_level != None and zoom_level < 4)
            self.view.zoom_out_button.set_sensitive(zoom_level != None and zoom_level > 0.25)

    def update_zoom_level(self):
        doc_preview = getattr(self.document, 'preview', None) if self.document != None else None
        if doc_preview is None:
            return
        zoom_level = doc_preview.zoom_manager.get_zoom_level()

        if zoom_level != None:
            self.view.zoom_level_label.set_text('{0:.1f}%'.format(zoom_level * 100))

    def _on_page_spin_activate(self, spin_button):
        page_number = int(spin_button.get_value())
        preview = getattr(self.document, 'preview', None) if self.document != None else None
        if preview is None:
            return
        if preview.layout == None or preview.poppler_document == None:
            return
        total = preview.poppler_document.get_n_pages()
        page_number = max(1, min(page_number, total))
        content = preview.view.content
        # per-page：用 get_page_top 取第 N 页顶部（已含 vertical_padding）。
        y = preview.layout.get_page_top(page_number - 1)
        if y is None:
            return
        preview.scroll_to_position(content.scrolling_offset_x, y)

    def _on_fit_width_clicked(self, button):
        if self.document == None:
            return
        doc_preview = getattr(self.document, 'preview', None)
        if doc_preview is None:
            return
        doc_preview.zoom_manager.set_zoom_fit_to_width_auto_offset()

    def _sync_switch_icons(self):
        '''按当前显示的面板，把两个 switch 按钮的图标设为"目标面板"图标。
        预览模式 → 显示 Help 图标（点击去 Help）；Help 模式 → 显示 PDF 图标。'''
        visible_name = self.main_window.preview_help_stack.get_visible_child_name()
        if visible_name == 'preview':
            icon = 'help-browser-symbolic'
        else:
            icon = 'view-paged-symbolic'
        self.view.switch_button.get_child().set_from_icon_name(icon)
        self.main_window.help_panel.switch_button.get_child().set_from_icon_name(icon)

    def _on_switch_clicked(self, button):
        # 以 preview_help_stack 当前可见面板为唯一真相来源，决定切换到哪个、
        # 以及按钮图标应展示的目标面板。不依赖独立的 _is_preview 布尔，
        # 避免快捷键 / 状态恢复等其它切换路径导致布尔与实际显示失同步。
        visible_name = self.main_window.preview_help_stack.get_visible_child_name()
        if visible_name == 'preview':
            # 当前预览 → 切到 Help，按钮图标展示目标（Help）
            self.view.switch_button.get_child().set_from_icon_name('help-browser-symbolic')
            self.main_window.help_panel.switch_button.get_child().set_from_icon_name('help-browser-symbolic')
            self.workspace.set_show_preview_or_help(False, True)
        else:
            # 当前 Help → 切到预览，按钮图标展示目标（PDF）
            self.view.switch_button.get_child().set_from_icon_name('view-paged-symbolic')
            self.main_window.help_panel.switch_button.get_child().set_from_icon_name('view-paged-symbolic')
            self.workspace.set_show_preview_or_help(True, False)

    def _attach_target_bar(self, preview_view):
        revealer = preview_view.target_label_revealer
        parent = revealer.get_parent()
        if parent is not None:
            parent.remove(revealer)
        self.view.target_bar_placeholder.append(revealer)

    def _detach_target_bar(self):
        placeholder = self.view.target_bar_placeholder
        child = placeholder.get_first_child()
        if child is not None:
            placeholder.remove(child)

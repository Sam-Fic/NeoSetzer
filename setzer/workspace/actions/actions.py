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

import os
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, Gio, Gtk, Gdk, Pango

from setzer.app.service_locator import ServiceLocator
from setzer.dialogs.dialog_locator import DialogLocator
from setzer.app.font_manager import FontManager
from setzer.popovers.popover_manager import PopoverManager
from setzer.settings.document_settings import DocumentSettings


class Actions(object):

    def __init__(self, workspace):
        self.workspace = workspace
        self.main_window = ServiceLocator.get_main_window()
        self.settings = ServiceLocator.get_settings()

        self.actions = dict()
        # 最近关闭的已保存文档 filename 栈，供 Ctrl+Shift+T 重开（仅保留最多 5 个）。
        self._closed_document_stack = []
        # idle 去抖 id：update_actions 被多路信号频繁触发（modified_changed、
        # can_sync_changed、notify::can-undo、notify::has-selection、
        # adjustment changed 等），单次按键可能连续触发若干次。去抖后合并为
        # 一次实际刷新，避免重复 set_enabled 调用。
        self._update_actions_idle_id = None
        self.add_action('new-latex-document', self.new_latex_document)
        self.add_action('new-bibtex-document', self.new_bibtex_document)
        self.add_action('open-document-dialog', self.open_document_dialog)
        self.add_action('open-recent-documents', self.open_recent_documents)
        self.add_action('build', self.build)
        self.add_action('save-and-build', self.save_and_build)
        self.add_action('show-build-log', self.show_build_log)
        self.add_action('close-build-log', self.close_build_log)
        self.add_action('save', self.save)
        self.add_action('save-as', self.save_as)
        self.add_action('save-all', self.save_all)
        self.add_action('save-session', self.save_session)
        self.add_action('export-pdf-as', self.export_pdf_as)
        self.add_action('print', self.print_document)
        self.add_action('close-all-documents', self.close_all)
        self.add_action('close-active-document', self.close_active_document)
        self.add_action('reopen-last-closed-document', self.reopen_last_closed_document)
        self.add_action('go-to-line', self.go_to_line)
        self.add_action('toggle-bookmark', self.toggle_bookmark)
        self.add_action('next-bookmark', self.next_bookmark)
        self.add_action('previous-bookmark', self.previous_bookmark)
        self.add_action('clear-bookmarks', self.clear_bookmarks)
        self.add_action('duplicate-line', self.duplicate_line)
        self.add_action('delete-line', self.delete_line)
        self.add_action('move-line-up', self.move_line_up)
        self.add_action('move-line-down', self.move_line_down)
        self.add_action('indent', self.indent)
        self.add_action('outdent', self.outdent)

        self.add_action('show-document-wizard', self.start_wizard, None)
        self.add_action('insert-before-after', self.insert_before_after, GLib.VariantType('as'))
        self.add_action('insert-symbol', self.insert_symbol, GLib.VariantType('as'))
        self.add_action('insert-after-packages', self.insert_after_packages, GLib.VariantType('as'))
        self.add_action('insert-before-document-end', self.insert_before_document_end, GLib.VariantType('as'))
        self.add_action('add-packages', self.add_packages, GLib.VariantType('as'))
        self.add_action('include-bibtex-file', self.start_include_bibtex_file_dialog, None)
        self.add_action('include-latex-file', self.start_include_latex_file_dialog, None)
        self.add_action('add-remove-packages-dialog', self.start_add_remove_packages_dialog, None)
        self.add_action('insert-image-dialog', self.start_insert_image_dialog, None)
        self.add_action('toggle-comment', self.toggle_comment)
        self.add_action('fold-all', self.fold_all)
        self.add_action('unfold-all', self.unfold_all)
        self.add_action('forward-sync', self.forward_sync)

        # Label context menu actions (right-click on \ref{...})
        self.add_action('jump-to-definition', self.jump_to_definition, GLib.VariantType('s'))
        self.add_action('copy-ref', self.copy_ref_label, GLib.VariantType('s'))
        self.add_action('copy-pageref', self.copy_pageref_label, GLib.VariantType('s'))
        self.add_action('copy-autoref', self.copy_autoref_label, GLib.VariantType('s'))
        self.add_action('find-all-refs', self.find_all_refs, GLib.VariantType('s'))

        self.add_action('start-search', self.start_search)
        self.add_action('start-search-and-replace', self.start_search_and_replace)
        self.add_action('find-next', self.find_next)
        self.add_action('find-previous', self.find_previous)
        self.add_action('stop-search', self.stop_search)

        # Multi-cursor actions
        self.add_action('select-next-occurrence', self.select_next_occurrence)
        self.add_action('select-all-occurrences', self.select_all_occurrences)
        self.add_action('add-cursor-above', self.add_cursor_above)
        self.add_action('add-cursor-below', self.add_cursor_below)
        self.add_action('clear-multi-cursor', self.clear_multi_cursor)

        self.add_action('cut', self.cut)
        self.add_action('copy', self.copy)
        self.add_action('paste', self.paste)
        self.add_action('delete-selection', self.delete_selection)
        self.add_action('select-all', self.select_all)
        self.add_action('undo', self.undo)
        self.add_action('redo', self.redo)

        self.add_action('zoom-in', self.zoom_in)
        self.add_action('zoom-out', self.zoom_out)
        self.add_action('reset-zoom', self.reset_zoom)

        # 有状态 action：state 为当前 fit 模式（none / width / text-width /
        # height）。菜单里三个 fit 项以 target 设各模式，GTK 自动在激活项前绘制
        # 对钩，与数值缩放的 preview-set-zoom-level 并列、互斥。
        preview_fit_mode_action = Gio.SimpleAction.new_stateful(
            'preview-fit-mode', GLib.VariantType('s'), GLib.Variant('s', 'none'))
        preview_fit_mode_action.connect('activate', self.preview_set_fit_mode)
        self.main_window.add_action(preview_fit_mode_action)
        self.actions['preview-fit-mode'] = preview_fit_mode_action
        # 有状态 action：state 为当前缩放级别（double）。菜单项以 target 设置各级
        # 别，GTK 自动在 target 与 state 匹配的项前绘制对钩（radio/check 指示符），
        # 与 set-build-interpreter 的实现一致——这是标准做法。
        preview_set_zoom_level_action = Gio.SimpleAction.new_stateful(
            'preview-set-zoom-level', GLib.VariantType('d'), GLib.Variant('d', 1.0))
        preview_set_zoom_level_action.connect('activate', self.preview_set_zoom_level)
        self.main_window.add_action(preview_set_zoom_level_action)
        self.actions['preview-set-zoom-level'] = preview_set_zoom_level_action

        # Preview context menu actions
        self.add_action('preview-rotate-cw', self.preview_rotate_cw)
        self.add_action('preview-rotate-ccw', self.preview_rotate_ccw)
        self.add_action('preview-open-link', self.preview_open_link)
        self.add_action('preview-copy-link', self.preview_copy_link)
        self.add_action('preview-copy-text', self.preview_copy_text, GLib.VariantType('i'))
        self.add_action('preview-copy-image', self.preview_copy_image, GLib.VariantType('i'))
        self.add_action('preview-save-image', self.preview_save_image, GLib.VariantType('i'))
        self.add_action('preview-search-pdf', self.preview_search_pdf)
        self.add_action('preview-show-source', self.preview_show_source)
        self.add_action('preview-zoom-in', self.preview_zoom_in)
        self.add_action('preview-zoom-out', self.preview_zoom_out)
        self.add_action('preview-print', self.preview_print_pdf)

        # Stateful recolor action: boolean state drives the checkmark in menu.
        recolor_action = Gio.SimpleAction.new_stateful(
            'preview-recolor', None, GLib.Variant.new_boolean(False))
        recolor_action.connect('activate', self.preview_toggle_recolor)
        self.main_window.add_action(recolor_action)
        self.actions['preview-recolor'] = recolor_action

        self.add_action('show-preferences-dialog', self.show_preferences_dialog)
        self.add_action('show-document-properties', self.show_document_properties)
        self.add_action('show-shortcuts-dialog', self.show_shortcuts_dialog)
        self.add_action('show-about-dialog', self.show_about_dialog)
        self.add_action('show-context-menu', self.show_context_menu)
        # toggle-fullscreen 使用 PropertyAction 绑定 MainWindow.fullscreened 属性，
        # 无需手工 set_state / 回调——GTK 自动翻转布尔属性并同步 UI。
        fullscreen_action = Gio.PropertyAction.new('toggle-fullscreen', self.main_window, 'fullscreened')
        self.main_window.add_action(fullscreen_action)
        self.actions['toggle-fullscreen'] = fullscreen_action

        # 每文档 LaTeX 解释器覆盖（优先于全局 preferences['latex_interpreter']）。
        # 用 stateful action（字符串状态）使菜单项自动以勾选态反映当前选中值。
        build_interpreter_action = Gio.SimpleAction.new_stateful(
            'set-build-interpreter', GLib.VariantType.new('s'), GLib.Variant('s', 'default'))
        build_interpreter_action.connect('activate', self.on_set_build_interpreter)
        self.main_window.add_action(build_interpreter_action)
        self.actions['set-build-interpreter'] = build_interpreter_action

        self.actions['quit'] = Gio.SimpleAction.new('quit', None)
        self.main_window.add_action(self.actions['quit'])

        self.workspace.connect('new_document', self.on_new_document)
        self.workspace.connect('document_removed', self.on_document_removed)
        self.workspace.connect('new_inactive_document', self.on_new_inactive_document)
        self.workspace.connect('new_active_document', self.on_new_active_document)

        self.update_actions()

    def add_action(self, name, callback, parameter=None):
        self.actions[name] = Gio.SimpleAction.new(name, parameter)
        self.main_window.add_action(self.actions[name])
        self.actions[name].connect('activate', callback)

    def on_new_document(self, workspace, document):
        if document.is_latex_document():
            document.build_system.connect('can_sync_changed', self.on_can_sync_changed)
        self.update_actions()

    def on_document_removed(self, workspace, document):
        if document.is_latex_document():
            document.build_system.disconnect('can_sync_changed', self.on_can_sync_changed)
        self.update_actions()

    def on_new_inactive_document(self, workspace, document):
        document.disconnect('modified_changed', self.on_modified_changed)
        document.source_buffer.disconnect_by_func(self.on_undo_changed)
        document.source_buffer.disconnect_by_func(self.on_has_selection_changed)
        document.view.scrolled_window.get_vadjustment().disconnect_by_func(self.on_adjustment_changed)

    def on_new_active_document(self, workspace, document):
        self.update_actions()
        document.connect('modified_changed', self.on_modified_changed)
        document.source_buffer.connect('notify::can-undo', self.on_undo_changed)
        document.source_buffer.connect('notify::has-selection', self.on_has_selection_changed)
        document.view.scrolled_window.get_vadjustment().connect('changed', self.on_adjustment_changed)

    def on_modified_changed(self, document):
        self.update_actions()

    def on_can_sync_changed(self, document, can_sync):
        self.update_actions()

    def on_undo_changed(self, buffer, can_undo):
        self.update_actions()

    def on_redo_changed(self, buffer, can_redo):
        self.update_actions()

    def on_has_selection_changed(self, buffer, has_selection):
        self.update_actions()

    def on_adjustment_changed(self, adjustment):
        self.update_actions()

    def update_actions(self):
        # 去抖入口：仅调度一次 idle，实际刷新在 _update_actions_now 中进行。
        # 多路信号在一个事件循环周期内连续触发时，只跑一次实际刷新。
        if self._update_actions_idle_id is None:
            self._update_actions_idle_id = GLib.idle_add(self._update_actions_idle)

    def _update_actions_idle(self):
        self._update_actions_idle_id = None
        self._update_actions_now()
        return False

    def _update_actions_now(self):
        document = self.workspace.get_active_document()
        document_active = document != None
        document_active_is_latex = document_active and document.is_latex_document()
        enable_save = document_active and (document.source_buffer.get_modified() or document.get_filename() == None)
        has_selection = document_active and document.source_buffer.get_has_selection()
        if self.workspace.root_document != None: sync_document = self.workspace.root_document
        else: sync_document = document
        can_sync = sync_document != None and sync_document.is_latex_document() and sync_document.build_system.can_sync
        can_build = (self.workspace.get_root_or_active_latex_document() != None)
        can_reset_zoom = (round(FontManager.zoom_level * 100) != 100)
        # get_font_desc 内部 Pango.FontDescription.from_string(font_string) 每次构造
        # 新对象，原调两次（zoom_in / zoom_out 各一）。取一次 size 复用。
        font_size = FontManager.get_font_desc().get_size()
        can_zoom_in = (font_size * 1.1 <= 24 * Pango.SCALE)
        can_zoom_out = (font_size / 1.1 >= 6 * Pango.SCALE)

        self.actions['close-active-document'].set_enabled(document_active)
        self.actions['close-all-documents'].set_enabled(document_active)
        self.actions['reopen-last-closed-document'].set_enabled(len(self._closed_document_stack) > 0)
        self.actions['save-session'].set_enabled(document_active)
        self.actions['save'].set_enabled(enable_save)
        self.actions['save-as'].set_enabled(document_active)
        self.actions['print'].set_enabled(document_active)
        # Export PDF As…：仅当已 build 出 PDF（preview.pdf_filename 已就绪）时启用。
        pdf_document = self.workspace.get_root_or_active_latex_document()
        has_pdf = pdf_document is not None and pdf_document.preview.pdf_filename is not None
        self.actions['export-pdf-as'].set_enabled(has_pdf)
        self.actions['save-all'].set_enabled(len(self.workspace.get_unsaved_documents()) > 0)
        self.actions['add-remove-packages-dialog'].set_enabled(document_active_is_latex)
        self.actions['redo'].set_enabled(document_active and document.source_buffer.get_can_redo())
        self.actions['undo'].set_enabled(document_active and document.source_buffer.get_can_undo())
        self.actions['cut'].set_enabled(has_selection)
        self.actions['copy'].set_enabled(has_selection)
        self.actions['delete-selection'].set_enabled(has_selection)
        self.actions['start-search'].set_enabled(document_active)
        self.actions['start-search-and-replace'].set_enabled(document_active)
        self.actions['find-next'].set_enabled(document_active)
        self.actions['find-previous'].set_enabled(document_active)
        self.actions['insert-before-after'].set_enabled(document_active_is_latex)
        self.actions['insert-symbol'].set_enabled(document_active_is_latex)
        self.actions['insert-before-document-end'].set_enabled(document_active_is_latex)
        self.actions['insert-after-packages'].set_enabled(document_active_is_latex)
        self.actions['add-packages'].set_enabled(document_active_is_latex)
        self.actions['show-document-wizard'].set_enabled(document_active_is_latex)
        self.actions['include-bibtex-file'].set_enabled(document_active_is_latex)
        self.actions['include-latex-file'].set_enabled(document_active_is_latex)
        self.actions['add-remove-packages-dialog'].set_enabled(document_active_is_latex)
        self.actions['toggle-comment'].set_enabled(document_active_is_latex)
        # 通用编辑功能（跳行、复制行、上下移行）对任何活动文档启用——
        # 原实现限定为 LaTeX 文档，BibTeX 与 cls/sty 文档用户无法使用这些
        # 基本编辑功能。LaTeX 专属动作（insert-*、add-packages、wizard 等）
        # 仍保持 document_active_is_latex 限制。
        self.actions['go-to-line'].set_enabled(document_active)
        self.actions['toggle-bookmark'].set_enabled(document_active)
        self.actions['next-bookmark'].set_enabled(document_active)
        self.actions['previous-bookmark'].set_enabled(document_active)
        self.actions['clear-bookmarks'].set_enabled(document_active)
        self.actions['duplicate-line'].set_enabled(document_active)
        self.actions['move-line-up'].set_enabled(document_active)
        self.actions['move-line-down'].set_enabled(document_active)
        self.actions['indent'].set_enabled(document_active)
        self.actions['outdent'].set_enabled(document_active)
        self.actions['forward-sync'].set_enabled(can_sync)
        self.actions['build'].set_enabled(can_build)
        self.actions['save-and-build'].set_enabled(can_build)
        self.actions['show-build-log'].set_enabled(document_active_is_latex)
        self.actions['close-build-log'].set_enabled(document_active_is_latex)
        self.actions['reset-zoom'].set_enabled(can_reset_zoom)
        self.actions['zoom-in'].set_enabled(can_zoom_in)
        self.actions['zoom-out'].set_enabled(can_zoom_out)

        # Preview context menu: sync recolor state and enable/disable.
        preview = getattr(document, 'preview', None) if document_active else None
        has_pdf = document_active and preview is not None and preview.pdf_filename is not None
        self.actions['preview-rotate-cw'].set_enabled(has_pdf)
        self.actions['preview-rotate-ccw'].set_enabled(has_pdf)
        self.actions['preview-search-pdf'].set_enabled(has_pdf)
        self.actions['preview-show-source'].set_enabled(document_active)
        self.actions['preview-copy-text'].set_enabled(has_pdf)
        self.actions['preview-copy-image'].set_enabled(has_pdf)
        self.actions['preview-save-image'].set_enabled(has_pdf)
        self.actions['preview-open-link'].set_enabled(has_pdf)
        self.actions['preview-copy-link'].set_enabled(has_pdf)
        if preview is not None:
            self.actions['preview-recolor'].set_state(
                GLib.Variant.new_boolean(preview.recolor_pdf))
        self.actions['preview-recolor'].set_enabled(has_pdf)
        self.actions['preview-zoom-in'].set_enabled(has_pdf)
        self.actions['preview-zoom-out'].set_enabled(has_pdf)
        self.actions['preview-print'].set_enabled(has_pdf)

        # 每文档 LaTeX 解释器覆盖：活动文档为 LaTeX 时启用并反映当前选择。
        if document_active_is_latex:
            current = document.build_system.latex_interpreter or 'default'
            self.actions['set-build-interpreter'].set_state(GLib.Variant('s', current))
            self.actions['set-build-interpreter'].set_enabled(True)
        else:
            self.actions['set-build-interpreter'].set_enabled(False)

    def on_set_build_interpreter(self, action, parameter):
        value = parameter.get_string()
        document = self.workspace.get_active_document()
        if document is None or not document.is_latex_document():
            return
        document.build_system.set_latex_interpreter(None if value == 'default' else value)
        # 持久化到该文档状态文件，崩溃/重启后保留。
        DocumentSettings.save_document_state(document)
        action.set_state(parameter)

    def new_latex_document(self, action=None, parameter=None):
        main_window = ServiceLocator.get_main_window()
        main_window.show_loading_spinner()
        # 延迟 200ms 再创建文档，让 spinner 先渲染并动画几帧，避免立即卡顿
        GLib.timeout_add(200, self._do_new_latex_document)

    def _do_new_latex_document(self):
        document = self.workspace.create_latex_document()
        self.workspace.add_document(document)
        self.workspace.set_active_document(document)
        return False

    def new_bibtex_document(self, action=None, parameter=None):
        main_window = ServiceLocator.get_main_window()
        main_window.show_loading_spinner()
        GLib.timeout_add(200, self._do_new_bibtex_document)

    def _do_new_bibtex_document(self):
        document = self.workspace.create_bibtex_document()
        self.workspace.add_document(document)
        self.workspace.set_active_document(document)
        return False

    def open_document_dialog(self, action=None, parameter=None):
        DialogLocator.get_dialog('open_document').run()

    def open_recent_documents(self, action=None, parameter=None):
        PopoverManager.get_popover('open_document').open()

    def save_and_build(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_root_or_active_latex_document()
        active_document = ServiceLocator.get_workspace().get_active_document()
        if document == None or active_document == None: return

        if document.filename == None:
            DialogLocator.get_dialog('build_save').run(document)
        else:
            self.save()
            # 手动构建已覆盖 pending auto-build 倒计时的目的，取消尚未 fire
            # 的倒计时，避免「用户手动编译后倒计时到又自动编一次」。
            self.workspace.auto_build.cancel_pending_for_target(document)
            # 手动构建：确保 is_auto_build = False，使 build_log 始终遵循
            # autoshow_build_log 设置弹出日志（不受 auto_build_autoshow_errors 影响）。
            document.build_system.is_auto_build = False
            document.build_system.build_and_forward_sync(active_document)

    def build(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_root_or_active_latex_document()
        active_document = ServiceLocator.get_workspace().get_active_document()
        if document == None or active_document == None: return

        if document.filename == None:
            DialogLocator.get_dialog('build_save').run(document)
        else:
            # 手动构建已覆盖 pending auto-build 倒计时的目的，取消尚未 fire
            # 的倒计时，避免「用户手动编译后倒计时到又自动编一次」。
            self.workspace.auto_build.cancel_pending_for_target(document)
            # 手动构建：确保 is_auto_build = False，使 build_log 始终遵循
            # autoshow_build_log 设置弹出日志（不受 auto_build_autoshow_errors 影响）。
            document.build_system.is_auto_build = False
            document.build_system.build_and_forward_sync(active_document)

    def forward_sync(self, action=None, parameter=''):
        active_document = self.workspace.get_active_document()
        if active_document == None: return

        if self.workspace.root_document != None: sync_document = self.workspace.root_document
        else: sync_document = active_document
        if not sync_document.is_latex_document(): return
        if not sync_document.build_system.can_sync: return

        sync_document.build_system.forward_sync(active_document)

    def show_build_log(self, action=None, parameter=''):
        self.workspace.set_show_build_log(True)

    def close_build_log(self, action=None, parameter=''):
        self.workspace.set_show_build_log(False)

    def save(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        active_document = self.workspace.get_active_document()
        if active_document.filename == None:
            self.save_as()
        else:
            active_document.save_to_disk()

    def save_as(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('save_document').run(document)

    def export_pdf_as(self, action=None, parameter=None):
        '''把 build 生成的 PDF 另存到其他位置（副本，不改源文件）。'''
        if self.workspace.get_active_document() == None: return

        # 导出的是 build 产物（root 文档的 PDF），而非当前活动文档本体。
        document = self.workspace.get_root_or_active_latex_document()
        if document is None or document.preview.pdf_filename is None:
            return

        DialogLocator.get_dialog('export_pdf').run(document)

    def print_document(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        from setzer.dialogs.print.print import PrintDialog
        document = self.workspace.get_active_document()
        PrintDialog().run(document)

    def save_all(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        active_document = self.workspace.get_active_document()
        return_to_active_document = False
        documents = self.workspace.get_unsaved_documents()
        if len(documents) > 0:
            for document in documents:
                if document.get_filename() == None:
                    self.workspace.set_active_document(document)
                    return_to_active_document = True
                    DialogLocator.get_dialog('save_document').run(document)
                else:
                    document.save_to_disk()
            if return_to_active_document == True:
                # 恢复到「保存全部」开始前的活动文档，而非循环里最后一个文档。
                # 原代码 set_active_document(document) 中 document 是 for 循环
                # 的最后一次迭代值——若未保存列表为 [B, C] 而原活动文档是 A，
                # 且 B 需要另存为对话框，则保存完成后焦点会落在 C（或 B）上，
                # 而非用户原本所在的 A。
                self.workspace.set_active_document(active_document)

    def save_session(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        DialogLocator.get_dialog('save_session').run()

    def close_all(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        unsaved_documents = self.workspace.get_unsaved_documents()
        if len(unsaved_documents) > 0:
            self.workspace.set_active_document(unsaved_documents[0])
            dialog = DialogLocator.get_dialog('close_confirmation')
            # 传 'documents' 字段：≥2 个未保存文档时弹批量对话框（用户选择
            # "多文档路径用批量"）。单文档时仍走原单文档对话框。
            dialog.run({'unsaved_document': unsaved_documents[0], 'documents': unsaved_documents}, self.close_all_callback)
        else:
            documents = self.workspace.get_all_documents()
            for document in documents:
                self.workspace.remove_document(document)

    def close_all_callback(self, parameters):
        document = parameters['unsaved_document']
        unsaved_documents = parameters['documents']
        response = parameters['response']

        if response == 0:  # discard (单)：移除当前，继续 close_all
            self.workspace.remove_document(document)
            self.close_all()
        elif response == 4:  # discard_all (批量)：移除所有未保存，继续 close_all
            for d in list(unsaved_documents):
                self.workspace.remove_document(d)
            self.close_all()
        elif response == 2:  # save (单)
            if document.get_filename() == None:
                DialogLocator.get_dialog('save_document').run(document, self.close_all)
            else:
                if document.save_to_disk():
                    self.close_all()
                # 保存失败：不继续关闭，toast 已弹出
        elif response == 3:  # save_all (批量)
            # 保存所有有 filename 的；无 filename 的逐个弹 save_document。
            # save_all_processed 记录本次 save_all 流程已提示过的 untitled 文档，
            # 避免用户取消保存时同一文档被重复提示（无限循环）。
            for d in list(unsaved_documents):
                if d.get_filename() is not None:
                    d.save_to_disk()  # 单个失败不中断批量保存，toast 各自弹出
            parameters['save_all_processed'] = set()
            untitled = [d for d in unsaved_documents if d.get_filename() is None]
            if untitled:
                first = untitled[0]
                parameters['save_all_processed'].add(id(first))
                self.workspace.set_active_document(first)
                parameters['unsaved_document'] = first
                DialogLocator.get_dialog('save_document').run(first, self.close_all_save_all_cb, parameters)
            else:
                self.close_all()

    def close_all_save_all_cb(self, parameters):
        '''save_all 模式下逐个 untitled 文档的 save_document 回调。

        用户可能取消保存（doc 仍 untitled+modified）；用 save_all_processed
        集合跳过已提示过的文档，继续下一个。全部提示完后回到 close_all 重新
        评估——若仍有未保存文档，再次弹批量对话框，用户可选 Discard All 终止。'''
        unsaved_documents = parameters['documents']
        processed = parameters.get('save_all_processed', set())
        untitled = [d for d in unsaved_documents
                    if d.get_filename() is None and id(d) not in processed]
        if untitled:
            next_doc = untitled[0]
            processed.add(id(next_doc))
            self.workspace.set_active_document(next_doc)
            parameters['unsaved_document'] = next_doc
            DialogLocator.get_dialog('save_document').run(next_doc, self.close_all_save_all_cb, parameters)
        else:
            # 所有 untitled 已提示过（保存或取消）。回到 close_all 重新评估。
            self.close_all()

    def push_closed_document(self, filename):
        '''Push a filename onto the closed-document stack for Ctrl+Shift+T reopen.

        Only documents with a filename that still exists on disk are tracked—
        unsaved (unnamed) documents cannot be safely reopened. The stack is
        capped at 5 entries; the oldest is evicted when full. If the filename
        is already in the stack it is moved to the top (most-recently-closed).
        '''
        if filename is None or not os.path.isfile(filename):
            return
        if filename in self._closed_document_stack:
            self._closed_document_stack.remove(filename)
        self._closed_document_stack.append(filename)
        if len(self._closed_document_stack) > 5:
            self._closed_document_stack.pop(0)
        # 通知 welcome screen 等 observers 刷新「最近关闭」列表
        self.workspace.add_change_code('update_closed_documents', self._closed_document_stack)

    def get_closed_document_stack(self):
        '''返回最近关闭文档栈的副本，most-recently-closed 在前（用于 UI 展示）。

        原栈用 list 末尾作栈顶（pop() 取最新），UI 展示需倒序。
        '''
        return list(reversed(self._closed_document_stack))

    def reopen_closed_document(self, filename):
        '''从栈中移除指定 filename 并打开它（用于 welcome screen 点击重开）。

        与 reopen_last_closed_document 不同，本方法可重开栈中任意一项
        （不限于栈顶）。文件已删除时从栈中移除但不打开。
        '''
        if filename in self._closed_document_stack:
            self._closed_document_stack.remove(filename)
        if not os.path.isfile(filename):
            self.workspace.add_change_code('update_closed_documents', self._closed_document_stack)
            return
        self.workspace.open_document_by_filename_with_spinner(filename)
        self.workspace.add_change_code('update_closed_documents', self._closed_document_stack)

    def close_active_document(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        # 仅当文档已保存（有 filename 且磁盘文件仍存在）才压入重开栈，
        # 未保存文档无法安全重开。
        self.push_closed_document(document.get_filename())
        if document.source_buffer.get_modified():
            dialog = DialogLocator.get_dialog('close_confirmation')
            dialog.run({'unsaved_document': document}, self.close_document_callback)
        else:
            self.workspace.remove_document(document)

    def reopen_last_closed_document(self, action=None, parameter=None):
        if len(self._closed_document_stack) == 0: return

        filename = self._closed_document_stack.pop()
        # 文件可能已被删除，丢弃无效项并继续尝试栈中更早的。
        while filename != None and not os.path.isfile(filename):
            if len(self._closed_document_stack) == 0:
                filename = None
                break
            filename = self._closed_document_stack.pop()
        if filename == None:
            self.update_actions()
            self.workspace.add_change_code('update_closed_documents', self._closed_document_stack)
            return

        self.workspace.open_document_by_filename_with_spinner(filename)
        self.workspace.add_change_code('update_closed_documents', self._closed_document_stack)

    def go_to_line(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document == None: return

        line_count = document.source_buffer.get_line_count()
        current_line = document.source_buffer.get_iter_at_offset(
            document.source_buffer.get_property('cursor-position')).get_line() + 1
        dialog = DialogLocator.get_dialog('go_to_line')
        dialog.run(line_count, self.go_to_line_callback, current_line)

    def go_to_line_callback(self, line):
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        line_count = buffer.get_line_count()
        if line < 1 or line > line_count: return

        insert_iter = buffer.get_iter_at_line(line - 1)[1]
        buffer.place_cursor(insert_iter)
        document.scroll_cursor_onscreen()
        document.view.source_view.grab_focus()

    def toggle_bookmark(self, action=None, parameter=None):
        """Toggle a bookmark on the current line."""
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())
        current_line = cursor_iter.get_line()
        document.bookmarks.toggle_bookmark(current_line)

    def next_bookmark(self, action=None, parameter=None):
        """Navigate to the next bookmark."""
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())
        current_line = cursor_iter.get_line()
        next_line = document.bookmarks.get_next_bookmark_line(current_line)
        if next_line is not None:
            buffer.place_cursor(buffer.get_iter_at_line(next_line)[1])
            document.scroll_cursor_onscreen(margin_lines=0)

    def previous_bookmark(self, action=None, parameter=None):
        """Navigate to the previous bookmark."""
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        cursor_iter = buffer.get_iter_at_mark(buffer.get_insert())
        current_line = cursor_iter.get_line()
        prev_line = document.bookmarks.get_previous_bookmark_line(current_line)
        if prev_line is not None:
            buffer.place_cursor(buffer.get_iter_at_line(prev_line)[1])
            document.scroll_cursor_onscreen()

    def clear_bookmarks(self, action=None, parameter=None):
        """Clear all bookmarks in the active document."""
        document = self.workspace.get_active_document()
        if document == None: return

        document.bookmarks.clear_bookmarks()

    def duplicate_line(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        has_selection = buffer.get_has_selection()
        if has_selection:
            start, end = buffer.get_selection_bounds()
            first_line = start.get_line()
            # 选区跨到 end 所在行；若 end 在行首(偏移0)则不计入该行。
            last_line = end.get_line() if end.get_line_offset() > 0 else max(end.get_line() - 1, first_line)
        else:
            first_line = last_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()

        buffer.begin_user_action()
        # 自底向上复制，避免行号偏移。
        for line_number in range(last_line, first_line - 1, -1):
            found, line_start = buffer.get_iter_at_line(line_number)
            line_end = line_start.copy()
            if line_end.ends_line():
                line_end.forward_char()
            elif line_number == buffer.get_line_count() - 1:
                # 末行无换行符：先补一个换行，再复制行内容。
                buffer.insert(line_end, '\n')
                line_end = buffer.get_iter_at_line(line_number)[1]
                line_end.forward_char()
            line_text = buffer.get_slice(line_start, line_end, False)
            buffer.insert(line_end, line_text)
        buffer.end_user_action()
        document.scroll_cursor_onscreen()

    def delete_line(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        has_selection = buffer.get_has_selection()
        if has_selection:
            start, end = buffer.get_selection_bounds()
            first_line = start.get_line()
            last_line = end.get_line() if end.get_line_offset() > 0 else max(end.get_line() - 1, first_line)
        else:
            first_line = last_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()

        buffer.begin_user_action()
        # 自底向上删除，避免行号偏移。
        for line_number in range(last_line, first_line - 1, -1):
            found, line_start = buffer.get_iter_at_line(line_number)
            line_end = line_start.copy()
            if line_end.ends_line():
                line_end.forward_char()
            elif line_number == buffer.get_line_count() - 1:
                if line_start.get_offset() > 0:
                    line_start.backward_char()
            buffer.delete(line_start, line_end)
        buffer.end_user_action()

    def move_line_up(self, action=None, parameter=None):
        self._move_line(-1)

    def move_line_down(self, action=None, parameter=None):
        self._move_line(1)

    def _move_line(self, direction):
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        line_count = buffer.get_line_count()
        has_selection = buffer.get_has_selection()
        if has_selection:
            start, end = buffer.get_selection_bounds()
            first_line = start.get_line()
            last_line = end.get_line() if end.get_line_offset() > 0 else max(end.get_line() - 1, first_line)
        else:
            first_line = last_line = buffer.get_iter_at_mark(buffer.get_insert()).get_line()

        if direction < 0 and first_line == 0: return
        if direction > 0 and last_line == line_count - 1: return

        buffer.begin_user_action()
        if direction < 0:
            # 与上一行交换：取 first_line-1 行块，移到 first_line..last_line 之后。
            upper_start = buffer.get_iter_at_line(first_line - 1)[1]
            upper_end = buffer.get_iter_at_line(first_line)[1]
            block = buffer.get_slice(upper_start, upper_end, False)
            buffer.delete(upper_start, upper_end)
            lower_start = buffer.get_iter_at_line(first_line)[1]
            lower_end = buffer.get_iter_at_line(last_line + 1)[1]
            lower_end = lower_end if lower_end.get_line_offset() == 0 or lower_end.ends_line() else lower_end
            # 在块尾（last_line 行末）后插入原上一行内容。
            insert_at = buffer.get_iter_at_line(last_line)[1]
            if not insert_at.ends_line():
                insert_at.forward_to_line_end()
            insert_at.forward_char()
            buffer.insert(insert_at, block)
        else:
            # 与下一行交换：取 last_line+1 行块，移到 first_line 之前。
            lower_start = buffer.get_iter_at_line(last_line + 1)[1]
            lower_end = buffer.get_iter_at_line(last_line + 2)[1] if last_line + 2 <= line_count - 1 else buffer.get_end_iter()
            if last_line + 1 == line_count - 1:
                lower_end = buffer.get_end_iter()
            block = buffer.get_slice(lower_start, lower_end, False)
            buffer.delete(lower_start, lower_end)
            upper_start = buffer.get_iter_at_line(first_line)[1]
            buffer.insert(upper_start, block)
        buffer.end_user_action()
        document.scroll_cursor_onscreen()

    def indent(self, action=None, parameter=None):
        self._indent_selection(outdent=False)

    def outdent(self, action=None, parameter=None):
        self._indent_selection(outdent=True)

    def _indent_selection(self, outdent=False):
        '''缩进 / 取消缩进当前行或选区（右键菜单项入口）。

        无选区时先选中光标所在整行，再交给 DocumentController.indent_selection
        处理——与 Tab / Shift+Tab 的「有选区才缩进」行为互补：菜单项始终可作用于
        当前行，不必先手动框选。'''
        document = self.workspace.get_active_document()
        if document == None: return

        buffer = document.source_buffer
        if not buffer.get_has_selection():
            insert = buffer.get_iter_at_mark(buffer.get_insert())
            line_number = insert.get_line()
            line_start = buffer.get_iter_at_line(line_number)[1]
            if line_number == buffer.get_line_count() - 1:
                # 末行无换行符：选中到行末；否则选中到下一行行首以免吞掉换行。
                line_end = buffer.get_end_iter()
            else:
                line_end = buffer.get_iter_at_line(line_number + 1)[1]
            buffer.select_range(line_start, line_end)

        document.controller.indent_selection(outdent=outdent)
        document.scroll_cursor_onscreen()


    def close_document_callback(self, parameters):
        if parameters['response'] == 0:
            self.workspace.remove_document(parameters['unsaved_document'])
        elif parameters['response'] == 2:
            document = parameters['unsaved_document']
            if document.get_filename() == None:
                DialogLocator.get_dialog('save_document').run(document)
            else:
                if document.save_to_disk():
                    self.workspace.remove_document(parameters['unsaved_document'])
                # 保存失败：不移除文档，toast 已弹出

    def start_wizard(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('document_wizard').run(document)

    def insert_before_after(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return
        if parameter == None: return

        document = self.workspace.get_active_document()
        document.source_buffer.begin_user_action()

        before, after = parameter[0], parameter[1]
        bounds = document.source_buffer.get_selection_bounds()
        if len(bounds) > 1:
            text = before + document.source_buffer.get_text(*bounds, False) + after
            text = document.replace_tabs_with_spaces_if_set(text)

            document.source_buffer.delete_selection(False, False)

            insert_iter = document.source_buffer.get_iter_at_mark(document.source_buffer.get_insert())
            text = document.indent_text_with_whitespace_at_iter(text, insert_iter)

            document.source_buffer.insert_at_cursor(text)
        else:
            text = before + '•' + after
            text = document.replace_tabs_with_spaces_if_set(text)
            insert_iter = document.source_buffer.get_iter_at_mark(document.source_buffer.get_insert())
            text = document.indent_text_with_whitespace_at_iter(text, insert_iter)
            document.source_buffer.insert_at_cursor(text)

        document.select_first_dot_around_cursor(offset_before=len(text), offset_after=0)
        document.source_buffer.end_user_action()

    def insert_symbol(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return
        if parameter == None: return

        document = self.workspace.get_active_document()
        document.source_buffer.begin_user_action()

        text = parameter[0]
        text = document.replace_tabs_with_spaces_if_set(text)
        insert_iter = document.source_buffer.get_iter_at_mark(document.source_buffer.get_insert())
        text = document.indent_text_with_whitespace_at_iter(text, insert_iter)

        bounds = document.source_buffer.get_selection_bounds()

        if len(bounds) > 1:
            document.source_buffer.delete_selection(False, False)

        document.source_buffer.insert_at_cursor(text)
        document.select_first_dot_around_cursor(offset_before=len(text), offset_after=0)
        document.source_buffer.end_user_action()

    def insert_after_packages(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        document.insert_text_after_packages_if_possible(parameter[0])
        document.select_first_dot_around_cursor(offset_before=len(parameter[0]), offset_after=0)
        document.scroll_cursor_onscreen()

    def insert_before_document_end(self, action, parameter):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        document.insert_before_document_end(parameter[0])
        document.scroll_cursor_onscreen()

    def add_packages(self, action, parameter):
        if self.workspace.get_active_document() == None: return
        if parameter == None: return

        document = self.workspace.get_active_document()
        if document.is_latex_document():
            document.add_packages(parameter)
            document.scroll_cursor_onscreen()

    def start_include_bibtex_file_dialog(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('include_bibtex_file').run(document)

    def start_include_latex_file_dialog(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('include_latex_file').run(document)

    def start_add_remove_packages_dialog(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('add_remove_packages').run(document)

    def toggle_comment(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        document.source_buffer.begin_user_action()

        bounds = document.source_buffer.get_selection_bounds()

        if len(bounds) > 1:
            end = (bounds[1].get_line() + 1) if (bounds[1].get_line_index() > 0) else bounds[1].get_line()
            line_numbers = list(range(bounds[0].get_line(), end))
        else:
            line_numbers = [document.source_buffer.get_iter_at_mark(document.source_buffer.get_insert()).get_line()]

        do_comment = False
        for line_number in line_numbers:
            line = document.get_line(line_number)
            if not line.lstrip().startswith('%'):
                do_comment = True

        if do_comment:
            for line_number in line_numbers:
                found, line_iter = document.source_buffer.get_iter_at_line(line_number)
                document.source_buffer.insert(line_iter, '%')
        else:
            for line_number in line_numbers:
                line = document.get_line(line_number)
                offset = len(line) - len(line.lstrip())
                found, start = document.source_buffer.get_iter_at_line(line_number)
                start.forward_chars(offset)
                end = start.copy()
                end.forward_char()
                document.source_buffer.delete(start, end)

        document.source_buffer.end_user_action()

    def start_search(self, action=None, parameter=None):
        # 当帮助面板获得键盘焦点时，Ctrl+F 应打开帮助搜索并聚焦输入框，
        # 而不是启动编辑器查找栏（编辑器查找栏的 Ctrl+F 仅在文档中生效）。
        help_view = self.main_window.help_panel
        focused_widget = self.main_window.get_focus()
        if help_view is not None and focused_widget is not None and help_view.is_ancestor(focused_widget):
            self.workspace.help_panel.controller.open_search()
            return

        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().search.set_mode_search()

    def start_search_and_replace(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().search.set_mode_replace()

    def find_next(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        search = self.workspace.get_active_document().search
        if not search.view.get_search_mode():
            search.set_mode_search()
        search.on_search_next_match()

    def find_previous(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        search = self.workspace.get_active_document().search
        if not search.view.get_search_mode():
            search.set_mode_search()
        search.on_search_previous_match()

    def stop_search(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().search.hide_search_bar()

    def _get_active_multicursor(self):
        """获取当前活动文档的 MultiCursor 实例，或 None。"""
        document = self.workspace.get_active_document()
        if document is None:
            return None
        mc = getattr(document, 'multicursor', None)
        return mc

    def _mc_feature_enabled(self, feature_name):
        """Check if a specific experimental multi-cursor feature is enabled."""
        settings = self.settings
        if not settings.get_value('preferences', 'experimental_features'):
            return False
        return settings.get_value('preferences', feature_name)

    def select_next_occurrence(self, action=None, parameter=None):
        """在当前活动文档中选中下一个相同词/匹配（Ctrl+D）。"""
        if not self._mc_feature_enabled('experimental_select_next'):
            return
        mc = self._get_active_multicursor()
        if mc is not None:
            mc.select_next_occurrence()

    def select_all_occurrences(self, action=None, parameter=None):
        """选中所有相同词/匹配（Ctrl+Shift+L）。"""
        if not self._mc_feature_enabled('experimental_select_all'):
            return
        mc = self._get_active_multicursor()
        if mc is not None:
            mc.select_all_occurrences()

    def add_cursor_above(self, action=None, parameter=None):
        """在当前光标所在位置上方行添加光标（Ctrl+Alt+Up）。"""
        if not self._mc_feature_enabled('experimental_add_above'):
            return
        mc = self._get_active_multicursor()
        if mc is not None:
            mc.add_cursor_above()

    def add_cursor_below(self, action=None, parameter=None):
        """在当前光标所在位置下方行添加光标（Ctrl+Alt+Down）。"""
        if not self._mc_feature_enabled('experimental_add_below'):
            return
        mc = self._get_active_multicursor()
        if mc is not None:
            mc.add_cursor_below()

    def clear_multi_cursor(self, action=None, parameter=None):
        """清除所有附加光标（Escape）。"""
        if not self._mc_feature_enabled('experimental_escape_clear'):
            return
        mc = self._get_active_multicursor()
        if mc is not None:
            mc.clear_all()

    def cut(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.copy()
        self.workspace.get_active_document().delete_selection()

    def copy(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        text = document.get_selected_text()
        if text != None:
            clipboard = document.source_view.get_clipboard()
            clipboard.set(text)

    def paste(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        document = self.workspace.get_active_document()
        clipboard = document.source_view.get_clipboard()
        # 异步探测剪贴板是否含图片纹理；含图片则进入插入对话框，
        # 否则维持原有纯文本粘贴路径（零回归）。
        clipboard.read_value_async(Gdk.Texture, GLib.PRIORITY_DEFAULT, None, self._paste_clipboard_read, document)

    def _paste_clipboard_read(self, clipboard, result, document):
        try:
            texture = clipboard.read_value_finish(result)
        except Exception:
            texture = None
        if texture is not None:
            # 直接粘贴图片：弹出对话框，图片来源已就绪（剪贴板）
            DialogLocator.get_dialog('insert_image').open(document, texture=texture)
        else:
            document.source_view.emit('paste-clipboard')

    def start_insert_image_dialog(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return
        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('insert_image').open(document, texture=None)

    def delete_selection(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().delete_selection()

    def select_all(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().select_all()

    def undo(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().source_buffer.undo()

    def redo(self, action=None, parameter=None):
        if self.workspace.get_active_document() == None: return

        self.workspace.get_active_document().source_buffer.redo()

    def zoom_in(self, action=None, parameter=''):
        font_desc = Pango.FontDescription.from_string(FontManager.font_string)
        font_desc.set_size(min(font_desc.get_size() * 1.1, 24 * Pango.SCALE))
        FontManager.font_string = font_desc.to_string()
        FontManager.propagate_font_setting()
        # 仅持久化缩放倍率到独立设置项；不要改写 settings.font_string（那是干净基准，
        # 一旦写入缩放后的字号，zoom_level 的分母会被污染，导致百分比被锁死）。
        self.settings.set_value('preferences', 'editor_font_zoom_level', FontManager.zoom_level)
        FontManager.saved_zoom_level = FontManager.zoom_level
        self._update_zoom_indicators()

    def zoom_out(self, action=None, parameter=''):
        font_desc = Pango.FontDescription.from_string(FontManager.font_string)
        font_desc.set_size(max(font_desc.get_size() / 1.1, 6 * Pango.SCALE))
        FontManager.font_string = font_desc.to_string()
        FontManager.propagate_font_setting()
        # 仅持久化缩放倍率到独立设置项；不要改写 settings.font_string（干净基准）。
        self.settings.set_value('preferences', 'editor_font_zoom_level', FontManager.zoom_level)
        FontManager.saved_zoom_level = FontManager.zoom_level
        self._update_zoom_indicators()

    def reset_zoom(self, action=None, parameter=''):
        # 重置到干净基准字号（100%），不牵连 settings.font_string。
        FontManager.font_string = FontManager.base_font_string
        FontManager.zoom_level = 1.0
        FontManager.propagate_font_setting()
        # 重置缩放倍率为 1.0，同时保存到独立设置项
        self.settings.set_value('preferences', 'editor_font_zoom_level', 1.0)
        FontManager.saved_zoom_level = 1.0
        self._update_zoom_indicators()

    def _update_zoom_indicators(self):
        '''缩放是全局设置，所有文档共享同一倍率。刷新右键菜单的缩放按钮标签，
        以及每个已打开文档状态栏中的缩放百分比标签。'''
        zoom_label = "{:.0%}".format(FontManager.zoom_level)
        try:
            self.workspace.context_menu.popover_more.view.reset_zoom_button.set_label(zoom_label)
            self.workspace.context_menu.reset_zoom_button_pointer.set_label(zoom_label)
        except AttributeError:
            pass
        for document in self.workspace.open_documents:
            statusbar = getattr(document, 'statusbar', None)
            if statusbar is not None:
                statusbar.update_zoom_field()

    def preview_set_zoom_level(self, action=None, parameter=None):
        if parameter is None: return
        level = parameter.unpack()
        document = self.workspace.get_root_or_active_latex_document()
        if document is not None:
            # popover 选具体百分比是手动缩放，脱离任何 fit 模式。
            document.preview.zoom_manager.zoom_mode = 'manual'
            document.preview.zoom_manager.set_zoom_level_auto_offset(level)
            # 同步 state，使菜单里对应级别前自动显示对钩。
            if action is not None:
                action.set_state(parameter)

    def preview_set_fit_mode(self, action=None, parameter=None):
        if parameter is None: return
        mode = parameter.unpack()
        document = self.workspace.get_root_or_active_latex_document()
        if document is None: return
        zoom_manager = document.preview.zoom_manager
        # 仅切换缩放模式；state 由 presenter 的 _sync_zoom_action_state 依据
        # zoom_manager.zoom_mode 统一同步，保证与数值缩放的 state 互斥。
        if mode == 'width':
            zoom_manager.set_zoom_fit_to_width_auto_offset()
        elif mode == 'text-width':
            zoom_manager.set_zoom_fit_to_text_width()
        elif mode == 'height':
            zoom_manager.set_zoom_fit_to_height()

    # --- Preview context menu actions -----------------------------------------

    def _get_preview(self):
        '''Return the active document's preview, or None.'''
        document = self.workspace.get_root_or_active_latex_document()
        if document is None:
            return None
        return document.preview

    def preview_rotate_cw(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is not None:
            preview.rotate(90)

    def preview_rotate_ccw(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is not None:
            preview.rotate(-90)

    def preview_toggle_recolor(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None:
            return
        preview.toggle_recolor()
        action.set_state(GLib.Variant.new_boolean(preview.recolor_pdf))

    def preview_open_link(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None:
            return
        link = preview.context_menu.current_link
        if link is not None:
            preview.open_link(link)

    def preview_copy_link(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None:
            return
        link = preview.context_menu.current_link
        if link is not None:
            text = link[1] if link[1] is not None else ''
            # GTK4 移除了 Gdk.Clipboard.set_text()，改用 set_content + ContentProvider。
            Gdk.Display.get_default().get_clipboard().set_content(Gdk.ContentProvider.new_for_value(text))

    def preview_copy_text(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None or parameter is None:
            return
        preview.copy_page_text(parameter.get_int32())

    def preview_copy_image(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None or parameter is None:
            return
        preview.copy_page_image(parameter.get_int32())

    def preview_save_image(self, action, parameter=None):
        preview = self._get_preview()
        if preview is None or parameter is None:
            return
        preview.save_page_image(parameter.get_int32())

    def preview_search_pdf(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is None:
            return
        preview.context_menu.open_search_popover(None)

    def preview_show_source(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document is not None and hasattr(document, 'view') and hasattr(document.view, 'source_view'):
            document.view.source_view.grab_focus()

    def preview_zoom_in(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is not None:
            preview.zoom_manager.zoom_in()

    def preview_zoom_out(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is not None:
            preview.zoom_manager.zoom_out()

    def preview_print_pdf(self, action=None, parameter=None):
        preview = self._get_preview()
        if preview is not None:
            preview.print_pdf()

    def show_preferences_dialog(self, action=None, parameter=''):
        DialogLocator.get_dialog('preferences').run()

    def show_document_properties(self, action=None, parameter=''):
        if self.workspace.get_active_document() == None: return
        document = self.workspace.get_active_document()
        DialogLocator.get_dialog('document_properties').run(document)

    def show_shortcuts_dialog(self, action=None, parameter=''):
        DialogLocator.get_dialog('keyboard_shortcuts').run()

    def show_about_dialog(self, action=None, parameter=''):
        DialogLocator.get_dialog('about').run()

    def show_context_menu(self, action=None, parameter=''):
        PopoverManager.create_popover('context_menu').view.popup()


    # --- Label context menu actions ------------------------------------------

    def jump_to_definition(self, action=None, parameter=None):
        r'''Jump to the \label{...} definition for the given label name.'''
        if parameter is None:
            return
        label = parameter.get_string()
        document = self.workspace.get_active_document()
        if document is None or not document.is_latex_document():
            return
        # Search for \label{label} in the document
        import re
        pattern = r'\\label\{' + re.escape(label) + r'\}'
        text = document.get_all_text()
        match = re.search(pattern, text)
        if match:
            offset = match.start()
            line = document.source_buffer.get_iter_at_offset(offset).get_line()
            document.place_cursor(line)
            document.scroll_cursor_onscreen(margin_lines=0)
            document.view.source_view.grab_focus()
        else:
            # Check included documents
            workspace = self.workspace
            for doc in workspace.open_documents:
                if doc is document:
                    continue
                if not doc.is_latex_document():
                    continue
                text = doc.get_all_text()
                match = re.search(pattern, text)
                if match:
                    workspace.set_active_document(doc)
                    offset = match.start()
                    line = doc.source_buffer.get_iter_at_offset(offset).get_line()
                    doc.place_cursor(line)
                    doc.scroll_cursor_onscreen()
                    doc.view.source_view.grab_focus()
                    return

    def copy_ref_label(self, action=None, parameter=None):
        r'''Copy \ref{label} to clipboard.'''
        if parameter is None:
            return
        label = parameter.get_string()
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\ref{' + label + '}')

    def copy_pageref_label(self, action=None, parameter=None):
        r'''Copy \pageref{label} to clipboard.'''
        if parameter is None:
            return
        label = parameter.get_string()
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\pageref{' + label + '}')

    def copy_autoref_label(self, action=None, parameter=None):
        '''Copy \autoref{label} to clipboard.'''
        if parameter is None:
            return
        label = parameter.get_string()
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set('\\autoref{' + label + '}')

    def find_all_refs(self, action=None, parameter=None):
        '''Search for all \ref{label} occurrences in the document.'''
        if parameter is None:
            return
        label = parameter.get_string()
        document = self.workspace.get_active_document()
        if document is None:
            return
        # Use the search bar to find all \ref{label} occurrences
        search = document.search
        search.set_mode_search()
        search.view.entry.set_text('\\ref{' + label + '}')
        search.on_search_entry_changed(search.view.entry)

    def fold_all(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document is None:
            return
        buffer = document.source_buffer
        buffer.begin_user_action()
        document.code_folding.fold_all()
        buffer.end_user_action()

    def unfold_all(self, action=None, parameter=None):
        document = self.workspace.get_active_document()
        if document is None:
            return
        buffer = document.source_buffer
        buffer.begin_user_action()
        document.code_folding.unfold_all()
        buffer.end_user_action()



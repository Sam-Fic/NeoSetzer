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

from gi.repository import Adw, Gtk, GLib

from setzer.app.service_locator import ServiceLocator


class WelcomeScreen(object):
    '''Welcome screen presenter.

    Drives the view returned by WelcomeScreenView(): it keeps the
    recent-documents list in sync with the workspace and wires the
    quick-action buttons (New LaTeX / New BibTeX / Template Wizard) to
    the workspace actions.

    activate()/deactivate() are retained as no-ops for call-site
    compatibility.
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().welcome_screen
        self.is_active = False

        # quick-action buttons
        self.view.new_latex_button.connect('clicked', self.on_new_latex_clicked)
        self.view.new_bibtex_button.connect('clicked', self.on_new_bibtex_clicked)
        self.view.wizard_button.connect('clicked', self.on_wizard_clicked)
        self.view.example_button.connect('clicked', self.on_example_clicked)

        # open a recent document when its row is activated
        self.view.recent_listbox.connect('row-activated', self.on_recent_row_activated)

        # clear-all button for the recent documents list
        self.view.recent_clear_button.connect('clicked', self.on_clear_recent_clicked)

        # keep the recent list in sync with the workspace
        self.workspace.connect('update_recently_opened_documents', self.on_recently_opened_changed)

        # 「最近关闭」列表：actions.push_closed_document / reopen_* 会发
        # update_closed_documents 信号，这里刷新列表。
        self.workspace.connect('update_closed_documents', self.on_closed_documents_changed)
        self.view.closed_listbox.connect('row-activated', self.on_closed_row_activated)

        # Row 复用缓存：refresh_recent_documents 每次打开/关闭文档都会被调用，
        # 原实现销毁全部 Adw.ActionRow + 2 个 Gtk.Image（prefix + suffix）再重建。
        # 50 条最近文档 = 150 个 widget 销毁 + 150 个创建，每次 5-15ms。
        # 改为按 filename 缓存 row：已存在的 row 直接 reparent 到 listbox（按
        # date 排序位置），新增的 filename 才创建 row，已移除的 filename 才销毁。
        self._row_cache = dict()

        self.refresh_recent_documents()
        if hasattr(self.workspace, 'actions'):
            self.refresh_closed_documents()
        self.activate()

    def activate(self):
        self.is_active = True

    def deactivate(self):
        self.is_active = False

    # --- quick actions ---

    def on_new_latex_clicked(self, button):
        self.workspace.actions.new_latex_document()

    def on_new_bibtex_clicked(self, button):
        self.workspace.actions.new_bibtex_document()

    def on_wizard_clicked(self, button):
        # Create the document but DON'T activate it yet — the wizard dialog
        # appears over the welcome screen. After the wizard closes, activate
        # the document (if user created a template) or remove it (if cancelled).
        # This avoids the jarring flash of editor → wizard dialog.
        document = self.workspace.create_latex_document()
        self.workspace.add_document(document)
        from setzer.dialogs.dialog_locator import DialogLocator
        wizard = DialogLocator.get_dialog('document_wizard')
        wizard.run(document)
        wizard.view.connect('closed', lambda d: self._on_wizard_closed(document, wizard))

    def _on_wizard_closed(self, document, wizard):
        if wizard.completed:
            self.workspace.set_active_document(document)
        else:
            self.workspace.remove_document(document)

    def on_example_clicked(self, button):
        example_path = os.path.join(ServiceLocator.get_resources_path(), 'example_document.tex')
        if os.path.isfile(example_path):
            self.workspace.open_document_by_filename_with_spinner(example_path)

    # --- recent documents ---

    def on_recently_opened_changed(self, workspace, recently_opened_documents):
        self.refresh_recent_documents()

    def on_recent_row_activated(self, listbox, row):
        filename = getattr(row, 'filename', None)
        if filename is not None:
            import os.path
            if not os.path.isfile(filename):
                self.workspace.remove_recently_opened_document(filename, notify=True)
                return
            self.workspace.open_document_by_filename_with_spinner(filename)

    def refresh_recent_documents(self):
        listbox = self.view.recent_listbox

        documents = self.workspace.recently_opened_documents.values()
        # 置顶项排在最前，其余按最近使用时间降序。
        pinned = self.workspace.pinned_recent_documents
        documents = sorted(documents, key=lambda val: (0 if val['filename'] in pinned else 1, -val['date']))

        if len(documents) == 0:
            self.view.empty_state.set_visible(True)
            self.view.recent_listbox.set_visible(False)
            self.view.recent_clear_button.set_visible(False)
            # 清空 listbox 但保留 row cache（下次有最近文档时复用）。
            while (child := listbox.get_first_child()) is not None:
                listbox.remove(child)
            return

        self.view.empty_state.set_visible(False)
        self.view.recent_listbox.set_visible(True)
        self.view.recent_clear_button.set_visible(True)

        # 先从 listbox 移除所有 row（不销毁——缓存的 row 仍被 _row_cache 引用）。
        # 然后按排序重新 append。这样：
        # - 已存在的 filename：直接 reparent，不创建新 widget（省 widget/条）
        # - 新 filename：创建 row 并加入 cache
        # - 已移除的 filename：从 cache 删除，row 失去引用被 GC
        # 每条 row 都经 _update_recent_row 刷新动态信息（大小/时间/修改/置顶）。
        while (child := listbox.get_first_child()) is not None:
            listbox.remove(child)

        valid_filenames = set()
        row_cache = self._row_cache
        for doc in documents:
            filename = doc['filename']
            valid_filenames.add(filename)
            row = row_cache.get(filename)
            if row is None:
                row = self._create_recent_row(filename)
                row_cache[filename] = row
            self._update_recent_row(row)
            listbox.append(row)

        # 移除已不在最近文档列表中的 row（文件被删除或用户清除历史）
        obsolete = set(row_cache.keys()) - valid_filenames
        for filename in obsolete:
            del row_cache[filename]

    def on_pin_recent_clicked(self, button, filename):
        self.workspace.toggle_pinned_recent_document(filename)

    def _create_recent_row(self, filename):
        '''创建单个最近文档 row 的骨架（Widget 仅在此创建一次，后续 refresh 复用）。
        动态信息（文件大小 / 时间戳 / 修改状态 / 置顶状态）由 _update_recent_row
        填充，以便复用 row 时也能反映最新状态（文件被外部改动、置顶切换等）。'''
        row = Adw.ActionRow()
        row.filename = filename
        row.set_activatable(True)

        if filename.endswith('.bib'):
            icon_name = 'document-bibtex-symbolic'
        else:
            icon_name = 'document-latex-symbolic'
        # 不设 set_pixel_size：让图标继承 Adw.ActionRow 的标准图标尺寸，
        # 随系统字体/HIDPI 缩放自适应。写死 16px 在 200% 缩放下会显得过小。
        icon = Gtk.Image.new_from_icon_name(icon_name)
        row.add_prefix(icon)

        # 右侧信息区：修改指示 + 文件大小 · 时间戳 + 置顶按钮 + 移除按钮
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        info_box.set_valign(Gtk.Align.CENTER)

        modified_image = Gtk.Image(icon_name='document-revert-symbolic')
        modified_image.set_tooltip_text(_('Changed on disk since last opened'))
        modified_image.set_visible(False)
        modified_image.set_opacity(0.6)
        info_box.append(modified_image)
        row._modified_image = modified_image

        info_label = Gtk.Label()
        info_label.add_css_class('dim-label')
        info_label.add_css_class('caption')
        info_box.append(info_label)
        row._info_label = info_label

        pin_button = Gtk.Button(icon_name='view-pin-symbolic')
        pin_button.set_has_frame(False)
        pin_button.set_valign(Gtk.Align.CENTER)
        pin_button.add_css_class('flat')
        pin_button.connect('clicked', self.on_pin_recent_clicked, filename)
        info_box.append(pin_button)
        row._pin_button = pin_button

        # 单条移除按钮：点击时仅从最近列表移除（不删除文件）。
        # Gtk.Button 在 ListBox row 内会消费点击事件，不会触发 row-activated。
        remove_button = Gtk.Button(icon_name='window-close-symbolic')
        remove_button.set_has_frame(False)
        remove_button.set_valign(Gtk.Align.CENTER)
        remove_button.add_css_class('flat')
        remove_button.set_tooltip_text(_('Remove from recent list'))
        remove_button.connect('clicked', self.on_remove_recent_clicked, filename)
        info_box.append(remove_button)

        row.add_suffix(info_box)

        # 8.3：重名文件时 tooltip 显示完整路径，便于区分。
        row.set_tooltip_text(filename)
        return row

    def _update_recent_row(self, row):
        '''填充/刷新 row 的动态信息（创建后或 refresh 复用时都会调用）。'''
        filename = row.filename
        doc = self.workspace.recently_opened_documents.get(filename)
        if doc is None:
            return

        if not row.get_title():
            row.set_title(os.path.basename(filename))
            row.set_subtitle(os.path.dirname(filename))

        parts = []
        try:
            parts.append(self._format_size(os.path.getsize(filename)))
        except OSError:
            pass
        parts.append(self._format_timestamp(doc['date']))
        row._info_label.set_text(' · '.join(parts))

        modified = False
        try:
            modified = os.path.getmtime(filename) > doc['date']
        except OSError:
            pass
        row._modified_image.set_visible(modified)

        pinned = self.workspace.is_pinned_recent_document(filename)
        row._pin_button.set_tooltip_text(_('Unpin') if pinned else _('Pin'))
        if pinned:
            row._pin_button.remove_css_class('dim-label')
            if not row._pin_button.has_css_class('accent'):
                row._pin_button.add_css_class('accent')
        else:
            row._pin_button.remove_css_class('accent')
            if not row._pin_button.has_css_class('dim-label'):
                row._pin_button.add_css_class('dim-label')

    @staticmethod
    def _format_size(n):
        size = float(n)
        for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
            if size < 1024 or unit == 'TB':
                if unit == 'B':
                    return '{:.0f} {}'.format(size, unit)
                return '{:.1f} {}'.format(size, unit)
            size /= 1024

    @staticmethod
    def _format_timestamp(t):
        dt = GLib.DateTime.new_from_unix_local(t)
        if dt is None:
            return ''
        return dt.format('%x %H:%M')

    def on_remove_recent_clicked(self, button, filename):
        self.workspace.remove_recently_opened_document(filename, notify=True)

    def on_clear_recent_clicked(self, button):
        if len(self.workspace.recently_opened_documents) == 0:
            return
        dialog = Adw.AlertDialog(
            heading=_('Clear Recent List?'),
            body=_('All documents will be removed from the recent list. '
                   'This does not delete the files themselves.'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('clear', _('Clear'))
        dialog.set_response_appearance('clear', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        main_window = ServiceLocator.get_main_window()
        dialog.choose(main_window, None, self.on_clear_recent_confirmed)

    def on_clear_recent_confirmed(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'clear':
            self.workspace.clear_recently_opened_documents()

    # --- recently closed documents ---

    def on_closed_documents_changed(self, workspace, closed_stack):
        self.refresh_closed_documents()

    def on_closed_row_activated(self, listbox, row):
        filename = getattr(row, 'filename', None)
        if filename is None:
            return
        import os.path
        if not os.path.isfile(filename):
            if hasattr(self.workspace, 'actions'):
                self.workspace.actions.reopen_closed_document(filename)
            return
        if hasattr(self.workspace, 'actions'):
            self.workspace.actions.reopen_closed_document(filename)

    def refresh_closed_documents(self):
        '''从 actions.get_closed_document_stack() 重建「最近关闭」列表。

        栈最多 5 项，无需 row 缓存（与 recent 不同，closed 频率低、量小）。
        栈为空时隐藏整个 section。
        '''
        if not hasattr(self.workspace, 'actions'):
            return
        listbox = self.view.closed_listbox
        closed_stack = self.workspace.actions.get_closed_document_stack()

        # 清空现有 rows
        while (child := listbox.get_first_child()) is not None:
            listbox.remove(child)

        if len(closed_stack) == 0:
            self.view.closed_heading.set_visible(False)
            listbox.set_visible(False)
            return

        self.view.closed_heading.set_visible(True)
        listbox.set_visible(True)

        for filename in closed_stack:
            row = self._create_closed_row(filename)
            listbox.append(row)

    def _create_closed_row(self, filename):
        '''创建单个最近关闭文档 row。与 _create_recent_row 类似但更简单
        （无 remove 按钮——重开即从栈移除，无需单条删除）。'''
        import os.path
        row = Adw.ActionRow()
        row.filename = filename
        row.set_title(os.path.basename(filename))
        row.set_subtitle(os.path.dirname(filename))
        row.set_activatable(True)

        if filename.endswith('.bib'):
            icon_name = 'document-bibtex-symbolic'
        else:
            icon_name = 'document-latex-symbolic'
        # 不设 set_pixel_size：与 _create_recent_row 一致，让图标随 HIDPI 自适应。
        icon = Gtk.Image.new_from_icon_name(icon_name)
        row.add_prefix(icon)
        return row

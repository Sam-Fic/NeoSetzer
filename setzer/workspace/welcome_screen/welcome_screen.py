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

import os

from gi.repository import Adw, Gtk

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

        # open a recent document when its row is activated
        self.view.recent_listbox.connect('row-activated', self.on_recent_row_activated)

        # keep the recent list in sync with the workspace
        self.workspace.connect('update_recently_opened_documents', self.on_recently_opened_changed)

        # Row 复用缓存：refresh_recent_documents 每次打开/关闭文档都会被调用，
        # 原实现销毁全部 Adw.ActionRow + 2 个 Gtk.Image（prefix + suffix）再重建。
        # 50 条最近文档 = 150 个 widget 销毁 + 150 个创建，每次 5-15ms。
        # 改为按 filename 缓存 row：已存在的 row 直接 reparent 到 listbox（按
        # date 排序位置），新增的 filename 才创建 row，已移除的 filename 才销毁。
        self._row_cache = dict()

        self.refresh_recent_documents()
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
        # The document wizard inserts a template into the active document,
        # so first create and activate a blank LaTeX document, then open it.
        document = self.workspace.create_latex_document()
        self.workspace.add_document(document)
        self.workspace.set_active_document(document)
        self.workspace.actions.start_wizard()

    # --- recent documents ---

    def on_recently_opened_changed(self, workspace, recently_opened_documents):
        self.refresh_recent_documents()

    def on_recent_row_activated(self, listbox, row):
        filename = getattr(row, 'filename', None)
        if filename is not None:
            import os.path
            if not os.path.isfile(filename):
                self.workspace.remove_recently_opened_document(filename)
                return
            self.workspace.open_document_by_filename(filename)

    def refresh_recent_documents(self):
        listbox = self.view.recent_listbox

        documents = self.workspace.recently_opened_documents.values()
        # most-recently-used first
        documents = sorted(documents, key=lambda val: val['date'], reverse=True)

        if len(documents) == 0:
            self.view.empty_label.set_visible(True)
            # 清空 listbox 但保留 row cache（下次有最近文档时复用）。
            while (child := listbox.get_first_child()) is not None:
                listbox.remove(child)
            return

        self.view.empty_label.set_visible(False)

        # 先从 listbox 移除所有 row（不销毁——缓存的 row 仍被 _row_cache 引用）。
        # 然后按 date 降序重新 append。这样：
        # - 已存在的 filename：直接 reparent，不创建新 widget（省 3 widget/条）
        # - 新 filename：创建 row 并加入 cache
        # - 已移除的 filename：从 cache 删除，row 失去引用被 GC
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
            listbox.append(row)

        # 移除已不在最近文档列表中的 row（文件被删除或用户清除历史）
        obsolete = set(row_cache.keys()) - valid_filenames
        for filename in obsolete:
            del row_cache[filename]

    def _create_recent_row(self, filename):
        '''创建单个最近文档 row。basename/dirname/icon 仅在此处计算一次，
        后续 refresh 复用 row 时不再重复计算（原实现每次 refresh 都对每个
        filename 调 os.path.basename + os.path.dirname）。'''
        row = Adw.ActionRow()
        row.filename = filename
        row.set_title(os.path.basename(filename))
        row.set_subtitle(os.path.dirname(filename))
        row.set_activatable(True)

        if filename.endswith('.bib'):
            icon_name = 'document-bibtex-symbolic'
        else:
            icon_name = 'document-latex-symbolic'
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(16)
        row.add_prefix(icon)

        open_icon = Gtk.Image.new_from_icon_name('go-next-symbolic')
        row.add_suffix(open_icon)
        return row

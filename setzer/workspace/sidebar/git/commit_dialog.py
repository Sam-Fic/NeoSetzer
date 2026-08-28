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

import builtins


def _(s):
    '''翻译函数。运行时委托 builtins._（由 setzer.in 注入 trans.gettext），
    未注入时回退到原字符串——便于开发/测试。'''
    fn = getattr(builtins, '_', None)
    return s if fn is None else fn(s)


import os

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango


STATUS_LABELS = {
    '??': _('Untracked'),
    'A ': _('Added'),
    'M ': _('Staged changes'),
    ' M': _('Modified'),
    'AM': _('Added + modified'),
    'D ': _('Deleted (staged)'),
    ' D': _('Deleted'),
    'MM': _('Modified (staged + unstaged)'),
}


class CommitDialog(Adw.Dialog):
    '''Commit & Push 对话框（#443）：输入 commit message + 勾选改动文件，
    然后由 GitRepository 执行 add → commit → push。

    刻意不做的事：diff 预览、branch 切换、精细 staging——需要者用终端。'''

    def __init__(self, workspace, repo, on_finished):
        super().__init__()
        self.workspace = workspace
        self.repo = repo
        self.on_finished = on_finished
        self.file_rows = []  # [(check_button, entry dict), ...]

        self.set_title(_('Commit && Push'))
        self.set_content_size(440, 520)
        # 工作区规范：禁用系统右上角关闭按钮，仅保留手动 Cancel 入口
        self.set_show_end_title_buttons(False)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        self.cancel_button = Gtk.Button(label=_('Cancel'))
        self.cancel_button.connect('clicked', lambda b: self.close())
        header.pack_start(self.cancel_button)
        self.commit_button = Gtk.Button(label=_('Commit'))
        self.commit_button.add_css_class('suggested-action')
        self.commit_button.set_sensitive(False)
        self.commit_button.connect('clicked', self.on_commit_clicked)
        header.pack_end(self.commit_button)
        main_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # Commit message 输入。Gtk.Entry（非 Adw.EntryRow：1.9.1 绑定无
        # set_placeholder_text，见项目记忆）。
        self.message_entry = Gtk.Entry()
        self.message_entry.set_placeholder_text(_('Commit message'))
        self.message_entry.connect('changed', lambda e: self.update_commit_sensitivity())
        self.message_entry.connect('activate', self.on_entry_activate)
        content.append(self.message_entry)

        label = Gtk.Label(label=_('Changes to include:'))
        label.set_xalign(0)
        label.add_css_class('dim-label')
        label.add_css_class('caption')
        content.append(label)

        # 单一滚动层：Clamp（限宽居中）包内容，ScrolledWindow 包 Clamp，
        # 文件列表自身随窗口滚动，无嵌套滚动。
        self.label_empty = Gtk.Label(label=_('No changes in this repository.'))
        self.label_empty.add_css_class('dim-label')
        self.label_empty.set_visible(False)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('boxed-list')

        content.append(self.label_empty)
        content.append(self.list_box)

        clamp = Adw.Clamp()
        clamp.set_child(content)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(clamp)

        self.set_child(main_box)
        main_box.append(scrolled)

        self.populate_files()

    def populate_files(self):
        '''按仓库当前状态填充文件勾选列表。untracked 默认不勾
        （防 .aux/.log/.pdf 等生成物误入提交）；.gitignore 排除项
        porcelain 默认不输出，自然不显示。'''
        state = self.repo.state
        files = state['changed_files'] if state else []
        self.file_rows = []

        child = self.list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.list_box.remove(child)
            child = next_child

        self.label_empty.set_visible(len(files) == 0)

        for entry in files:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(10)
            box.set_margin_end(10)
            row.set_child(box)

            check = Gtk.CheckButton()
            check.set_active(not entry['untracked'])
            check.connect('toggled', lambda c: self.update_commit_sensitivity())
            box.append(check)

            path_label = Gtk.Label(label=entry['path'])
            path_label.set_hexpand(True)
            path_label.set_xalign(0)
            path_label.set_ellipsize(Pango.EllipsizeMode.START)
            box.append(path_label)

            status_label = Gtk.Label(label=STATUS_LABELS.get(entry['status'], entry['status']))
            status_label.add_css_class('dim-label')
            status_label.add_css_class('caption')
            box.append(status_label)

            self.list_box.append(row)
            self.file_rows.append((check, entry))

        self.update_commit_sensitivity()

    def get_selected_files(self):
        return [entry['path'] for check, entry in self.file_rows if check.get_active()]

    def update_commit_sensitivity(self, *args):
        self.commit_button.set_sensitive(
            bool(self.message_entry.get_text().strip()) and bool(self.get_selected_files()))

    def on_entry_activate(self, entry):
        if self.commit_button.get_sensitive():
            self.on_commit_clicked(None)

    def on_commit_clicked(self, button):
        message = self.message_entry.get_text().strip()
        files = self.get_selected_files()
        if not message or not files:
            return
        # 先保存仓库内所有有未保存修改的文档，保证提交内容与编辑器一致。
        self.save_open_documents()
        self.commit_button.set_sensitive(False)
        self.repo.commit_and_push(files, message, self.on_commit_done)

    def save_open_documents(self):
        root_prefix = self.repo.root + os.sep
        for document in self.workspace.get_all_documents():
            filename = document.get_filename()
            if filename is None:
                continue
            if not filename.startswith(root_prefix):
                continue
            if document.source_buffer.get_modified():
                document.save_to_disk(show_toast=False)

    def on_commit_done(self, error):
        self.close()
        if self.on_finished is not None:
            self.on_finished(error)

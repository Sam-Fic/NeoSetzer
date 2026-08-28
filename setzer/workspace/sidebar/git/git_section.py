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


import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw

import time

from setzer.app.service_locator import ServiceLocator
from setzer.vcs.git_repository import GitRepository, git_available
from setzer.workspace.sidebar.git.git_section_viewgtk import GitSectionView
from setzer.workspace.sidebar.git.commit_dialog import CommitDialog


class GitSection(object):
    '''Git 侧栏面板控制器（#443）。显示当前活动文档所属仓库的
    分支/ahead-behind/最近提交/改动数，提供 Pull 与 Commit & Push。

    显示条件：git 可用 + 文档在 repo 内 + 偏好开启，否则整个 section 隐藏
    （与 DocumentStats 的 hide_view/set_group 模式一致）。

    刷新时机（事件驱动，无轮询）：活动文档切换、文档保存、仓库状态信号
    （.git/index、.git/HEAD 的 Gio.FileMonitor 覆盖终端里的 commit/pull）。
    '''

    def __init__(self, workspace):
        self.workspace = workspace
        self.settings = ServiceLocator.get_settings()
        self.document = None
        self.repo = None
        self.group = None

        self.view = GitSectionView()
        self.view.button_pull.connect('clicked', self.on_pull_clicked)
        self.view.button_commit.connect('clicked', self.on_commit_clicked)

        self._saved_document = None

        workspace.connect('new_active_document', self.on_new_active_document)
        self.settings.connect('settings_changed', self.on_settings_changed)
        self.set_document()

    def set_group(self, group):
        self.group = group

    def on_new_active_document(self, workspace, document):
        self.set_document()

    def on_settings_changed(self, settings, parameter):
        section, item, value = parameter
        if section == 'preferences' and item in ('git_integration', 'git_sidebar_panel'):
            self.set_document()

    def set_document(self):
        document = self.workspace.get_active_document()

        # 'saved' 信号跟随活动文档：保存后刷新仓库状态
        if document is not self._saved_document:
            if self._saved_document is not None:
                try:
                    self._saved_document.disconnect('saved', self.on_document_saved)
                except KeyError:
                    pass
            self._saved_document = document
            if document is not None:
                document.connect('saved', self.on_document_saved)

        self.document = document

        repo = None
        if document is not None:
            repo = GitRepository.get_for_path(document.get_filename())
        if repo is not self.repo:
            if self.repo is not None:
                try:
                    self.repo.disconnect('state_changed', self.on_repo_state_changed)
                except KeyError:
                    pass
            self.repo = repo
            if self.repo is not None:
                self.repo.connect('state_changed', self.on_repo_state_changed)

        self.update_view()

    def on_document_saved(self, document):
        if self.repo is not None:
            self.repo.refresh()

    def on_repo_state_changed(self, repo):
        if repo is self.repo:
            self.update_view()

    def _panel_enabled(self):
        return (self.settings.get_value('preferences', 'git_integration')
                and self.settings.get_value('preferences', 'git_sidebar_panel')
                and git_available())

    def update_view(self):
        state = self.repo.state if self.repo is not None else None
        visible = self._panel_enabled() and state is not None
        if self.group is not None:
            self.group.set_visible(visible)
        if not visible:
            return

        # 分支 + ahead/behind
        self.view.label_branch.set_text(state['branch'] or _('(no commits yet)'))
        marks = []
        if state['ahead']:
            marks.append('↑ %d' % state['ahead'])
        if state['behind']:
            marks.append('↓ %d' % state['behind'])
        if marks:
            self.view.label_ahead_behind.set_text(' '.join(marks))
            self.view.label_ahead_behind.set_visible(True)
        elif state['upstream']:
            self.view.label_ahead_behind.set_text(_('synced'))
            self.view.label_ahead_behind.set_visible(True)
        else:
            self.view.label_ahead_behind.set_visible(False)

        # 最近提交
        if state['commit_subject']:
            self.view.label_commit.set_text(state['commit_subject'])
            self.view.label_commit.set_visible(True)
            if state['commit_timestamp']:
                date_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(state['commit_timestamp']))
            else:
                date_str = ''
            self.view.label_commit_date.set_text(date_str)
            self.view.label_commit_date.set_visible(True)
        else:
            self.view.label_commit.set_visible(False)
            self.view.label_commit_date.set_visible(False)

        # 改动概要
        count = len(state['changed_files'])
        if count == 0:
            self.view.label_changed.set_text(_('Working tree clean'))
        else:
            self.view.label_changed.set_text(_('Changed files') + ': %d' % count)

    # —— 写操作（pull / commit & push），按仓库显式信任 ——————————

    def _is_trusted(self):
        return self.repo.root in self.settings.get_value('preferences', 'git_trusted_repos')

    def _request_trust(self, callback):
        '''首次写操作前的仓库信任确认。git hooks 可执行任意命令，
        打开外来项目时这是真实风险，必须显式确认一次。'''
        dialog = Adw.AlertDialog()
        dialog.set_heading(_('Trust This Repository?'))
        dialog.set_body(_('Pull, commit and push run git commands in this repository, '
                          'which may execute its hooks:\n\n%s') % self.repo.root)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('trust', _('Trust and Continue'))
        dialog.set_response_appearance('trust', Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response', self.on_trust_response, callback)
        dialog.present(ServiceLocator.get_main_window())

    def on_trust_response(self, dialog, response, callback):
        if response != 'trust':
            return
        trusted = list(self.settings.get_value('preferences', 'git_trusted_repos'))
        if self.repo.root not in trusted:
            trusted.append(self.repo.root)
            self.settings.set_value('preferences', 'git_trusted_repos', trusted)
        callback()

    def on_pull_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        if not self._is_trusted():
            self._request_trust(self.do_pull)
            return
        self.do_pull()

    def do_pull(self):
        state = self.repo.state
        # 脏工作区保护：--ff-only 会失败，先给出明确提示，不留困惑状态。
        if state['dirty']:
            self.view.show_error(_('Working tree has uncommitted changes. Commit them '
                                   'first, or use a terminal to stash.'))
            return
        self.view.show_error(None)
        self.view.button_pull.set_sensitive(False)
        self.repo.pull(self.on_pull_done)

    def on_pull_done(self, error):
        self.view.button_pull.set_sensitive(True)
        if error:
            self.view.show_error(error)
        else:
            self.view.show_error(None)
            self.repo.refresh()

    def on_commit_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        if not self._is_trusted():
            self._request_trust(self.open_commit_dialog)
            return
        self.open_commit_dialog()

    def open_commit_dialog(self):
        if not self.repo.state['changed_files']:
            self.view.show_error(_('No changes to commit.'))
            return
        self.view.show_error(None)
        dialog = CommitDialog(self.workspace, self.repo, self.on_commit_finished)
        dialog.present(ServiceLocator.get_main_window())

    def on_commit_finished(self, error):
        if error:
            self.view.show_error(error)
        else:
            self.view.show_error(None)
            self.repo.refresh()

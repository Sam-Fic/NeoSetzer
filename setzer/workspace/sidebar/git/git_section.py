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

import os

from setzer.app.service_locator import ServiceLocator
from setzer.vcs.git_repository import GitRepository, git_available
from setzer.workspace.sidebar.git.git_section_viewgtk import (
    GitSectionView, format_commit_date,
)
from setzer.workspace.sidebar.git.commit_dialog import CommitDialog


class GitSection(object):
    '''Git 侧栏面板控制器（#443）。显示当前活动文档所属仓库的
    分支/ahead-behind/最近提交/改动数/线性历史，提供 Pull、Commit、Push。

    提交与推送是两个独立动作：纯本地仓库（或没配远程的项目）只需要提交。

    显示条件：git 可用 + 文档已保存 + 偏好开启；文档所在目录还不是仓库时
    显示「初始化仓库」入口（快捷初始化），所以面板不再因为「不在 repo 内」
    整体消失（与 DocumentStats 的 hide_view/set_group 模式一致）。

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
        self.view.button_push.connect('clicked', self.on_push_clicked)
        self.view.button_init.connect('clicked', self.on_init_clicked)

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

    def _init_directory(self):
        '''返回可用于快捷初始化的目录；没有可初始化的目录时返回 None。

        只在文档已保存（有绝对路径）时提供：未命名文档没有所属目录，
        git init 出来的仓库也无处安放。'''
        if self.document is None:
            return None
        filename = self.document.get_filename()
        if filename is None:
            return None
        directory = os.path.dirname(os.path.realpath(filename))
        if not directory or not os.path.isdir(directory):
            return None
        return directory

    def update_view(self):
        enabled = self._panel_enabled()
        has_repo = self.repo is not None
        state = self.repo.state if has_repo else None
        # 仓库不存在但有可初始化的目录 → 显示快捷初始化入口。
        init_directory = None
        if enabled and not has_repo:
            init_directory = self._init_directory()
        visible = enabled and (has_repo or init_directory is not None)
        if self.group is not None:
            self.group.set_visible(visible)
        if not visible:
            return

        if not has_repo:
            self.view.show_init()
            return
        # 刚连上仓库时状态还在后台线程里跑：留白等 state_changed，别把
        # 「尚未取到状态」误当成「没有仓库」而闪回初始化页。
        if state is None:
            return
        self.view.show_repository()

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
            self.view.label_commit_date.set_text(
                format_commit_date(state['commit_timestamp']))
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

        # 推送只在「配了远程 + 有东西可推」时可用：本地领先远程，或还没建立
        # upstream（首次推送会自动 --set-upstream）。纯本地项目或已有 upstream
        # 且不领先 → 灰掉并说明原因，避免点了只得到 "Everything up-to-date"。
        has_remote = bool(state['remotes'])
        has_upstream = state['upstream'] is not None
        self.view.button_push.set_sensitive(
            has_remote and bool(state['branch'])
            and (bool(state['ahead']) or not has_upstream))
        self.view.button_push.set_tooltip_text(
            _('Push commits to the remote') if has_remote
            else _('No remote repository is set up for this project.'))

        # 拉取需要 upstream：没有它 git pull 只会吐 "no tracking information"。
        # 先把按钮灰掉，并指向「先推送一次」这条唯一出路。
        self.view.button_pull.set_sensitive(has_upstream)
        self.view.button_pull.set_tooltip_text(
            _('Update this branch from the remote (fast-forward only)')
            if has_upstream
            else _('Push this branch once to set its upstream, then you can pull.'))

        # 线性历史
        self.view.populate_history(state['history'])

    # —— 快捷初始化（文档所在目录还不是仓库）————————————————

    def on_init_clicked(self, button):
        if not self._panel_enabled() or self.repo is not None:
            return
        directory = self._init_directory()
        if directory is None:
            return
        # 首次写操作前的显式确认：git init 会在该目录写下 .git，选错目录
        # （比如家目录）代价很高，所以把完整路径摆出来让用户看一眼。
        dialog = Adw.AlertDialog()
        dialog.set_heading(_('Initialize Git Repository?'))
        dialog.set_body(_('A new repository will be created in:\n\n%s') % directory)
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('init', _('Initialize'))
        dialog.set_response_appearance('init', Adw.ResponseAppearance.SUGGESTED)
        # Enter/Esc 默认走 Cancel：git init 是有后果的写操作，默认响应须安全侧。
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_init_confirmed, directory)
        dialog.present(ServiceLocator.get_main_window())

    def on_init_confirmed(self, dialog, response, directory):
        if response != 'init':
            return
        self.view.button_init.set_sensitive(False)
        GitRepository.init_repository(directory, self.on_init_done)

    def on_init_done(self, error, repo):
        self.view.button_init.set_sensitive(True)
        if error:
            self._toast(error)
            return
        # 初始化成功不弹 toast：面板随即切到仓库视图，状态变化本身即确认。
        if repo is not None:
            # 刚建好的仓库由本应用创建，不可能有预置 hooks，直接记为已信任，
            # 免得紧接着的第一次提交又弹一次信任确认。
            self._trust_repository(repo.root)
            # 结构变化（从「无仓库」到「有仓库」）：重走 set_document 完成
            # 信号连接与视图刷新，避免在这里重复实现一遍绑定逻辑。
            self.repo = None
            self.set_document()

    def _toast(self, message):
        '''非模态结果反馈统一走主窗口 toast（ui-guidelines）。'''
        if not message:
            return
        try:
            main_window = ServiceLocator.get_main_window()
            if main_window is not None and hasattr(main_window, 'toast_overlay'):
                main_window.toast_overlay.add_toast(Adw.Toast.new(message))
        except Exception:
            pass  # toast 失败不影响主流程

    # —— 写操作（pull / commit / push），按仓库显式信任 ——————————

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
        # Enter/Esc 默认走 Cancel：信任仓库会放行 hooks 执行，默认须安全侧。
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.connect('response', self.on_trust_response, callback)
        dialog.present(ServiceLocator.get_main_window())

    def on_trust_response(self, dialog, response, callback):
        if response != 'trust':
            return
        self._trust_repository(self.repo.root)
        callback()

    def _trust_repository(self, root):
        '''把仓库根路径写入信任列表（幂等）。'''
        if root is None:
            return
        trusted = list(self.settings.get_value('preferences', 'git_trusted_repos'))
        if root not in trusted:
            trusted.append(root)
            self.settings.set_value('preferences', 'git_trusted_repos', trusted)

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

    def on_push_clicked(self, button):
        if not self._panel_enabled() or self.repo is None or self.repo.state is None:
            return
        if not self._is_trusted():
            self._request_trust(self.do_push)
            return
        self.do_push()

    def do_push(self):
        state = self.repo.state
        if state['branch'] is None:
            self.view.show_error(_('Nothing to push: this branch has no commits yet.'))
            return
        self.view.show_error(None)
        self.view.button_push.set_sensitive(False)
        self.repo.push(self.on_push_done)

    def on_push_done(self, error):
        if error:
            # 失败时 update_view 里重算的敏感度可能仍为 True，这里只需先恢复。
            self.view.show_error(error)
            self.view.button_push.set_sensitive(True)
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

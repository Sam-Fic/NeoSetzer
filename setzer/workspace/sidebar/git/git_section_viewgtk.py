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


import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Pango


def format_commit_date(timestamp):
    '''把 git 的 unix 时间戳格式化为本地时间；缺失时返回空串。'''
    if not timestamp:
        return ''
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(timestamp))


class GitSectionView(Gtk.Box):
    '''Git 侧栏面板视图（#443）。仿 document_stats：纯标签 + 按钮行
    + 线性历史列表，紧凑、无嵌套滚动。

    两种模式共用一层 Gtk.Stack（无过渡动画）：
    - 'repository'：分支/ahead-behind、最近提交、改动数、Pull/Commit/Push、历史；
    - 'init'：文档所在目录还不是仓库时，用 Adw.StatusPage 给出「初始化仓库」入口。

    历史列表与侧栏其它分区（Files / Structure / To-Dos）同款组件：
    Gtk.ListBox(boxed-list + compact-rows) 里装 Adw.ActionRow；仓库还没有提交时
    同样换成 Adw.StatusPage 空状态（与 StructureWidget 的 empty_state 一致）。'''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(6)

        self.content_stack = Gtk.Stack()
        self.append(self.content_stack)

        self.repo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_stack.add_named(self.repo_box, 'repository')
        self._build_repo_box()

        self.init_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_stack.add_named(self.init_box, 'init')
        self._build_init_box()

        self.show_repository()

    # —— 构造 ——————————————————————————————————————————————

    def _build_repo_box(self):
        # 分支行：分支名 + ahead/behind（↑N ↓M）
        self.branch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.repo_box.append(self.branch_box)

        self.label_branch = Gtk.Label()
        self.label_branch.set_xalign(0)
        self.label_branch.set_ellipsize(Pango.EllipsizeMode.END)
        self.branch_box.append(self.label_branch)

        self.label_ahead_behind = Gtk.Label()
        self.label_ahead_behind.set_xalign(1)
        self.label_ahead_behind.set_hexpand(True)
        self.label_ahead_behind.add_css_class('dim-label')
        self.label_ahead_behind.add_css_class('caption')
        self.branch_box.append(self.label_ahead_behind)

        # 最近一次提交：subject + 日期
        self.label_commit = Gtk.Label()
        self.label_commit.set_wrap(True)
        self.label_commit.set_xalign(0)
        self.repo_box.append(self.label_commit)

        self.label_commit_date = Gtk.Label()
        self.label_commit_date.set_xalign(0)
        self.label_commit_date.add_css_class('dim-label')
        self.label_commit_date.add_css_class('caption')
        self.repo_box.append(self.label_commit_date)

        # 工作区改动概要
        self.label_changed = Gtk.Label()
        self.label_changed.set_wrap(True)
        self.label_changed.set_xalign(0)
        self.label_changed.set_margin_top(4)
        self.repo_box.append(self.label_changed)

        # 错误/结果提示（pull 失败、push 无凭据等），默认隐藏
        self.label_error = Gtk.Label()
        self.label_error.set_wrap(True)
        self.label_error.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_error.set_xalign(0)
        self.label_error.add_css_class('dim-label')
        self.label_error.add_css_class('caption')
        self.label_error.set_visible(False)
        self.repo_box.append(self.label_error)

        # 操作按钮行：三个按钮等宽（hexpand），提交为主操作带 suggested-action。
        # 提交与推送刻意分开：纯本地项目（或没配远程）只需要提交。
        self.button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.button_box.set_margin_top(4)
        self.repo_box.append(self.button_box)

        self.button_pull = Gtk.Button(label=_('Pull'))
        self.button_pull.set_hexpand(True)
        self.button_pull.set_tooltip_text(_('Update this branch from the remote (fast-forward only)'))
        self.button_box.append(self.button_pull)

        self.button_commit = Gtk.Button(label=_('Commit'))
        self.button_commit.set_hexpand(True)
        self.button_commit.add_css_class('suggested-action')
        self.button_commit.set_tooltip_text(_('Commit selected changes'))
        self.button_box.append(self.button_commit)

        self.button_push = Gtk.Button(label=_('Push'))
        self.button_push.set_hexpand(True)
        self.button_push.set_tooltip_text(_('Push commits to the remote'))
        self.button_box.append(self.button_push)

        # 线性历史（`git log` 顺序，新 → 旧）。
        self.label_history = Gtk.Label(label=_('Recent commits'))
        self.label_history.set_xalign(0)
        self.label_history.add_css_class('dim-label')
        self.label_history.add_css_class('caption')
        self.label_history.set_margin_top(6)
        self.label_history.set_visible(False)
        self.repo_box.append(self.label_history)

        self.history_list = Gtk.ListBox()
        self.history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.history_list.add_css_class('boxed-list')
        self.history_list.add_css_class('compact-rows')
        self.history_list.set_visible(False)
        self.repo_box.append(self.history_list)

        # 仓库还没有任何提交：用空状态占位（与其它侧栏分区的空状态同款，
        # 但更矮，避免 220px 的最小高度盖过上面的分支信息）。
        self.history_empty = self._make_status_page(
            'document-open-recent-symbolic',
            _('No commits yet'),
            _('Commit your changes to start building history.'))
        self.history_empty.add_css_class('git-history-empty')
        self.history_empty.set_visible(False)
        self.repo_box.append(self.history_empty)

    def _build_init_box(self):
        self.init_status = self._make_status_page(
            'xsi-git-symbolic',
            _('Not a Git Repository'),
            _('Track changes and history for this folder with Git.'))
        self.button_init = Gtk.Button(label=_('Initialize Repository'))
        self.button_init.add_css_class('suggested-action')
        self.button_init.set_halign(Gtk.Align.CENTER)
        self.init_status.set_child(self.button_init)
        self.init_box.append(self.init_status)

    @staticmethod
    def _make_status_page(icon_name, title, description=None):
        '''与侧栏其它空状态同款（.compact + .sidebar-empty-state）。
        Adw.StatusPage 内部包了一层 ScrolledWindow，默认不传播自然高度，
        分配高度偏小时会自己先滚动；打开 propagate-natural-height 让它按
        内容高度申请尺寸。'''
        status = Adw.StatusPage()
        status.set_icon_name(icon_name)
        status.set_title(title)
        if description is not None:
            status.set_description(description)
        status.add_css_class('compact')
        status.add_css_class('sidebar-empty-state')
        child = status.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.ScrolledWindow):
                child.set_propagate_natural_height(True)
                break
            child = child.get_next_sibling()
        return status

    # —— 模式切换 ——————————————————————————————————————————

    def show_repository(self):
        self.content_stack.set_visible_child_name('repository')

    def show_init(self):
        self.content_stack.set_visible_child_name('init')

    # —— 填充 ——————————————————————————————————————————————

    def show_error(self, text):
        if text:
            self.label_error.set_text(text)
            self.label_error.set_visible(True)
        else:
            self.label_error.set_visible(False)

    def populate_history(self, commits):
        '''按仓库历史重建列表行。commits 为空（新仓库）时换成空状态占位。'''
        commits = commits or []
        self.history_list.remove_all()
        has_commits = len(commits) > 0
        self.label_history.set_visible(has_commits)
        self.history_list.set_visible(has_commits)
        self.history_empty.set_visible(not has_commits)
        if not has_commits:
            return
        for commit in commits:
            row = Adw.ActionRow()
            row.set_title(commit['subject'] or _('(no commit message)'))
            row.set_subtitle(self._format_history_subtitle(commit))
            # 行宽有限，标题/副标题都会被省略；hover 给出完整信息。
            row.set_tooltip_text('%s\n%s\n%s' % (
                commit['subject'] or '',
                commit['author'] or '',
                format_commit_date(commit['timestamp'])))
            self.history_list.append(row)

    def _format_history_subtitle(self, commit):
        parts = [commit['hash']]
        if commit['author']:
            parts.append(commit['author'])
        date_str = format_commit_date(commit['timestamp'])
        if date_str:
            parts.append(date_str)
        return ' · '.join(parts)

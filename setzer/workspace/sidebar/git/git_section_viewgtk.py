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


# 文件状态 XY → 展示文案（原 commit_dialog 弹窗移入；弹窗废弃后由文件行
# subtitle 使用）。
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


class GitSectionView(Gtk.Box):
    '''Git 侧栏面板视图（#443）。VSCode Source Control 式排版：
    Changes / Commits 两档内容（档位切换在 GitPageView 工具栏，驱动
    repo_view_stack），Changes 档 = 分支行 + 面板内提交区 + 改动文件列表，
    Commits 档 = 线性历史。untracked / init 等整页状态见 content_stack。

    状态字母徽标着色规则（.git-status-badge，style_gtk.css）：M/D 红、A/U 绿。'''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        # 吃掉父容器分给的页内剩余高度，空状态才能垂直居中（见 vexpand 链路
        # 注释与 git_page_viewgtk 的模块 docstring）。内容子页仍从顶部排。
        self.set_vexpand(True)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)
        self.append(self.content_stack)

        self.repo_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_stack.add_named(self.repo_box, 'repository')
        self._build_repo_box()

        self.init_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_stack.add_named(self.init_box, 'init')
        self._build_init_box()
        # 同 no_document_status：先撑满（vexpand）再居中（valign）。
        self.init_status.set_vexpand(True)
        self.init_status.set_valign(Gtk.Align.CENTER)

        # 整页状态：无已保存文档 / Git 整体不可用（独立面板页后不再靠
        # 隐藏分区表达「不适用」，改为页内空状态说明原因）。
        # vexpand + valign CENTER：GtkBox 只把额外空间分给 expand 的子控件
        # （非 expand 的子控件只占自然高度、贴在顶部），所以两个都要设——
        # 撑满后靠 valign 在剩余空间里居中。
        self.no_document_status = self._make_status_page(
            'document-open-symbolic',
            _('No Document'),
            _('Open a saved document to see its Git status.'))
        self.no_document_status.set_vexpand(True)
        self.no_document_status.set_valign(Gtk.Align.CENTER)
        self.content_stack.add_named(self.no_document_status, 'no-document')

        self.unavailable_status = self._make_status_page(
            'action-unavailable-symbolic', _('Git Unavailable'))
        self.unavailable_status.set_vexpand(True)
        self.unavailable_status.set_valign(Gtk.Align.CENTER)
        self.content_stack.add_named(self.unavailable_status, 'unavailable')

        self.show_repository()

    # —— 构造 ——————————————————————————————————————————————

    def _build_repo_box(self):
        # Changes / Commits 两档内容（档位切换由 GitPageView 工具栏驱动）。
        self.repo_view_stack = Gtk.Stack()
        self.repo_view_stack.set_vexpand(True)
        self.repo_view_stack.set_interpolate_size(False)
        self.repo_box.append(self.repo_view_stack)

        self._build_changes_view()
        self._build_commits_view()
        self.repo_view_stack.add_named(self.changes_box, 'changes')
        self.repo_view_stack.add_named(self.commits_box, 'commits')

    def _build_changes_view(self):
        self.changes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # 分支行：分支名 + ahead/behind 徽标 + Pull/Push 图标按钮。
        # Pull/Push 是低频动作，降为 .flat 图标按钮，不与 Commit 争视觉权重。
        self.branch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.changes_box.append(self.branch_box)

        self.label_branch = Gtk.Label()
        self.label_branch.set_xalign(0)
        self.label_branch.set_ellipsize(Pango.EllipsizeMode.END)
        self.branch_box.append(self.label_branch)

        self.label_ahead_behind = Gtk.Label()
        self.label_ahead_behind.set_xalign(0)
        self.label_ahead_behind.set_margin_start(2)
        self.label_ahead_behind.add_css_class('dim-label')
        self.label_ahead_behind.add_css_class('caption')
        self.branch_box.append(self.label_ahead_behind)

        # 中间弹性空隙把两个图标按钮推到行尾。
        self.branch_spacer = Gtk.Box()
        self.branch_spacer.set_hexpand(True)
        self.branch_box.append(self.branch_spacer)

        self.button_pull = Gtk.Button()
        self.button_pull.add_css_class('flat')
        # 图标：应用自带 base-down/up-arrow-symbolic（用户提供），构建时随
        # gresource 打包；文件尚未入库时按钮显示空白图标，不报错。
        self.button_pull.set_icon_name('base-down-arrow-symbolic')
        self.button_pull.set_tooltip_text(_('Update this branch from the remote (fast-forward only)'))
        self.button_pull.update_property(
            [Gtk.AccessibleProperty.LABEL],
            [_('Update this branch from the remote (fast-forward only)')])
        self.branch_box.append(self.button_pull)

        self.button_push = Gtk.Button()
        self.button_push.add_css_class('flat')
        self.button_push.set_icon_name('base-up-arrow-symbolic')
        self.button_push.set_tooltip_text(_('Push commits to the remote'))
        self.button_push.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Push commits to the remote')])
        self.branch_box.append(self.button_push)

        # Commit message 输入区。TextView（VSCode 式多行），placeholder 用
        # overlay 内的 dim Label（TextView 无 placeholder 属性）。
        self.message_overlay = Gtk.Overlay()
        self.message_text = Gtk.TextView()
        self.message_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.message_text.set_size_request(-1, 72)
        self.message_text.set_left_margin(8)
        self.message_text.set_right_margin(8)
        self.message_text.set_top_margin(6)
        self.message_text.set_bottom_margin(6)
        self.message_text.set_accepts_tab(False)
        self.message_text.add_css_class('git-commit-message')
        self.message_overlay.set_child(self.message_text)

        self.message_placeholder = Gtk.Label(label=_('Commit message'))
        self.message_placeholder.set_xalign(0)
        self.message_placeholder.set_valign(Gtk.Align.START)
        self.message_placeholder.set_margin_top(6)
        # 9px 光学例外：TextView 文本左内边距 8px + 1px 边框，占位文字
        # 与输入文本左缘对齐。
        self.message_placeholder.set_margin_start(9)
        self.message_placeholder.add_css_class('dim-label')
        self.message_overlay.add_overlay(self.message_placeholder)
        self.changes_box.append(self.message_overlay)

        # Commit 主按钮：占满整行，suggested-action。
        self.button_commit = Gtk.Button(label=_('Commit'))
        self.button_commit.add_css_class('suggested-action')
        self.button_commit.set_tooltip_text(_('Commit the checked files (Ctrl+Enter)'))
        self.changes_box.append(self.button_commit)

        # 错误/结果提示（pull 失败、push 无凭据等），默认隐藏。
        self.label_error = Gtk.Label()
        self.label_error.set_wrap(True)
        self.label_error.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_error.set_xalign(0)
        self.label_error.add_css_class('dim-label')
        self.label_error.add_css_class('caption')
        self.label_error.set_visible(False)
        self.changes_box.append(self.label_error)

        # 改动文件列表：行 = 勾选框 + 状态字母徽标 + 文件名/目录 + Discard。
        # 列表是面板里唯一会无限变长的区域，必须套 ScrolledWindow：此前直接
        # 塞进 Gtk.Box，文件一多整段内容就超出面板被裁掉且滚不动。
        # vexpand 让滚动区吃掉剩余高度（缩短窗口时列表先被压缩滚动，而不是
        # 把 Commit 按钮顶出面板）；未溢出时 ListBox 仍按内容高度画卡片、
        # 下方留白由 viewport 提供，观感与改动前一致。
        self.files_list = Gtk.ListBox()
        self.files_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.files_list.add_css_class('boxed-list')
        # viewport 会把 child 拉伸到视口高度，而 boxed-list 的背景画在
        # ListBox 上：不限制的话文件很少时卡片也会一路延伸到面板底部。
        # 顶部对齐让它只占内容高度，缺少的部分由透明的 viewport 留白。
        self.files_list.set_valign(Gtk.Align.START)
        self.files_list.set_visible(False)

        self.files_scroller = Gtk.ScrolledWindow()
        self.files_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.files_scroller.set_vexpand(True)
        # 圆角卡片蒙版见 .list-card-scroll：滚动时行不会被方角糊出卡片圆角。
        self.files_scroller.set_overflow(Gtk.Overflow.HIDDEN)
        self.files_scroller.add_css_class('list-card-scroll')
        self.files_scroller.set_child(self.files_list)
        self.files_scroller.set_visible(False)
        self.changes_box.append(self.files_scroller)

        # 工作区干净：不用单行灰字，改为与 Document Structure 空状态同款的
        # Adw.StatusPage（.compact + .sidebar-empty-state：48px 图标 /
        # 0.95em 标题）。vexpand 吃掉改动列表原本占的高度，内容在剩余空间
        # 里垂直居中；高度不够时内部 ScrolledWindow 已开
        # propagate-natural-height，不会自己滚起来。
        self.clean_status = self._make_status_page(
            'object-select-symbolic', _('Working tree clean'))
        self.clean_status.set_vexpand(True)
        self.clean_status.set_visible(False)
        self.changes_box.append(self.clean_status)

    def _build_commits_view(self):
        # 线性历史（`git log` 顺序，新 → 旧）。组件沿用原布局。
        self.commits_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.history_list = Gtk.ListBox()
        self.history_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.history_list.add_css_class('boxed-list')
        self.history_list.add_css_class('compact-rows')
        # 同 files_list：别让卡片被 viewport 拉满（见 _build_changes_view）。
        self.history_list.set_valign(Gtk.Align.START)

        # 历史同样可能很长（git log 无上限），滚动容器写法与改动文件列表一致。
        self.history_scroller = Gtk.ScrolledWindow()
        self.history_scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.history_scroller.set_vexpand(True)
        self.history_scroller.set_overflow(Gtk.Overflow.HIDDEN)
        self.history_scroller.add_css_class('list-card-scroll')
        self.history_scroller.set_child(self.history_list)
        self.commits_box.append(self.history_scroller)

        # 仓库还没有任何提交：空状态占位。规格与 Document Structure 页的空
        # 状态完全一致（.compact + .sidebar-empty-state：48px 图标 / 220px
        # 最小高度）。vexpand + valign CENTER 让它在滚动区原本占的整块空间里
        # 垂直居中，而不是贴在顶端。
        self.history_empty = self._make_status_page(
            'document-open-recent-symbolic',
            _('No commits yet'),
            _('Commit your changes to start building history.'))
        self.history_empty.set_vexpand(True)
        self.history_empty.set_valign(Gtk.Align.CENTER)
        self.history_empty.set_visible(False)
        self.commits_box.append(self.history_empty)

    def _build_init_box(self):
        self.init_status = self._make_status_page(
            'xsi-git-symbolic',
            _('Not a Git Repository'),
            _('Track changes and history for this folder with Git.'))
        self.init_status.set_vexpand(True)
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

    def show_no_document(self):
        self.content_stack.set_visible_child_name('no-document')

    def show_unavailable(self, description):
        '''Git 整体不可用（偏好关 / git 未安装）：显示原因说明。'''
        self.unavailable_status.set_description(description)
        self.content_stack.set_visible_child_name('unavailable')

    def set_repo_view(self, name):
        '''Changes / Commits 档位切换（GitPageView 工具栏驱动）。'''
        if name in ('changes', 'commits'):
            self.repo_view_stack.set_visible_child_name(name)

    # —— Changes 档填充 ——————————————————————————————

    def set_message_placeholder_visible(self, visible):
        self.message_placeholder.set_visible(visible)

    def show_error(self, text):
        if text:
            self.label_error.set_text(text)
            self.label_error.set_visible(True)
        else:
            self.label_error.set_visible(False)

    def clear_file_rows(self):
        self.files_list.remove_all()
        self.files_list.set_visible(False)
        self.files_scroller.set_visible(False)
        self.clean_status.set_visible(False)

    def append_file_row(self, row):
        self.files_list.append(row)
        self.files_list.set_visible(True)
        self.files_scroller.set_visible(True)
        self.clean_status.set_visible(False)

    def set_clean(self):
        self.files_list.set_visible(False)
        self.files_scroller.set_visible(False)
        self.clean_status.set_visible(True)

    def build_file_row(self, entry):
        '''构造一个改动文件行：prefix 勾选框 + 状态字母徽标，
        title=文件名、subtitle=相对目录 + 状态文案，suffix「查看文件」+「Discard」
        按钮（diff 为行本体点击，无需按钮）。信号由控制器接（勾选 → 提交范围；
        activate → diff；open → 编辑器打开；discard → 确认流程）。
        返回 (row, check_button, open_button, discard_button)。'''
        status = entry['status']
        status_label = STATUS_LABELS.get(status, status)
        dirname = os.path.dirname(entry['path'])

        row = Adw.ActionRow()
        row.set_title(os.path.basename(entry['path']))
        subtitle = ' · '.join(part for part in (dirname, status_label) if part)
        row.set_subtitle(subtitle or status_label)
        # 点击行本体 → 查看文本 diff。AdwPreferencesRow.activatable 默认是
        # False（与直觉相反），不显式置 True 的话整行点击没有任何反应。
        row.set_activatable(True)

        check = Gtk.CheckButton()
        check.set_valign(Gtk.Align.CENTER)
        row.add_prefix(check)

        badge = Gtk.Label(label=status.strip() or '?')
        badge.set_valign(Gtk.Align.CENTER)
        badge.add_css_class('git-status-badge')
        badge.add_css_class('caption-heading')
        # 着色：新增/未跟踪绿，删除红，改动橙红。首字母判定足够（MM 等组合
        # 以未暂存侧为准）。
        if status.strip().startswith(('A', 'U', '?')):
            badge.add_css_class('success')
        elif status.strip().startswith('D'):
            badge.add_css_class('error')
        else:
            badge.add_css_class('warning')
        row.add_prefix(badge)

        open_button = Gtk.Button()
        open_button.add_css_class('flat')
        open_button.set_icon_name('file-code-symbolic')
        open_button.set_tooltip_text(_('View file'))
        open_button.set_valign(Gtk.Align.CENTER)
        row.add_suffix(open_button)

        discard = Gtk.Button()
        discard.add_css_class('flat')
        discard.set_icon_name('edit-delete-symbolic')
        discard.set_tooltip_text(_('Discard changes in this file'))
        discard.set_valign(Gtk.Align.CENTER)
        row.add_suffix(discard)

        return row, check, open_button, discard

    # —— Commits 档填充 ——————————————————————————————

    def populate_history(self, commits):
        '''按仓库历史重建列表行。commits 为空（新仓库）时换成空状态占位。'''
        commits = commits or []
        self.history_list.remove_all()
        has_commits = len(commits) > 0
        self.history_list.set_visible(has_commits)
        self.history_scroller.set_visible(has_commits)
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

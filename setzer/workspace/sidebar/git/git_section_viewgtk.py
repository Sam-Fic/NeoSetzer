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
from gi.repository import Gtk, Pango


class GitSectionView(Gtk.Box):
    '''Git 侧栏面板视图（#443）。仿 document_stats：纯标签 + 按钮行，
    紧凑、无嵌套滚动。'''

    def __init__(self):
        Gtk.Box.__init__(self)
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_spacing(6)

        # 分支行：分支名 + ahead/behind（↑N ↓M）
        self.branch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.append(self.branch_box)

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

        # 最近一次提交：subject + 相对日期
        self.label_commit = Gtk.Label()
        self.label_commit.set_wrap(True)
        self.label_commit.set_xalign(0)
        self.append(self.label_commit)

        self.label_commit_date = Gtk.Label()
        self.label_commit_date.set_xalign(0)
        self.label_commit_date.add_css_class('dim-label')
        self.label_commit_date.add_css_class('caption')
        self.append(self.label_commit_date)

        # 工作区改动概要
        self.label_changed = Gtk.Label()
        self.label_changed.set_wrap(True)
        self.label_changed.set_xalign(0)
        self.label_changed.set_margin_top(4)
        self.append(self.label_changed)

        # 错误/结果提示（pull 失败、push 无凭据等），默认隐藏
        self.label_error = Gtk.Label()
        self.label_error.set_wrap(True)
        self.label_error.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.label_error.set_xalign(0)
        self.label_error.add_css_class('dim-label')
        self.label_error.add_css_class('caption')
        self.label_error.set_visible(False)
        self.append(self.label_error)

        # 操作按钮行：两个按钮等宽（hexpand），主操作带 suggested-action
        self.button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.button_box.set_margin_top(4)
        self.append(self.button_box)

        self.button_pull = Gtk.Button(label=_('Pull'))
        self.button_pull.set_hexpand(True)
        self.button_pull.set_tooltip_text(_('Update this branch from the remote (fast-forward only)'))
        self.button_box.append(self.button_pull)

        self.button_commit = Gtk.Button(label=_('Commit && Push'))
        self.button_commit.set_hexpand(True)
        self.button_commit.add_css_class('suggested-action')
        self.button_commit.set_tooltip_text(_('Commit selected changes and push to the remote'))
        self.button_box.append(self.button_commit)

    def show_error(self, text):
        if text:
            self.label_error.set_text(text)
            self.label_error.set_visible(True)
        else:
            self.label_error.set_visible(False)

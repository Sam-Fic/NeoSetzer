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

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio

from setzer.app.service_locator import ServiceLocator
from setzer.keyboard_shortcuts.shortcut_controller import ShortcutController


def register_global_accels(settings=None):
    '''注册 LaTeX 文档级命令的全局加速器（win.insert-before-after /
    win.insert-symbol 的带参动作）。

    这些注册面向的是窗口级动作表，与具体文档无关、值也只随用户键位偏好
    变化。此前在 ShortcutControllerLaTeX.__init__（即每个新建/打开的
    LaTeX 文档各一次）执行，实测每次注册阻塞 20-90ms、16 个合计约
    0.6-1s，且会话恢复 N 个文档就重复 N 遍——是「新建文档 / 打开文件 /
    恢复会话卡顿数秒」的主要来源。现改为应用启动时调用一次；键位设置
    变化时由 Shortcuts 重新调用，即时全局生效。
    '''
    if settings is None:
        settings = ServiceLocator.get_settings()
    shortcuts = settings.get_value('keyboard_shortcuts', None)
    if shortcuts is None:
        shortcuts = settings.defaults['keyboard_shortcuts']

    app = ServiceLocator.get_main_window().app

    def before_after(parameter, accel):
        app.set_accels_for_action(
            Gio.Action.print_detailed_name('win.insert-before-after', GLib.Variant('as', parameter)),
            [accel])

    def insert_symbol(parameter, accel):
        app.set_accels_for_action(
            Gio.Action.print_detailed_name('win.insert-symbol', GLib.Variant('as', parameter)),
            [accel])

    before_after(['\\textbf{', '}'], shortcuts.get('bold', '<Control>b'))
    before_after(['\\textit{', '}'], shortcuts.get('italic', '<Control>i'))
    before_after(['\\underline{', '}'], shortcuts.get('underline', '<Control>u'))
    before_after(['\\texttt{', '}'], shortcuts.get('typewriter', '<Control><Shift>y'))
    before_after(['\\emph{', '}'], shortcuts.get('emphasized', '<Control><Shift>e'))
    before_after(['$ ', ' $'], shortcuts.get('inline_math', '<Control>m'))
    before_after(['\\[ ', ' \\]'], shortcuts.get('display_math', '<Control><Shift>m'))
    before_after(['\\begin{equation}\n\t', '\n\\end{equation}'], shortcuts.get('equation', '<Control><Shift>n'))
    before_after(['\\begin{•}\n\t', '\n\\end{•}'], shortcuts.get('environment', '<Control>e'))
    before_after(['_{', '}'], shortcuts.get('subscript', '<Control><Shift>d'))
    before_after(['^{', '}'], shortcuts.get('superscript', '<Control><Shift>u'))
    insert_symbol(['\\frac{•}{•}'], shortcuts.get('fraction', '<Alt><Shift>f'))
    insert_symbol(['\\left •'], shortcuts.get('left', '<Alt><Shift>l'))
    insert_symbol(['\\right •'], shortcuts.get('right', '<Alt><Shift>r'))
    insert_symbol(['\\item •'], shortcuts.get('list_item', '<Control><Shift>i'))
    insert_symbol(['\\\\\n'], shortcuts.get('new_line', '<Control>Return'))


class ShortcutControllerLaTeX(ShortcutController):

    def __init__(self):
        ShortcutController.__init__(self)

        self.main_window = ServiceLocator.get_main_window()
        self.workspace = ServiceLocator.get_workspace()
        self.actions = self.workspace.actions
        self.settings = ServiceLocator.get_settings()

        self.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        self.load_shortcuts()

    def load_shortcuts(self):
        # 带参动作的加速器（textbf/italic/... 共 16 个）是窗口级全局注册，
        # 已由 register_global_accels 在应用启动时一次性完成，不再随每个
        # 文档重复注册。这里只保留作用于本视图的回调型快捷键。
        shortcuts = self.settings.get_value('keyboard_shortcuts', None)
        if shortcuts is None:
            shortcuts = self.settings.defaults['keyboard_shortcuts']

        # fallback 与 settings.py 默认值 '<Control>slash' 保持一致：settings 始终提供
        # 该值，fallback 实际不会命中，但保持两边写法一致以避免混淆。
        self.create_and_add_shortcut(shortcuts.get('toggle_comment', '<Control>slash'), self.actions.toggle_comment)
        self.create_and_add_shortcut(shortcuts.get('quotation_marks', '<Control>quotedbl'), self.shortcut_quotes)

    def shortcut_quotes(self, accel_group=None, window=None, key=None, mask=None):
        self.main_window.shortcutsbar.quotes_button.activate()



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
gi.require_version('Adw', '1')
from gi.repository import Adw


class InterpreterMissingDialog(object):

    def __init__(self, main_window, preferences_dialog):
        self.main_window = main_window
        self.preferences_dialog = preferences_dialog

    def run(self, interpreter_name):
        self.setup(interpreter_name)
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, interpreter_name):
        # latexmk 在主流发行版上是独立包（texlive-latex-base 等集合不包含它），
        # 缺 latexmk 时给出针对性的安装提示与关闭入口，而非误导用户装引擎。
        if interpreter_name == 'latexmk':
            body = _('''Setzer is configured to run builds through "latexmk", which seems to be missing on this system.

You can install it with:
• Ubuntu/Debian: sudo apt install latexmk
• Fedora: sudo dnf install latexmk
• Arch Linux: sudo pacman -S latexmk

Or turn off latexmk in Preferences ▸ Build System.''')
        else:
            body = _('''Setzer is configured to use "{interpreter}" which seems to be missing on this system.

You can install it with:
• Ubuntu/Debian: sudo apt install texlive-latex-base
• Fedora: sudo dnf install texlive-scheme-medium
• Arch Linux: sudo pacman -S texlive-core

Or choose a different interpreter in Preferences.''').format(interpreter=interpreter_name)
        # 两个分支共用同一组响应：无 add_response 的 AlertDialog 一个按钮都
        # 没有，close response 也未注册，模态弹出后无法跳偏好或可靠关闭。
        self.view = Adw.AlertDialog(
            heading=_('LaTeX Interpreter is missing.'),
            body=body)
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('preferences', _('Go to Preferences'))
        self.view.set_response_appearance('preferences', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('preferences')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'preferences':
            self.preferences_dialog.run()

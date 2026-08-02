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


class ReplaceConfirmationDialog(object):

    def __init__(self, main_window):
        self.main_window = main_window
        self.search_context = None
        self.replacement = None
        # 选区模式自定义替换回调：非 None 时 dialog_process_response 调用它而非
        # 默认的 search_context.replace_all。每次 run() 重新赋值，None 表示走
        # 默认全 buffer 替换——避免单例复用导致上次的回调残留误触发。
        self.on_confirm = None

    def run(self, original, replacement, number_of_occurrences, search_context, on_confirm=None):
        self.search_context = search_context
        self.replacement = replacement
        self.on_confirm = on_confirm
        self.setup(original, replacement, number_of_occurrences)
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, original, replacement, number_of_occurrences):
        if number_of_occurrences < 0:
            # GtkSource 异步扫描尚未完成（-1）：告知用户正在计算匹配数，
            # 而非静默降级为不含数字的通用提示——用户知道为什么没有具体数字。
            heading = _('Replace all matches of "{original}" with "{replacement}"?').format(original=original, replacement=replacement)
            body = _('Counting matches… The number of occurrences is still being calculated.')
        else:
            str_occurrences = ngettext('Replacing {amount} occurence of "{original}" with "{replacement}".', 'Replacing {amount} occurrences of "{original}" with "{replacement}".', number_of_occurrences)
            heading = str_occurrences.format(amount=str(number_of_occurrences), original=original, replacement=replacement)
            body = _('Do you really want to do this?')
        self.view = Adw.AlertDialog(
            heading=heading,
            body=body)
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('replace', _('Yes, replace all occurrences'))
        self.view.set_response_appearance('replace', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('replace')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'replace':
            # 选区模式传 on_confirm 走自定义替换（选区范围逐个替换）；
            # None 则走默认全 buffer replace_all（一次性 C 调用）。
            if self.on_confirm is not None:
                self.on_confirm()
            else:
                self.search_context.replace_all(self.replacement, -1)

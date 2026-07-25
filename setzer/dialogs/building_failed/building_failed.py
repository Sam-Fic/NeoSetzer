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

import urllib.parse

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Adw, Gtk, Gdk, Gio

from setzer.app.service_locator import ServiceLocator


class BuildingFailedDialog(object):

    def __init__(self, main_window, preferences_dialog):
        self.main_window = main_window
        self.preferences_dialog = preferences_dialog
        self.error_message = None

    def run(self, error_message):
        self.error_message = error_message
        self.setup(error_message)
        self.view.choose(self.main_window, None, self.dialog_process_response)

    def setup(self, error_message):
        self.view = Adw.AlertDialog(
            heading=_('Something went wrong.'),
            body=_('''The build process ended unexpectedly returning "{error_message}".

To configure your build system go to Preferences.''').format(error_message=error_message))
        # 响应顺序：Copy / Search / Cancel / Preferences（按用户确认的设计：
        # 仅补两个小按钮，不解析错误行号、不本地化消息）。Copy 与 Search 关闭
        # 对话框后执行对应操作；用户若想接着去 Preferences 需重开——这与"只补两
        # 个小按钮"的轻量需求一致，避免对话框变为多步操作面板。
        self.view.add_response('copy', _('Copy Error'))
        self.view.add_response('search', _('Search Online'))
        self.view.add_response('cancel', _('Cancel'))
        self.view.add_response('preferences', _('Go to Preferences'))
        self.view.set_response_appearance('preferences', Adw.ResponseAppearance.SUGGESTED)
        self.view.set_default_response('preferences')
        self.view.set_close_response('cancel')

    def dialog_process_response(self, dialog, result):
        response_id = dialog.choose_finish(result)
        if response_id == 'copy':
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(self.error_message or '')
            self._show_toast(_('Error message copied to clipboard'))
        elif response_id == 'search':
            # 用 Google 搜索错误消息（URL 编码避免特殊字符破坏 URL）。
            # 与预览面板打开 PDF 模式一致：Gio.AppInfo.launch_default_for_uri
            # 用系统默认处理器（HTTP → 默认浏览器）。
            query = urllib.parse.quote(self.error_message or '')
            url = 'https://www.google.com/search?q=' + query
            Gio.AppInfo.launch_default_for_uri(url, None)
        elif response_id == 'preferences':
            self.preferences_dialog.run()

    def _show_toast(self, text):
        main_window = ServiceLocator.get_main_window()
        if hasattr(main_window, 'toast_overlay'):
            toast = Adw.Toast.new(text)
            toast.set_timeout(3)
            main_window.toast_overlay.add_toast(toast)

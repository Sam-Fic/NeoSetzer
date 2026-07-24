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

import gi
gi.require_versions({'Gtk': '4.0', 'WebKit': '6.0'})
from gi.repository import WebKit, Gtk

import os.path
import pickle
import html
import re

from setzer.helpers.observable import Observable
import setzer.workspace.help_panel.help_panel_controller as help_panel_controller
import setzer.workspace.help_panel.help_panel_presenter as help_panel_presenter
from setzer.app.service_locator import ServiceLocator
from setzer.app.color_manager import ColorManager


class HelpPanel(Observable):

    def __init__(self, workspace):
        Observable.__init__(self)

        self.workspace = workspace
        self.view = ServiceLocator.get_main_window().help_panel

        self.path = 'file://' + os.path.join(ServiceLocator.get_resources_path(), 'help')
        self.home_uri = self.path + '/latex2e_0.html'
        self.current_uri = self.home_uri

        self.search_index = None
        # 懒加载搜索索引：原实现在 workspace 构造（应用启动早期）同步
        # open + pickle.load 读取 search_index.pickle。该索引仅用于帮助面板搜索，
        # 若用户从不打开帮助面板（常见场景），这次 I/O + 反序列化（数千到上万项）
        # 完全是浪费，却推后了主窗口可交互时间。改为记录路径，首次搜索时才加载。
        self._search_index_path = os.path.join(ServiceLocator.get_resources_path(), 'help', 'search_index.pickle')
        self.search_results_blank = list()
        self.search_results = self.search_results_blank
        self.query = ''

        self.controller = help_panel_controller.HelpPanelController(self, self.view)
        self.presenter = help_panel_presenter.HelpPanelPresenter(self, self.view)

        self.add_change_code('search_query_changed')

        self.update_colors()

    def _ensure_search_index(self):
        if self.search_index is None:
            with open(self._search_index_path, 'rb') as filehandle:
                self.search_index = pickle.load(filehandle)
        return self.search_index

    def set_uri(self, uri):
        self.current_uri = uri
        self.add_change_code('uri_changed', uri)

    def set_uri_by_search_item(self, uri_ending, text, location):
        self.current_uri = self.path + '/' + uri_ending

        self.search_results_blank = [item for item in self.search_results_blank if (item[0] != uri_ending or item[1] != text or item[2] != location)]
        self.search_results_blank.append([uri_ending, text, location])

        if len(self.search_results_blank) > 8:
            self.search_results_blank.pop()

        self.add_change_code('uri_changed', self.current_uri)

    def _highlight(self, text, words_lower):
        # 单次扫描替代原实现的多遍 str.replace：
        #   1. html.unescape 解码 HTML 实体（原 4 次 replace，且顺序脆弱）
        #   2. 大小写不敏感正则一次性插入 \x00/\x01 高亮标记（原每词 3 次 replace）
        #   3. html.escape 重新转义（原 6 次 replace）
        #   4. 标记转 <b></b>
        # \x00/\x01 是不可能出现在帮助文本中的控制字符，避免与原文冲突。
        text = html.unescape(text)
        if words_lower:
            pattern = re.compile('|'.join(re.escape(w) for w in words_lower), re.IGNORECASE)
            text = pattern.sub(lambda m: '\x00' + m.group(0) + '\x01', text)
        text = html.escape(text)
        text = text.replace('\x00', '<b>').replace('\x01', '</b>')
        return text

    def set_search_query(self, query):
        self.query = query
        if query == '':
            self.search_results = self.search_results_blank
        else:
            words = query.split()
            # 预小写化查询词：原实现循环内每次 item[0].find(word.lower()) 都重新
            # .lower()，且 item[0] 未预小写导致大小写敏感漏匹配。此处统一小写比较。
            words_lower = [w.lower() for w in words]
            self.search_results = list()
            index = self._ensure_search_index()
            for item in index:
                if len(self.search_results) == 8: break

                found = True
                for word_lower in words_lower:
                    if item[0].find(word_lower) == -1:
                        found = False
                        break
                if found:
                    headline = self._highlight(item[2], words_lower)
                    location = self._highlight(item[3], words_lower)
                    self.search_results.append([item[1], headline, location])
        self.add_change_code('search_query_changed')

    def update_colors(self):
        css = '''body {margin: 1em; margin-top: 0px; padding-top: 1px; background: @view_bg_color; color: @view_fg_color; }
a {color: @link_color; }
a:visited {color: @link_color_visited; }
a:active {color: @link_color_active; }
a.external:after {text-decoration: underline; text-decoration-color: @view_bg_color; content: ' 🡭'; }'''
        css = css.replace('@view_bg_color', ColorManager.get_ui_color_string('view_bg_color'))
        css = css.replace('@view_fg_color', ColorManager.get_ui_color_string('view_fg_color'))
        css = css.replace('@link_color_visited', ColorManager.get_ui_color_string('link_color_visited'))
        css = css.replace('@link_color_active', ColorManager.get_ui_color_string('link_color_active'))
        css = css.replace('@link_color', ColorManager.get_ui_color_string('link_color'))

        style_sheet = WebKit.UserStyleSheet.new(css, WebKit.UserContentInjectedFrames.ALL_FRAMES, WebKit.UserStyleLevel.USER, None, None)

        self.view.user_content_manager.add_style_sheet(style_sheet)



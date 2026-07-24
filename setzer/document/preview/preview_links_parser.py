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
gi.require_version('Poppler', '0.18')
from gi.repository import Poppler

from setzer.helpers.observable import Observable
from setzer.helpers.timer import timer


class PreviewLinksParser(Observable):

    def __init__(self, preview):
        Observable.__init__(self)
        self.preview = preview
        self.links = dict()

        self.preview.connect('pdf_changed', self.on_pdf_changed)

    def on_pdf_changed(self, notifying_object):
        if self.preview.poppler_document != None:
            self.links = dict()
            for page_num in range(self.preview.poppler_document.get_n_pages()):
                self.links[page_num] = None
        else:
            self.links = dict()

    def get_links_for_page(self, page_number):
        if page_number in self.links:
            if self.links[page_number] == None:
                links = list()
                # GTK4 配套的新版 Poppler 中 get_link_mappings()（复数，返回列表）
                # 已更名为 get_link_mapping()（单数）。其返回类型在不同版本间
                # 有差异（单个 LinkMapping / None / 旧版列表），统一规整成
                # 列表再遍历，避免 AttributeError 与类型假设错误。
                page = self.preview.poppler_document.get_page(page_number)
                result = page.get_link_mapping()
                if result is None:
                    link_mapping_list = list()
                elif isinstance(result, list):
                    link_mapping_list = result
                else:
                    link_mapping_list = [result]
                for link_mapping in link_mapping_list:
                    action = link_mapping.action
                    area = link_mapping.area
                    if action.type == Poppler.ActionType.URI:
                        links.append([area, action.uri.uri, 'uri'])
                    elif action.type == Poppler.ActionType.GOTO_DEST:
                        # find_dest 对未知 named_dest 返回 None。若存入 None，
                        # 点击链接时 scroll_dest_on_screen(None) 会 AttributeError 崩溃。
                        # 跳过此类无效目标链接。
                        try:
                            dest = self.preview.poppler_document.find_dest(action.goto_dest.dest.named_dest)
                        except Exception:
                            dest = None
                        if dest is not None:
                            links.append([area, dest, 'goto'])
                self.links[page_number] = links
            return self.links[page_number]
        else:
            return list()



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

import re

from setzer.app.service_locator import ServiceLocator
from setzer.helpers.observable import Observable
from setzer.helpers.timer import timer


# parse_symbols 在每次按键（insert/delete）时都 finditer 全文。原实现每次
# 都经 ServiceLocator.get_regex_object 做哈希查表；模块级预编译后直接持有。
_BIBITEM_REGEX = re.compile(r'@(\w+)\{(\w+)')


class ParserBibTeX(Observable):

    def __init__(self, document):
        Observable.__init__(self)
        self.document = document
        self.text = ''

        self.symbols = dict()
        self.symbols['bibitems'] = set()
        self.symbols['labels'] = set()
        self.symbols['labels_with_offset'] = list()
        self.symbols['todos'] = set()
        self.symbols['todos_with_offset'] = set()
        self.symbols['included_latex_files'] = set()
        self.symbols['bibliographies'] = set()
        self.symbols['packages'] = set()
        self.symbols['packages_detailed'] = dict()
        self.symbols['blocks'] = list()

        self.document.source_buffer.connect('insert-text', self.on_text_inserted)
        self.document.source_buffer.connect('delete-range', self.on_text_deleted)

    #@timer
    def on_text_deleted(self, buffer, start_iter, end_iter):
        start_offset = start_iter.get_offset()
        end_offset = end_iter.get_offset()
        self.text = self.text[:start_offset] + self.text[end_offset:]
        self.parse_symbols(self.text)

    #@timer
    def on_text_inserted(self, buffer, location_iter, text, text_length):
        offset = location_iter.get_offset()
        self.text = self.text[:offset] + text + self.text[offset:]
        self.parse_symbols(self.text)

    #@timer
    def parse_symbols(self, text):
        bibitems = set()
        for match in _BIBITEM_REGEX.finditer(text):
            bibitems.add(match.group(2).strip())

        self.symbols['bibitems'] = bibitems



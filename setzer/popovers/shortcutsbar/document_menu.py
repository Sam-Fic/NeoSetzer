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
gi.require_version('Gtk', '4.0')
from gi.repository import GLib

from setzer.popovers.helpers.gio_menu_builder import GioMenuBuilder


class DocumentMenu(GioMenuBuilder):

    def __init__(self, popover_manager=None):
        GioMenuBuilder.__init__(self)

        self.add_insert_symbol_item('main', '\\documentclass', ['\\documentclass[•]{•}'])
        self.add_action_button('main', _('Add / Remove Packages') + '...', 'win.add-remove-packages-dialog')
        self.add_menu_button(_('Document Info'), 'document_info')
        self.add_separator()
        self.add_before_after_item('main', _('Document Environment'), ['\\begin{document}\n\t', '\n\\end{document}'])
        self.add_insert_symbol_item('main', _('Show Title') + ' (\\maketitle)', ['\\maketitle'])
        self.add_insert_symbol_item('main', _('Table of Contents'), ['\\tableofcontents'])
        self.add_separator()
        self.add_action_button('main', _('Include LaTeX File') + ' (\\input)...', 'win.include-latex-file')

        # 每文档 LaTeX 解释器覆盖子菜单（对应 actions.on_set_build_interpreter）。
        self.add_page('interpreter-submenu', _('Build Interpreter'))
        interpreters = [(_('Use Global Default'), 'default'),
                        ('PDFLaTeX', 'pdflatex'),
                        ('XeLaTeX', 'xelatex'),
                        ('LuaLaTeX', 'lualatex'),
                        ('Tectonic', 'tectonic')]
        for label, value in interpreters:
            self.add_action_button('interpreter-submenu', label,
                                   'win.set-build-interpreter', GLib.Variant('s', value))
        self.add_menu_button(_('Build Interpreter'), 'interpreter-submenu')

        # document info submenu
        self.add_page('document_info', _('Document Info'))
        self.add_insert_symbol_item('document_info', _('Author'), ['\\author{•}'])
        self.add_insert_symbol_item('document_info', _('Title'), ['\\title{•}'])
        self.add_insert_symbol_item('document_info', _('Date'), ['\\date{•}'])
        self.add_insert_symbol_item('document_info', _('Date Today'), ['\\date{\\today}'])

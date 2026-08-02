#!/usr/bin/env python3
# coding: utf-8

# Copyright (C) 2017-present Robert Griesel
# Copyright (C) 2026 Sam-Fic
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
from gi.repository import Gio


class NewDocumentView(object):
    '''Produces a Gio.Menu model consumed by Gtk.MenuButton.set_menu_model().

    This is the standard libadwaita approach — identical to the hamburger menu.
    The native Gtk.PopoverMenu handles styling, keyboard navigation, and
    automatic popdown on action activation.
    '''

    def __init__(self):
        self.model = Gio.Menu()
        self.model.append(_('New LaTeX Document'), 'win.new-latex-document')
        self.model.append(_('New BibTeX Document'), 'win.new-bibtex-document')

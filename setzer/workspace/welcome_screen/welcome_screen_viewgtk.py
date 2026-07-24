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
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk


def WelcomeScreenView():
    '''Welcome screen shown when no document is open.

    Built as a scrollable column so it works on small windows:
      - an Adw.StatusPage (icon + title + friendly hint)
      - a width-limited (Adw.Clamp) region with:
          * quick-action buttons (New LaTeX / New BibTeX / Template Wizard)
          * a recent-documents list (Adw.ActionRow per file)

    Adw.StatusPage is final and cannot be subclassed, so the function
    returns a Gtk.ScrolledWindow. The dynamic widgets the presenter needs
    to drive (recent list, buttons, empty-state label) are attached as
    Python attributes on the returned widget for easy access.

    All _() calls happen inside this function body, never at import time,
    because gettext is installed only after application activation.
    '''
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_hexpand(True)
    scrolled.set_propagate_natural_height(True)

    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    column.set_margin_top(24)
    column.set_margin_bottom(24)
    column.set_margin_start(24)
    column.set_margin_end(24)
    scrolled.set_child(column)

    # --- top: status page (icon + title + hint) ---
    status = Adw.StatusPage()
    status.set_icon_name('document-latex-symbolic')
    status.set_title(_('Write beautiful LaTeX documents with ease!'))
    status.set_description(_('Start a new document below, pick a template, '
                            'or jump back into one of your recent files.'))
    status.set_vexpand(False)
    column.append(status)

    # --- width-limited content ---
    clamp = Adw.Clamp()
    clamp.set_maximum_size(520)
    clamp.set_tightening_threshold(400)
    column.append(clamp)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

    # quick actions heading
    actions_heading = Gtk.Label(label=_('Create a new document'))
    actions_heading.set_halign(Gtk.Align.START)
    actions_heading.add_css_class('title-4')
    content.append(actions_heading)

    # quick-action buttons
    actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    actions_box.set_homogeneous(True)

    new_latex_button = Gtk.Button()
    new_latex_button.set_icon_name('document-new')
    new_latex_button.set_label(_('New LaTeX Document'))
    new_latex_button.set_hexpand(True)

    new_bibtex_button = Gtk.Button()
    new_bibtex_button.set_icon_name('document-new')
    new_bibtex_button.set_label(_('New BibTeX File'))
    new_bibtex_button.set_hexpand(True)

    wizard_button = Gtk.Button()
    wizard_button.set_icon_name('preferences-other')
    wizard_button.set_label(_('Use a Template…'))
    wizard_button.set_hexpand(True)

    actions_box.append(new_latex_button)
    actions_box.append(new_bibtex_button)
    actions_box.append(wizard_button)
    content.append(actions_box)

    # recent documents heading
    recent_heading = Gtk.Label(label=_('Recent documents'))
    recent_heading.set_halign(Gtk.Align.START)
    recent_heading.add_css_class('title-4')
    content.append(recent_heading)

    recent_listbox = Gtk.ListBox()
    recent_listbox.add_css_class('boxed-list')
    recent_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    content.append(recent_listbox)

    # shown only when there are no recent documents
    empty_label = Gtk.Label(label=_('No recent documents yet.'))
    empty_label.add_css_class('dim-label')
    empty_label.set_halign(Gtk.Align.START)
    content.append(empty_label)

    clamp.set_child(content)

    # expose dynamic widgets to the presenter
    scrolled.recent_listbox = recent_listbox
    scrolled.empty_label = empty_label
    scrolled.new_latex_button = new_latex_button
    scrolled.new_bibtex_button = new_bibtex_button
    scrolled.wizard_button = wizard_button

    return scrolled

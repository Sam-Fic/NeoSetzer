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
from gi.repository import Gtk, Gio, GLib

from setzer.app.font_manager import FontManager
from setzer.app.latex_templates import WRAP_COMMANDS, WRAP_ENVIRONMENTS, INSERT_TEMPLATES


def _action_item(label, detailed_action, accel=None):
    '''A Gio.MenuItem for an action, optionally with a parseable accel.

    The ``accel`` attribute is rendered natively by Gtk.PopoverMenu as the
    shortcut label on the right (e.g. ``<Control>z`` -> "Ctrl+Z").'''
    item = Gio.MenuItem.new(label, detailed_action)
    if accel is not None:
        item.set_attribute_value('accel', GLib.Variant('s', accel))
    return item


def _wrap_item(label, before, after):
    '''A Gio.MenuItem that invokes ``win.insert-before-after`` with a [before, after]
    parameter. When a range is selected the wrapped text becomes the payload;
    otherwise the ``•`` placeholder is left for the cursor (the calling action
    handles both cases).

    ``label`` is localised here (at menu-build time) because the template data
    stores raw English strings to avoid calling _() at module import time.'''
    item = Gio.MenuItem.new(_(label), 'win.insert-before-after')
    item.set_action_and_target_value('win.insert-before-after',
                                     GLib.Variant('as', [before, after]))
    return item


def _insert_item(label, template):
    '''A Gio.MenuItem that invokes ``win.insert-symbol`` with a template string.

    ``label`` is localised here (at menu-build time) — see _wrap_item for why.'''
    item = Gio.MenuItem.new(_(label), 'win.insert-symbol')
    item.set_action_and_target_value('win.insert-symbol',
                                     GLib.Variant('as', [template]))
    return item


def _build_submenu_from_list(items, builder_fn):
    '''Build a Gio.Menu submenu from a list of template dicts.

    Each dict is passed to ``builder_fn`` which returns a Gio.MenuItem.
    Returns the populated Gio.Menu.'''
    submenu = Gio.Menu()
    for entry in items:
        submenu.append_item(builder_fn(entry))
    return submenu


class ContextMenuView(Gtk.PopoverMenu):
    '''Shortcutsbar "more" popover (the F12 context menu).

    Built from a ``Gio.Menu`` model on a native ``Gtk.PopoverMenu`` — the same
    form as the hamburger menu — instead of the former hand-built
    ListBox-in-popover. Action items use real GAction targets and parseable
    accelerators. The LaTeX/BibTeX items (Toggle Comment / Show in Preview) live
    in a section rebuilt on active-document changes. The Zoom controls are a
    custom child widget (so the buttons can trigger actions without closing
    the popover and the reset label can be updated dynamically).
    '''

    def __init__(self, popover_manager):
        Gtk.PopoverMenu.__init__(self)
        self.set_size_request(288, -1)

        self.latex_section = Gio.Menu()
        # 暴露 model 引用供共享：编辑器右键菜单（workspace ContextMenu.popover_pointer）
        # 用同一份 Gio.Menu model，使两者样式与内容完全一致（LaTeX section 重建对两者同效）。
        self.model = self._build_model()
        self.set_menu_model(self.model)
        self.add_child(self._build_zoom_widget(), 'zoom-controls')
        self.connect('map', self.on_map)

    def _build_model(self):
        model = Gio.Menu()

        section_edit = Gio.Menu()
        section_edit.append_item(_action_item(_('Undo'), 'win.undo', '<Control>z'))
        section_edit.append_item(_action_item(_('Redo'), 'win.redo', '<Control><Shift>z'))
        model.append_section(None, section_edit)

        section_clip = Gio.Menu()
        section_clip.append_item(_action_item(_('Cut'), 'win.cut', '<Control>x'))
        section_clip.append_item(_action_item(_('Copy'), 'win.copy', '<Control>c'))
        section_clip.append_item(_action_item(_('Paste'), 'win.paste', '<Control>v'))
        section_clip.append_item(_action_item(_('Delete'), 'win.delete-selection'))
        model.append_section(None, section_clip)

        section_select = Gio.Menu()
        section_select.append_item(_action_item(_('Select All'), 'win.select-all', '<Control>a'))
        model.append_section(None, section_select)

        # 行操作：快捷键已存在（Alt+Up/Down、Alt+Shift+D、Tab/Shift+Tab），
        # 此前仅藏在线下，放进菜单提升可发现性。非 LaTeX 专属，对任何文档可用。
        section_lines = Gio.Menu()
        section_lines.append_item(_action_item(_('Duplicate Line'), 'win.duplicate-line', '<Alt><Shift>d'))
        section_lines.append_item(_action_item(_('Move Line Up'), 'win.move-line-up', '<Alt>Up'))
        section_lines.append_item(_action_item(_('Move Line Down'), 'win.move-line-down', '<Alt>Down'))
        section_lines.append_item(_action_item(_('Indent'), 'win.indent', '<Tab>'))
        section_lines.append_item(_action_item(_('Unindent'), 'win.outdent', '<Shift>Tab'))
        model.append_section(None, section_lines)

        # LaTeX/BibTeX section: rebuilt via rebuild_latex_section().
        model.append_section(None, self.latex_section)

        # Zoom controls as a custom child row.
        zoom_section = Gio.Menu()
        zoom_item = Gio.MenuItem()
        zoom_item.set_attribute_value('custom', GLib.Variant('s', 'zoom-controls'))
        zoom_section.append_item(zoom_item)
        model.append_section(None, zoom_section)

        return model

    def _build_zoom_widget(self):
        box = Gtk.CenterBox()
        box.set_orientation(Gtk.Orientation.HORIZONTAL)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        zoom_label = Gtk.Label(label=_('Zoom'))
        box.set_start_widget(zoom_label)

        inner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        button_zoom_out = Gtk.Button()
        button_zoom_out.set_icon_name('value-decrease-symbolic')
        button_zoom_out.add_css_class('flat')
        button_zoom_out.set_action_name('win.zoom-out')
        inner_box.append(button_zoom_out)

        self.reset_zoom_button = Gtk.Button.new_with_label("{:.0%}".format(FontManager.zoom_level))
        self.reset_zoom_button.add_css_class('flat')
        self.reset_zoom_button.set_action_name('win.reset-zoom')
        inner_box.append(self.reset_zoom_button)

        button_zoom_in = Gtk.Button()
        button_zoom_in.set_icon_name('value-increase-symbolic')
        button_zoom_in.add_css_class('flat')
        button_zoom_in.set_action_name('win.zoom-in')
        inner_box.append(button_zoom_in)

        box.set_end_widget(inner_box)
        return box

    def rebuild_latex_section(self, document):
        '''Populate (or clear) the LaTeX/BibTeX section for the active document.

        "Toggle Comment" is shown for any document (LaTeX, BibTeX, etc.),
        while "Show in Preview" and the LaTeX-specific sub-menus are limited
        to LaTeX documents.  The three sub-menus (Wrap in Command / Wrap in
        Environment / Insert Template) provide quick access to common LaTeX
        constructs via the existing ``insert-before-after`` and ``insert-symbol``
        G-actions (registered in ``Actions``) — no new action plumbing needed.
        '''
        self.latex_section.remove_all()
        if document is None:
            return

        self.latex_section.append_item(
            _action_item(_('Toggle Comment'), 'win.toggle-comment', '<Control>slash'))

        if document.is_latex_document():
            # -- Wrap in Command sub-menu ---------------------------------------
            wrap_cmds_submenu = _build_submenu_from_list(
                WRAP_COMMANDS,
                lambda e: _wrap_item(e['label'], e['before'], e['after']))
            self.latex_section.append_submenu(
                _('Wrap in Command'), wrap_cmds_submenu)

            # -- Wrap in Environment sub-menu ----------------------------------
            wrap_envs_submenu = _build_submenu_from_list(
                WRAP_ENVIRONMENTS,
                lambda e: _wrap_item(e['label'], e['before'], e['after']))
            self.latex_section.append_submenu(
                _('Wrap in Environment'), wrap_envs_submenu)

            # -- Insert Template sub-menu --------------------------------------
            insert_submenu = _build_submenu_from_list(
                INSERT_TEMPLATES,
                lambda e: _insert_item(e['label'], e['template']))
            self.latex_section.append_submenu(
                _('Insert Template'), insert_submenu)

            # -- Show in Preview ------------------------------------------------
            self.latex_section.append_item(
                _action_item(_('Show in Preview'), 'win.forward-sync', 'F7'))

    def on_map(self, popover):
        popover.grab_focus()
